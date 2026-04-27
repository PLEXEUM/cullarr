import asyncio
import json
from datetime import datetime, timedelta
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.utils.logger import get_logger
from app.core.run_state import _active_run

logger = get_logger()


async def acquire_run_lock(run_type: str) -> bool:
    """Acquire lock for a run. Returns True if acquired, False if already running."""
    conn = get_connection()
    try:
        state = conn.execute("SELECT is_running, started_at FROM run_state WHERE id = 1").fetchone()

        if state and state["is_running"]:
            if state["started_at"]:
                started = datetime.fromisoformat(state["started_at"])
                if datetime.now() - started > timedelta(hours=2):
                    logger.warning("Run lock timed out, forcing release")
                    conn.execute(
                        "UPDATE run_state SET is_running = 0, run_type = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
                    )
                    conn.commit()
                else:
                    logger.info(f"Run already in progress ({state['run_type']}), waiting...")
                    return False

        conn.execute(
            "UPDATE run_state SET is_running = 1, run_type = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (run_type,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


async def release_run_lock():
    """Release the run lock."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE run_state SET is_running = 0, run_type = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
        )
        conn.commit()
    finally:
        conn.close()


async def _apply_plex_collections(
    plex_client: PlexClient,
    collection_name: str,
    movies: list,
    library_map: dict
) -> None:
    """
    Add movies to a Plex collection.
    Handles both individual movies and collection groups.
    Uses title + year to find the Plex rating key.
    """
    # Flatten collections into individual movies
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)

    if not flat_movies:
        return

    # Get or create the collection once - pass first movie's rating key if available
    first_rating_key = None
    if flat_movies:
        first_movie = flat_movies[0]
        title = first_movie.get("movie_title")
        year = first_movie.get("movie_year")
        if title and year:
            key = f"{title.lower()}|{year}"
            first_rating_key = library_map.get(key)
    
    collection_key = await plex_client.get_or_create_collection(collection_name, first_rating_key)
    if not collection_key:
        logger.error(f"Failed to get or create Plex collection: {collection_name}")
        return

    # Add each movie to the collection
    for movie in flat_movies:
        title = movie.get("movie_title")
        year = movie.get("movie_year")
        
        if not title or not year:
            logger.debug(f"Missing title or year for movie, skipping Plex collection")
            continue
        
        key = f"{title.lower()}|{year}"
        rating_key = library_map.get(key)
        
        if not rating_key:
            logger.debug(f"No Plex rating key found for '{title} ({year})'")
            continue

        logger.info(f"DEBUG: About to add rating_key={rating_key} to collection_key={collection_key}")
        success = await plex_client.add_to_collection(collection_key, rating_key)
        if success:
            logger.info(f"Added '{title}' to Plex collection '{collection_name}'")
        else:
            logger.warning(f"Failed to add '{title}' to Plex collection")


async def _remove_plex_collections(
    plex_client: PlexClient,
    collection_name: str,
    movies: list,
    library_map: dict
) -> None:
    """
    Remove movies from a Plex collection.
    Handles both individual movies and collection groups.
    Uses title + year to find the Plex rating key.
    """
    # Flatten collections into individual movies
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)

    if not flat_movies:
        return

    # Get the collection key (don't create if doesn't exist)
    collection_key = await plex_client.get_or_create_collection(collection_name)
    if not collection_key:
        # Collection doesn't exist, nothing to remove
        return

    for movie in flat_movies:
        title = movie.get("movie_title")
        year = movie.get("movie_year")
        
        if not title or not year:
            continue
        
        key = f"{title.lower()}|{year}"
        rating_key = library_map.get(key)
        
        if not rating_key:
            continue

        success = await plex_client.remove_from_collection(collection_key, rating_key)
        if success:
            logger.info(f"Removed '{title}' from Plex collection '{collection_name}'")
        else:
            logger.warning(f"Failed to remove '{title}' from Plex collection")


async def _build_plex_library_map(plex_client: PlexClient) -> dict:
    library_items = await plex_client.get_library_items()
    library_map = {}
    for item in library_items:
        title = item.get("title")
        year = item.get("year")
        rating_key = item.get("rating_key")
        if title and year and rating_key:
            key = f"{title.lower()}|{year}"
            library_map[key] = rating_key
    logger.info(f"Built Plex library map with {len(library_map)} items (by title|year)")
    return library_map


def _queue_entries_for_movie(movie: dict, scheduled_date: str) -> list:
    """
    Build a list of DB insert tuples for a scored movie entry.
    For collections, returns one tuple per movie in the collection.
    For individual movies, returns a single tuple.
    Each collection member shares the same scheduled_date and collection score.
    """
    entries = []

    if movie.get("is_collection"):
        for member in movie.get("movies", []):
            entries.append((
                member["movie_id"],
                member["movie_title"],
                member["movie_year"],
                member.get("tmdb_id"),
                member["tmdb_rating"],
                member["size_gb"],
                member["quality"],
                1 if member["monitored"] else 0,
                movie["normalized_score"],  # use collection score for all members
                json.dumps(member["factors"]),
                scheduled_date,
                movie.get("collection_title", "Unknown Collection"),
            ))
    else:
        entries.append((
            movie["movie_id"],
            movie["movie_title"],
            movie["movie_year"],
            movie.get("tmdb_id"),
            movie["tmdb_rating"],
            movie["size_gb"],
            movie["quality"],
            1 if movie["monitored"] else 0,
            movie["normalized_score"],
            json.dumps(movie["factors"]),
            scheduled_date,
            None,  # no collection name for individual movies
        ))

    return entries


async def run_score_cycle():
    """Score all movies and add top N to scheduled deletions queue."""
    lock_acquired = await acquire_run_lock("score")
    if not lock_acquired:
        logger.info("Score run skipped - another run in progress")
        return

    try:
        conn = get_connection()

        # Load configs
        radarr_config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        plex_config = conn.execute("SELECT * FROM plex_config WHERE id = 1").fetchone()
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()

        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.error("Radarr not configured, cannot run score cycle")
            return

        # Check if Radarr is reachable
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, radarr_msg = await radarr_client.test_connection()
        if not radarr_ok:
            logger.error(f"Radarr connection failed: {radarr_msg}")
            return

        # Get Plex client and data if enabled
        plex_client = None
        plex_play_counts = None
        plex_library_map = {}
        plex_enabled = bool(
            plex_config and plex_config["enabled"] and
            plex_config["url"] and plex_config["api_key"]
        )

        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            plex_ok, plex_msg = await plex_client.test_connection()
            if plex_ok:
                plex_library_map, plex_play_counts = await asyncio.gather(
                    _build_plex_library_map(plex_client),
                    plex_client.get_play_counts_by_tmdb()
                )
                logger.info(f"Fetched Plex data: {len(plex_play_counts)} TMDb play counts")
            
                # ===== PROGRESS UPDATE: Plex data fetched =====
                _active_run["current_movie"] = f"Fetched Plex watch history for {len(plex_play_counts)} movies"
                _active_run["current"] = 20
                # ===== END PROGRESS UPDATE =====
            else:
                logger.warning(f"Plex connection failed: {plex_msg}, continuing without watch data")
                plex_enabled = False

        # Fetch movies from Radarr
        movies = await radarr_client.get_movies()

        # ===== PROGRESS UPDATE: Movies fetched =====
        _active_run["current_movie"] = f"Fetched {len(movies)} movies from Radarr"
        _active_run["current"] = 10
        _active_run["total"] = 100  # Using percentage scale (0-100)
        # ===== END PROGRESS UPDATE =====

        # ===== NEW: Clean up orphaned entries in scheduled_deletions =====
        radarr_movie_ids = {movie["id"] for movie in movies if movie.get("id")}
        if radarr_movie_ids:
            placeholders = ",".join("?" * len(radarr_movie_ids))
            orphaned = conn.execute(
                f"SELECT id, movie_id, movie_title FROM scheduled_deletions WHERE status = 'scheduled' AND movie_id NOT IN ({placeholders})",
                tuple(radarr_movie_ids)
            ).fetchall()
            
            if orphaned:
                conn.execute(
                    f"DELETE FROM scheduled_deletions WHERE status = 'scheduled' AND movie_id NOT IN ({placeholders})",
                    tuple(radarr_movie_ids)
                )
                logger.info(f"Cleaned {len(orphaned)} orphaned entries from scheduled_deletions (movies no longer in Radarr)")
                for orphan in orphaned:
                    logger.debug(f"Orphan removed: {orphan['movie_title']} (ID: {orphan['movie_id']})")
        # ===== END NEW CODE =====

        # Score movies (collection grouping handled inside engine if enabled)
        engine = ScoringEngine(conn)
        
        # ===== PROGRESS UPDATE: Starting scoring =====
        _active_run["current_movie"] = f"Scoring {len(movies)} movies..."
        _active_run["current"] = 30
        # ===== END PROGRESS UPDATE =====
        
        scored_movies = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

        # ===== PROGRESS UPDATE: Scoring complete =====
        _active_run["current_movie"] = f"Scored {len(scored_movies)} entries"
        _active_run["current"] = 60
        # ===== END PROGRESS UPDATE =====
        
        logger.info(f"Scored {len(scored_movies)} entries ({len(movies)} total movies)")

        # ===== WRAP HEAVY DB OPERATIONS IN THREAD POOL =====
        def _process_queue_operations():
            thread_conn = get_connection()
            try:
                # Get current scheduled deletions count
                current_queue = thread_conn.execute(
                    "SELECT COUNT(DISTINCT collection_name) as coll_count, "
                    "COUNT(CASE WHEN collection_name IS NULL THEN 1 END) as single_count "
                    "FROM scheduled_deletions WHERE status = 'scheduled'"
                ).fetchone()

                # Count slots used
                used_slots = (
                    (current_queue["coll_count"] if current_queue else 0) +
                    (current_queue["single_count"] if current_queue else 0)
                )
                max_queued = settings["max_queued"] if settings else 20
                available_slots = max_queued - used_slots

                logger.info(f"Queue status: {used_slots}/{max_queued} slots used, {available_slots} available")

                if available_slots <= 0:
                    logger.info("Queue full, no new movies added")
                    return None

                # Filter already-queued movie IDs
                scheduled_ids = thread_conn.execute(
                    "SELECT movie_id FROM scheduled_deletions"
                ).fetchall()
                scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

                # Get threshold from settings
                threshold = settings["min_score_threshold"] if settings else 0

                # Filter candidates
                candidates = []
                for entry in scored_movies:
                    if entry.get("normalized_score", 0) <= threshold:
                        continue
                        
                    if entry.get("is_collection"):
                        member_ids = {m["movie_id"] for m in entry.get("movies", [])}
                        if not member_ids.intersection(scheduled_id_set):
                            candidates.append(entry)
                    else:
                        if entry["movie_id"] not in scheduled_id_set:
                            candidates.append(entry)

                to_add = candidates[:available_slots]
                return to_add, scheduled_id_set
            finally:
                thread_conn.close()

        # Run DB operations in thread pool to prevent blocking
        result = await asyncio.to_thread(_process_queue_operations)
        
        if result is None:
            # Queue was full
            return
        
        to_add, scheduled_id_set = result

        # ===== NEW: Track current queue IDs before adding new movies =====
        current_queue_ids = set()
        existing_queued = conn.execute(
            "SELECT movie_id FROM scheduled_deletions WHERE status = 'scheduled'"
        ).fetchall()
        for row in existing_queued:
            current_queue_ids.add(row["movie_id"])
        # ===== END TRACK CURRENT QUEUE =====
        
        # Add to scheduled deletions (another DB-heavy operation)
        def _add_to_queue():
            thread_conn = get_connection()
            try:
                added = []
                for movie in to_add:
                    scheduled_date = datetime.now() + timedelta(
                        days=settings["delete_after_days"] if settings else 7
                    )
                    entries = _queue_entries_for_movie(movie, scheduled_date.isoformat())

                    try:
                        for entry in entries:
                            thread_conn.execute(
                                """INSERT OR IGNORE INTO scheduled_deletions
                                (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating, size_gb, quality,
                                 monitored, score, score_factors, scheduled_date, status, collection_name)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)""",
                                entry
                            )

                        label = movie.get("collection_title") or movie["movie_title"]
                        count = len(entries)
                        if movie.get("is_collection"):
                            logger.info(f"Added to queue: {label} (collection: {count} movies)")
                        else:
                            logger.info(f"Added to queue: {label} (score: {movie['normalized_score']:.1f})")
                        added.append(movie)
                    except Exception as e:
                        label = movie.get("collection_title") or movie.get("movie_title")
                        logger.error(f"Failed to add {label} to queue: {e}")
                
                thread_conn.commit()
                return added
            finally:
                thread_conn.close()

        added = await asyncio.to_thread(_add_to_queue)

        # ===== PROGRESS UPDATE: Queue addition complete =====
        _active_run["current_movie"] = f"Added {len(added)} movies to deletion queue"
        _active_run["current"] = 90
        # ===== END PROGRESS UPDATE =====
        # ===== END THREAD POOL WRAPPER =====

        # ===== NEW: Clean up Plex collections for movies that left the queue =====
        # Get the new queue IDs after additions
        new_queue_ids = set()
        updated_queued = conn.execute(
            "SELECT movie_id FROM scheduled_deletions WHERE status = 'scheduled'"
        ).fetchall()
        for row in updated_queued:
            new_queue_ids.add(row["movie_id"])
        
        # Find movies that were removed (in old but not in new)
        removed_movie_ids = current_queue_ids - new_queue_ids
        
        if removed_movie_ids and plex_enabled and plex_client and plex_config["collection_name"]:
            # Get movie details for removed movies (need title and year for mapping)
            placeholders = ",".join("?" * len(removed_movie_ids))
            removed_movies_data = conn.execute(
                f"SELECT movie_id, movie_title, movie_year FROM scored_movies_cache WHERE movie_id IN ({placeholders})",
                tuple(removed_movie_ids)
            ).fetchall()
            
            if removed_movies_data:
                # Convert to list of dicts for _remove_plex_collections
                removed_movies_list = [dict(row) for row in removed_movies_data]
                logger.info(f"Removing {len(removed_movies_list)} movies from Plex collection '{plex_config['collection_name']}'")
                await _remove_plex_collections(
                    plex_client,
                    plex_config["collection_name"],
                    removed_movies_list,
                    plex_library_map
                )
        # ===== END PLEX CLEANUP =====

        # Add newly queued movies to Plex collection
        if plex_enabled and plex_client and plex_config["collection_name"] and added:
            logger.info(f"Adding {len(added)} movies to Plex collection '{plex_config['collection_name']}'")
            await _apply_plex_collections(
                plex_client,
                plex_config["collection_name"],
                added,
                plex_library_map
            )

        # ===== PROGRESS UPDATE: Complete =====
        _active_run["current_movie"] = f"Score cycle complete: added {len(added)} queue entries"
        _active_run["current"] = 100
        # ===== END PROGRESS UPDATE =====

        logger.info(f"Score cycle complete: added {len(added)} queue entries")

    except Exception as e:
        logger.error(f"Score cycle failed: {e}")
    finally:
        conn.close()
        await release_run_lock()


async def run_cull_cycle(dry_run: bool = False):
    """
    Delete movies that have passed their scheduled deletion date.
    If dry_run=True, returns the list of due movies without deleting anything.
    """
    lock_acquired = await acquire_run_lock("cull")
    if not lock_acquired:
        logger.info("Cull run skipped - another run in progress")
        return

    try:
        conn = get_connection()

        # Load configs
        radarr_config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        plex_config = conn.execute("SELECT * FROM plex_config WHERE id = 1").fetchone()

        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.error("Radarr not configured, cannot run cull cycle")
            return

        # Get Plex client if enabled
        plex_client = None
        plex_library_map = {}
        plex_enabled = bool(
            plex_config and plex_config["enabled"] and
            plex_config["url"] and plex_config["api_key"]
        )

        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            plex_ok, _ = await plex_client.test_connection()
            if plex_ok:
                plex_library_map = await _build_plex_library_map(plex_client)
            else:
                logger.warning("Plex connection failed, labels will not be removed")
                plex_enabled = False

        # Get due movies
        now = datetime.now().isoformat()
        due_movies = conn.execute(
            "SELECT * FROM scheduled_deletions WHERE status = 'scheduled' AND scheduled_date <= ?",
            (now,)
        ).fetchall()

        if not due_movies:
            logger.info("No movies due for deletion")
            # Still need to update _active_run for dry run
            if dry_run:
                _active_run["dry_run_results"] = []
                _active_run["current_movie"] = "Dry run complete — no movies due for deletion"
            return

        logger.info(f"Found {len(due_movies)} movies due for deletion")

        # ===== PROGRESS UPDATE: Starting cull =====
        _active_run["total"] = 100
        _active_run["current"] = 0
        _active_run["current_movie"] = f"Preparing to delete {len(due_movies)} movies..."
        # ===== END PROGRESS UPDATE =====
        
        # If dry run, just return the list without deleting
        if dry_run:
            # Convert rows to dict for JSON serialization
            results = [dict(movie) for movie in due_movies]
            _active_run["dry_run_results"] = results
            _active_run["current_movie"] = f"Dry run complete — {len(results)} movies would be deleted"
            logger.info(f"Dry cull run complete: {len(results)} movies would be deleted")
            return

        # Normal deletion logic continues here...
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])

        deleted = 0
        failed = 0

        for movie in due_movies:
            logger.info(f"Deleting: {movie['movie_title']} (scheduled: {movie['scheduled_date']})")

            try:
                result = await radarr_client.delete_movie_entirely(movie["movie_id"])

                if result["success"]:
                    conn.execute(
                        """INSERT INTO deletion_history
                        (movie_id, movie_title, movie_year, size_gb, score, status)
                        VALUES (?, ?, ?, ?, ?, 'deleted')""",
                        (
                            movie["movie_id"],
                            movie["movie_title"],
                            movie["movie_year"],
                            movie["size_gb"],
                            movie["score"]
                        )
                    )
                    conn.execute("DELETE FROM scheduled_deletions WHERE id = ?", (movie["id"],))
                    deleted += 1
                    logger.info(f"Deleted: {movie['movie_title']}")

                    # ===== PROGRESS UPDATE: Movie deleted =====
                    percent_complete = int(((deleted + failed) / len(due_movies)) * 100) if due_movies else 0
                    _active_run["current"] = percent_complete
                    _active_run["current_movie"] = f"Deleted: {movie['movie_title']} ({deleted + failed} of {len(due_movies)})"
                    # ===== END PROGRESS UPDATE =====

                    # Remove from Plex collection after successful deletion
                    if plex_enabled and plex_client and plex_config["collection_name"]:
                        await _remove_plex_collections(
                            plex_client,
                            plex_config["collection_name"],
                            [dict(movie)],
                            plex_library_map
                        )
                else:
                    logger.error(f"Delete failed for {movie['movie_title']}: {result['message']}")
                    conn.execute(
                        """INSERT INTO deletion_history
                        (movie_id, movie_title, movie_year, size_gb, score, status, error_message)
                        VALUES (?, ?, ?, ?, ?, 'failed', ?)""",
                        (
                            movie["movie_id"],
                            movie["movie_title"],
                            movie["movie_year"],
                            movie["size_gb"],
                            movie["score"],
                            result["message"]
                        )
                    )
                    conn.execute("DELETE FROM scheduled_deletions WHERE id = ?", (movie["id"],))
                    failed += 1

                    # ===== PROGRESS UPDATE: Movie failed =====
                    percent_complete = int(((deleted + failed) / len(due_movies)) * 100) if due_movies else 0
                    _active_run["current"] = percent_complete
                    _active_run["current_movie"] = f"Failed: {movie['movie_title']} ({deleted + failed} of {len(due_movies)})"
                    # ===== END PROGRESS UPDATE =====

                conn.commit()
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error deleting {movie['movie_title']}: {e}")
                conn.execute("DELETE FROM scheduled_deletions WHERE id = ?", (movie["id"],))
                failed += 1
                conn.commit()

        logger.info(f"Cull cycle complete: deleted {deleted}, failed {failed}")

        # ===== PROGRESS UPDATE: Complete =====
        _active_run["current_movie"] = f"Cull cycle complete: deleted {deleted}, failed {failed}"
        _active_run["current"] = 100
        # ===== END PROGRESS UPDATE =====

    except Exception as e:
        logger.error(f"Cull cycle failed: {e}")
    finally:
        conn.close()
        await release_run_lock()
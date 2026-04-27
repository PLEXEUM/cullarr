import asyncio
import json
from datetime import datetime, timedelta
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.utils.logger import get_logger

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


async def _apply_plex_labels(
    plex_client: PlexClient,
    label_text: str,
    movies: list,
    library_map: dict
) -> None:
    """
    Add a Plex label to each movie in the list.
    Handles both individual movies and collection groups.
    """
    # Flatten collections into individual movies for label application
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)

    for movie in flat_movies:
        tmdb_id = movie.get("tmdb_id")
        if not tmdb_id:
            logger.debug(f"No TMDb ID for {movie['movie_title']}, skipping Plex label")
            continue

        rating_key = library_map.get(str(tmdb_id))
        if not rating_key:
            logger.debug(f"No Plex rating key found for {movie['movie_title']} (TMDb: {tmdb_id})")
            continue

        success = await plex_client.add_label(rating_key, label_text)
        if success:
            logger.info(f"Added Plex label '{label_text}' to {movie['movie_title']}")
        else:
            logger.warning(f"Failed to add Plex label to {movie['movie_title']}")


async def _remove_plex_labels(
    plex_client: PlexClient,
    label_text: str,
    movies: list,
    library_map: dict
) -> None:
    """
    Remove a Plex label from each movie in the list.
    Handles both individual movies and collection groups.
    """
    # Flatten collections into individual movies for label removal
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)

    for movie in flat_movies:
        tmdb_id = movie.get("tmdb_id") or movie.get("tmdb_id")
        if not tmdb_id:
            continue

        rating_key = library_map.get(str(tmdb_id))
        if not rating_key:
            continue

        success = await plex_client.remove_label(rating_key, label_text)
        if success:
            logger.info(f"Removed Plex label '{label_text}' from {movie['movie_title']}")
        else:
            logger.warning(f"Failed to remove Plex label from {movie['movie_title']}")


async def _build_plex_library_map(plex_client: PlexClient) -> dict:
    """
    Build a single tmdb_id -> rating_key map from the Plex library.
    Called once per cycle to avoid repeated full library scans.
    """
    library_items = await plex_client.get_library_items()
    library_map = {}
    for item in library_items:
        if item.get("tmdb_id"):
            library_map[str(item["tmdb_id"])] = item["rating_key"]
    logger.info(f"Built Plex library map with {len(library_map)} items")
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
            else:
                logger.warning(f"Plex connection failed: {plex_msg}, continuing without watch data")
                plex_enabled = False

        # Fetch movies from Radarr
        movies = await radarr_client.get_movies()

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
        scored_movies = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

        logger.info(f"Scored {len(scored_movies)} entries ({len(movies)} total movies)")

        # ===== WRAP HEAVY DB OPERATIONS IN THREAD POOL =====
        def _process_queue_operations():
            # Get current scheduled deletions count
            current_queue = conn.execute(
                "SELECT COUNT(DISTINCT collection_name) as coll_count, "
                "COUNT(CASE WHEN collection_name IS NULL THEN 1 END) as single_count "
                "FROM scheduled_deletions WHERE status = 'scheduled'"
            ).fetchone()

            # Count slots used: each unique collection = 1 slot, each individual = 1 slot
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
            scheduled_ids = conn.execute(
                "SELECT movie_id FROM scheduled_deletions"
            ).fetchall()
            scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

            # Get threshold from settings
            threshold = settings["min_score_threshold"] if settings else 0

            # Filter out entries where any member is already queued AND apply threshold
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
            added = []
            for movie in to_add:
                scheduled_date = datetime.now() + timedelta(
                    days=settings["delete_after_days"] if settings else 7
                )
                entries = _queue_entries_for_movie(movie, scheduled_date.isoformat())

                try:
                    for entry in entries:
                        conn.execute(
                            """INSERT OR IGNORE INTO scheduled_deletions
                            (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating, size_gb, quality,
                             monitored, score, score_factors, scheduled_date, status, collection_name)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)""",
                            entry
                        )

                    label = movie.get("collection_title") or movie["movie_title"]
                    count = len(entries)
                    logger.info(
                        f"Added to queue: {label} "
                        f"({'collection: ' + str(count) + ' movies' if movie.get('is_collection') else 'score: ' + str(movie['normalized_score']):.1f})"
                    )
                    added.append(movie)
                except Exception as e:
                    label = movie.get("collection_title") or movie.get("movie_title")
                    logger.error(f"Failed to add {label} to queue: {e}")
            
            conn.commit()
            return added

        added = await asyncio.to_thread(_add_to_queue)
        # ===== END THREAD POOL WRAPPER =====

        # ===== NEW: Clean up Plex labels for movies that left the queue =====
        # Get the new queue IDs after additions
        new_queue_ids = set()
        updated_queued = conn.execute(
            "SELECT movie_id FROM scheduled_deletions WHERE status = 'scheduled'"
        ).fetchall()
        for row in updated_queued:
            new_queue_ids.add(row["movie_id"])
        
        # Find movies that were removed (in old but not in new)
        removed_movie_ids = current_queue_ids - new_queue_ids
        
        if removed_movie_ids and plex_enabled and plex_client and plex_config["label_text"]:
            # Get movie details for removed movies
            placeholders = ",".join("?" * len(removed_movie_ids))
            removed_movies_data = conn.execute(
                f"SELECT movie_id, movie_title, tmdb_id FROM scored_movies_cache WHERE movie_id IN ({placeholders})",
                tuple(removed_movie_ids)
            ).fetchall()
            
            if removed_movies_data:
                # Convert to list of dicts for _remove_plex_labels
                removed_movies_list = [dict(row) for row in removed_movies_data]
                logger.info(f"Removing Plex labels for {len(removed_movies_list)} movies that left the queue")
                await _remove_plex_labels(
                    plex_client,
                    plex_config["label_text"],
                    removed_movies_list,
                    plex_library_map
                )
        # ===== END PLEX CLEANUP =====

        # Apply Plex labels to all newly queued movies in one pass
        if plex_enabled and plex_client and plex_config["label_text"] and added:
            logger.info(f"Applying Plex labels to {len(added)} queue entries")
            await _apply_plex_labels(
                plex_client,
                plex_config["label_text"],
                added,
                plex_library_map
            )

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

    # Import the global _active_run from run module to store dry run results
    from app.api.run import _active_run

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

                    # Remove Plex label after successful deletion
                    if plex_enabled and plex_client and plex_config["label_text"]:
                        await _remove_plex_labels(
                            plex_client,
                            plex_config["label_text"],
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

                conn.commit()
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error deleting {movie['movie_title']}: {e}")
                conn.execute("DELETE FROM scheduled_deletions WHERE id = ?", (movie["id"],))
                failed += 1
                conn.commit()

        logger.info(f"Cull cycle complete: deleted {deleted}, failed {failed}")

    except Exception as e:
        logger.error(f"Cull cycle failed: {e}")
    finally:
        conn.close()
        await release_run_lock()
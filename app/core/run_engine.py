import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
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


# In run_engine.py, replace _apply_plex_collections and _remove_plex_collections:
async def _apply_plex_collections(
    plex_client: PlexClient,
    movies: list,
    library_map: dict
) -> Dict[int, str]:  # ← CHANGED: returns dict of movie_id -> rating_key
    """Add movies to Plex collection using Maintainerr-style tag writing."""
    
    rating_key_map = {}  # ← ADDED: store rating keys by movie_id
    
    conn = get_connection()
    try:
        plex_config = conn.execute("SELECT collection_key, url FROM plex_config WHERE id = 1").fetchone()
        collection_key = plex_config["collection_key"] if plex_config else None
        if not collection_key:
            logger.warning("No Plex collection selected")
            return rating_key_map  # ← CHANGED: return empty dict
        
        # Get collection name ONCE, outside the loop
        from plexapi.server import PlexServer
        server = PlexServer(plex_config["url"], plex_client.api_key)
        collection_obj = server.fetchItem(int(collection_key))
        collection_name = collection_obj.title
        logger.info(f"Using Plex collection: '{collection_name}'")

        # Add this debug line (optional, helps troubleshooting)
        logger.debug(f"Will add {len(movies)} movie entries to collection '{collection_name}'")
    
        # ===== ADD DEBUG HERE =====
        logger.info(f"🎯 Target collection name: '{collection_name}'")
        logger.info(f"📊 Total movies to process: {len(movies)}")
        # Flatten to see what we're adding
        flat_debug = []
        for m in movies:
            if m.get("is_collection"):
                flat_debug.extend([sub.get('movie_title') for sub in m.get("movies", [])])
            else:
                flat_debug.append(m.get("movie_title"))
        logger.info(f"🎬 Movies to add to collection: {flat_debug}")
        # ===== END DEBUG =====
    
    finally:
        conn.close()
    
    # Flatten collections into individual movies
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)
    
    for movie in flat_movies:
        title = movie.get("movie_title")
        year = movie.get("movie_year")
        movie_id = movie.get("movie_id")  # ← ADDED: get movie_id
        
        if not title or not year or not movie_id:  # ← CHANGED: check movie_id
            continue
        
        key = f"{title.lower()}|{year}"
        rating_key = library_map.get(key)
        
        if not rating_key:
            logger.debug(f"No Plex rating key for '{title} ({year})'")
            continue
        
        # Use the new sync method with collection NAME (string tag)
        success = await plex_client.sync_collection(
            item_rating_key=rating_key,
            collection_name=collection_name,
            should_be_in=True
        )
        
        if success:
            logger.info(f"Added '{title}' to Plex collection '{collection_name}'")
            rating_key_map[movie_id] = rating_key  # ← ADDED: store rating key
        else:
            logger.warning(f"Failed to add '{title}' to Plex collection")
    
    return rating_key_map  # ← ADDED: return the map


async def _remove_plex_collections(
    plex_client: PlexClient,
    movies: list,
    library_map: dict
) -> None:
    """Remove movies from Plex collection using Maintainerr-style tag removal."""
    
    conn = get_connection()
    try:
        plex_config = conn.execute("SELECT collection_key, url FROM plex_config WHERE id = 1").fetchone()
        collection_key = plex_config["collection_key"] if plex_config else None
        if not collection_key:
            return
        
        from plexapi.server import PlexServer
        server = PlexServer(plex_config["url"], plex_client.api_key)
        collection_obj = server.fetchItem(int(collection_key))
        collection_name = collection_obj.title
    finally:
        conn.close()
    
    flat_movies = []
    for movie in movies:
        if movie.get("is_collection"):
            flat_movies.extend(movie.get("movies", []))
        else:
            flat_movies.append(movie)
    
    for movie in flat_movies:
        title = movie.get("movie_title")
        year = movie.get("movie_year")
        
        if not title or not year:
            continue
        
        key = f"{title.lower()}|{year}"
        rating_key = library_map.get(key)
        
        if not rating_key:
            continue
        
        success = await plex_client.sync_collection(
            item_rating_key=rating_key,
            collection_name=collection_name,
            should_be_in=False
        )
        
        if success:
            logger.info(f"Removed '{title}' from Plex collection '{collection_name}'")


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


async def run_score_cycle():
    """Score all movies and mark top N for deletion based on max_queued setting."""
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
            
                _active_run["current_movie"] = f"Fetched Plex watch history for {len(plex_play_counts)} movies"
                _active_run["current"] = 20
            else:
                logger.warning(f"Plex connection failed: {plex_msg}, continuing without watch data")
                plex_enabled = False

        # Fetch movies from Radarr
        movies = await radarr_client.get_movies()

        _active_run["current_movie"] = f"Fetched {len(movies)} movies from Radarr"
        _active_run["current"] = 10
        _active_run["total"] = 100

        # Score movies
        engine = ScoringEngine(conn)
        
        _active_run["current_movie"] = f"Scoring {len(movies)} movies..."
        _active_run["current"] = 30
        
        scored_movies = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

        _active_run["current_movie"] = f"Scored {len(scored_movies)} entries"
        _active_run["current"] = 60
        
        logger.info(f"Scored {len(scored_movies)} entries ({len(movies)} total movies)")

        # ===== STEP 1: Get existing scheduled movies (for Plex cleanup later) =====
        existing_scheduled = {}
        existing_scheduled_movies = []  # ← ADD THIS
        existing = conn.execute(
            "SELECT movie_id, movie_title, movie_year, scheduled_date FROM scored_movies_cache WHERE scheduled_for_deletion = 1 AND scheduled_date IS NOT NULL"
        ).fetchall()
        for row in existing:
            existing_scheduled[row["movie_id"]] = row["scheduled_date"]
            existing_scheduled_movies.append(dict(row))  # ← ADD THIS
        
        logger.info(f"Found {len(existing_scheduled)} currently scheduled movies with existing dates")

        # ===== STEP 2: Clear existing scheduled flags and dates =====
        conn.execute("UPDATE scored_movies_cache SET scheduled_for_deletion = 0, scheduled_date = NULL")
        
        # ===== STEP 3: Insert or update all scored movies in cache =====
        for entry in scored_movies:
            if entry.get("is_collection"):
                for member in entry.get("movies", []):
                    conn.execute("""
                        INSERT OR REPLACE INTO scored_movies_cache
                        (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                         size_gb, age_days, quality, monitored, normalized_score,
                         raw_score, factors, plex_play_count,
                         collection_name, collection_id, is_collection,
                         scheduled_for_deletion, scheduled_date, cached_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, CURRENT_TIMESTAMP)
                    """, (
                        member["movie_id"],
                        member["movie_title"],
                        member["movie_year"],
                        member.get("tmdb_id"),
                        member["tmdb_rating"],
                        member["size_gb"],
                        member["age_days"],
                        member["quality"],
                        1 if member["monitored"] else 0,
                        entry["normalized_score"],
                        entry["raw_score"],
                        json.dumps(member["factors"]),
                        member.get("plex_play_count", 0),
                        entry.get("collection_title"),
                        entry.get("collection_id"),
                    ))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO scored_movies_cache
                    (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                     size_gb, age_days, quality, monitored, normalized_score,
                     raw_score, factors, plex_play_count,
                     collection_name, collection_id, is_collection,
                     scheduled_for_deletion, scheduled_date, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, CURRENT_TIMESTAMP)
                """, (
                    entry["movie_id"],
                    entry["movie_title"],
                    entry["movie_year"],
                    entry.get("tmdb_id"),
                    entry["tmdb_rating"],
                    entry["size_gb"],
                    entry["age_days"],
                    entry["quality"],
                    1 if entry["monitored"] else 0,
                    entry["normalized_score"],
                    entry["raw_score"],
                    json.dumps(entry["factors"]),
                    entry.get("plex_play_count", 0),
                    None,
                    None,
                ))
        
        conn.commit()
        logger.info(f"Updated scored_movies_cache with {len(scored_movies)} entries")

        # ===== STEP 4: Determine top N movies to schedule =====
        max_queued = settings["max_queued"] if settings else 20
        threshold = settings["min_score_threshold"] if settings else 0
        deletions_per_day = int(settings["deletions_per_day"]) if settings and settings["deletions_per_day"] is not None else 0
        base_delay_days = settings["delete_after_days"] if settings else 7
        
        # Get all movies sorted by normalized_score (highest first)
        all_movies = conn.execute("""
            SELECT movie_id, normalized_score, scheduled_date
            FROM scored_movies_cache
            WHERE normalized_score > ?
            ORDER BY normalized_score DESC
        """, (threshold,)).fetchall()
        
        # Top N movies to schedule (or fewer if not enough qualify)
        top_movies = all_movies[:max_queued]
        
        logger.info(f"Top {len(top_movies)} movies qualify for scheduling (threshold: {threshold})")
        
        # ===== STEP 5: Assign scheduled dates with staggering =====
        # NOTE: existing_scheduled was populated BEFORE we cleared the flags
        
        # Calculate staggering for new movies
        current_time = datetime.now()
        
        for idx, movie in enumerate(top_movies):
            movie_id = movie["movie_id"]
            
            # Check if this movie was already scheduled
            if movie_id in existing_scheduled:
                # Keep existing scheduled date
                conn.execute(
                    "UPDATE scored_movies_cache SET scheduled_for_deletion = 1 WHERE movie_id = ?",
                    (movie_id,)
                )
                logger.debug(f"Keeping scheduled date for movie_id {movie_id} (stays in top {max_queued})")
            else:
                # New movie entering top N - calculate scheduled date
                stagger_days = 0
                if deletions_per_day > 0:
                    # How many new movies are before this one in the top N?
                    # Count new movies among top movies up to this index
                    new_movies_before = sum(
                        1 for i in range(idx) 
                        if top_movies[i]["movie_id"] not in existing_scheduled
                    )
                    stagger_days = new_movies_before // deletions_per_day
                
                scheduled_date = current_time + timedelta(days=base_delay_days + stagger_days)
                
                conn.execute("""
                    UPDATE scored_movies_cache 
                    SET scheduled_for_deletion = 1, scheduled_date = ?
                    WHERE movie_id = ?
                """, (scheduled_date.isoformat(), movie_id))
                
                logger.info(f"Scheduled new movie (rank #{idx+1}) for {scheduled_date.isoformat()} (+{base_delay_days + stagger_days} days)")
        
        # ===== STEP 6: Ensure all other movies are marked as not scheduled =====
        conn.execute("""
            UPDATE scored_movies_cache 
            SET scheduled_for_deletion = 0, scheduled_date = NULL
            WHERE movie_id NOT IN (
                SELECT movie_id FROM scored_movies_cache 
                WHERE scheduled_for_deletion = 1
            )
        """)
        
        conn.commit()
        
        # ===== STEP 6b: Get new scheduled movie IDs =====
        new_scheduled_ids = set()
        new_scheduled = conn.execute(
            "SELECT movie_id FROM scored_movies_cache WHERE scheduled_for_deletion = 1"
        ).fetchall()
        for row in new_scheduled:
            new_scheduled_ids.add(row["movie_id"])
        
        # ===== STEP 6c: Find movies that were removed from the schedule =====
        old_scheduled_ids = set(existing_scheduled.keys())
        removed_movie_ids = old_scheduled_ids - new_scheduled_ids
        
        # ===== STEP 6d: Remove removed movies from Plex collection =====
        if removed_movie_ids and plex_enabled and plex_client and plex_config and plex_config["collection_key"]:
            # Get full movie details for removed movies
            placeholders = ",".join("?" * len(removed_movie_ids))
            removed_movies_data = conn.execute(
                f"SELECT movie_id, movie_title, movie_year FROM scored_movies_cache WHERE movie_id IN ({placeholders})",
                tuple(removed_movie_ids)
            ).fetchall()
            
            if removed_movies_data:
                removed_movies_list = [dict(row) for row in removed_movies_data]
                logger.info(f"Removing {len(removed_movies_list)} movies from Plex collection (fell out of queue due to reranking)")
                await _remove_plex_collections(plex_client, removed_movies_list, plex_library_map)
        
        scheduled_count = conn.execute(
            "SELECT COUNT(*) as count FROM scored_movies_cache WHERE scheduled_for_deletion = 1"
        ).fetchone()["count"]
        
        logger.info(f"Score cycle complete: {scheduled_count} movies scheduled for deletion (max_queued: {max_queued})")
        
        # ===== STEP 7: Sync with Plex collection if enabled =====
        if plex_enabled and plex_client and plex_config and plex_config["collection_key"]:
            # Get all scheduled movies
            scheduled_movies = conn.execute("""
                SELECT movie_id, movie_title, movie_year, scheduled_for_deletion
                FROM scored_movies_cache
                WHERE scheduled_for_deletion = 1
            """).fetchall()
            
            if scheduled_movies:
                # Build library map if not already done
                if not plex_library_map:
                    plex_library_map = await _build_plex_library_map(plex_client)
                
                # Add scheduled movies to Plex collection
                rating_key_map = await _apply_plex_collections(
                    plex_client,
                    [dict(m) for m in scheduled_movies],
                    plex_library_map
                )
                logger.info(f"Added {len(rating_key_map)} movies to Plex collection")
        
        _active_run["current_movie"] = f"Score cycle complete: {scheduled_count} movies scheduled"
        _active_run["current"] = 100

    except Exception as e:
        logger.error(f"Score cycle failed: {e}")
        import traceback
        traceback.print_exc()
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

        # Get due movies from scored_movies_cache
        now = datetime.now().isoformat()
        due_movies = conn.execute("""
            SELECT movie_id, movie_title, movie_year, size_gb, normalized_score as score,
                   scheduled_date, tmdb_id
            FROM scored_movies_cache
            WHERE scheduled_for_deletion = 1 
              AND scheduled_date IS NOT NULL 
              AND scheduled_date <= ?
        """, (now,)).fetchall()

        if not due_movies:
            logger.info("No movies due for deletion")
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
            results = [dict(movie) for movie in due_movies]
            _active_run["dry_run_results"] = results
            _active_run["current_movie"] = f"Dry run complete — {len(results)} movies would be deleted"
            logger.info(f"Dry cull run complete: {len(results)} movies would be deleted")
            return

        # Normal deletion logic
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])

        deleted = 0
        failed = 0

        for movie in due_movies:
            logger.info(f"Deleting: {movie['movie_title']} (scheduled: {movie['scheduled_date']})")

            try:
                result = await radarr_client.delete_movie_entirely(movie["movie_id"])

                if result["success"]:
                    # Record in deletion history
                    conn.execute("""
                        INSERT INTO deletion_history
                        (movie_id, movie_title, movie_year, size_gb, score, status)
                        VALUES (?, ?, ?, ?, ?, 'deleted')
                    """, (
                        movie["movie_id"],
                        movie["movie_title"],
                        movie["movie_year"],
                        movie["size_gb"],
                        movie["score"]
                    ))
                    
                    # Remove from cache (movie no longer exists)
                    conn.execute(
                        "DELETE FROM scored_movies_cache WHERE movie_id = ?",
                        (movie["movie_id"],)
                    )
                    
                    deleted += 1
                    logger.info(f"Deleted: {movie['movie_title']}")

                    # ===== PROGRESS UPDATE: Movie deleted =====
                    percent_complete = int(((deleted + failed) / len(due_movies)) * 100) if due_movies else 0
                    _active_run["current"] = percent_complete
                    _active_run["current_movie"] = f"Deleted: {movie['movie_title']} ({deleted + failed} of {len(due_movies)})"
                    # ===== END PROGRESS UPDATE =====

                    # Remove from Plex collection after successful deletion
                    if plex_enabled and plex_client and plex_config["collection_key"]:
                        await _remove_plex_collections(
                            plex_client,
                            [dict(movie)],
                            plex_library_map
                        )
                else:
                    logger.error(f"Delete failed for {movie['movie_title']}: {result['message']}")
                    conn.execute("""
                        INSERT INTO deletion_history
                        (movie_id, movie_title, movie_year, size_gb, score, status, error_message)
                        VALUES (?, ?, ?, ?, ?, 'failed', ?)
                    """, (
                        movie["movie_id"],
                        movie["movie_title"],
                        movie["movie_year"],
                        movie["size_gb"],
                        movie["score"],
                        result["message"]
                    ))
                    
                    # Remove scheduled flag but keep in cache
                    conn.execute("""
                        UPDATE scored_movies_cache 
                        SET scheduled_for_deletion = 0, scheduled_date = NULL
                        WHERE movie_id = ?
                    """, (movie["movie_id"],))
                    
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
                conn.execute("""
                    UPDATE scored_movies_cache 
                    SET scheduled_for_deletion = 0, scheduled_date = NULL
                    WHERE movie_id = ?
                """, (movie["movie_id"],))
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
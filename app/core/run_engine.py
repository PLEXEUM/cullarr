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

        # Get Plex play counts if enabled — keyed by TMDb ID string
        plex_play_counts = None
        plex_enabled = plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"]
        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            plex_ok, plex_msg = await plex_client.test_connection()
            if plex_ok:
                # Use get_play_counts_by_tmdb so data is keyed by TMDb ID,
                # matching what the scoring engine looks up against movie.tmdbId
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()
                logger.info(f"Fetched Plex play counts for {len(plex_play_counts)} TMDb IDs")
            else:
                logger.warning(f"Plex connection failed: {plex_msg}, continuing without watch data")

        # Fetch movies from Radarr
        movies = await radarr_client.get_movies()

        # Score movies
        engine = ScoringEngine(conn)
        scored_movies = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

        logger.info(f"Scored {len(scored_movies)} movies")

        # Get current scheduled deletions count
        current_queue = conn.execute(
            "SELECT COUNT(*) as count FROM scheduled_deletions WHERE status = 'scheduled'"
        ).fetchone()
        current_count = current_queue["count"] if current_queue else 0
        max_queued = settings["max_queued"] if settings else 20
        available_slots = max_queued - current_count

        logger.info(f"Queue status: {current_count}/{max_queued} slots used, {available_slots} available")

        if available_slots <= 0:
            logger.info("Queue full, no new movies added")
            return

        # Get top N movies by score, excluding any already in the queue
        scheduled_ids = conn.execute(
            "SELECT movie_id FROM scheduled_deletions"
        ).fetchall()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

        candidates = [m for m in scored_movies if m["movie_id"] not in scheduled_id_set]
        to_add = candidates[:available_slots]

        # Add to scheduled deletions — INSERT OR IGNORE to never reset an existing countdown
        added = 0
        for movie in to_add:
            scheduled_date = datetime.now() + timedelta(days=settings["delete_after_days"] if settings else 7)

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO scheduled_deletions
                    (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating, size_gb, quality, monitored, score, score_factors, scheduled_date, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')""",
                    (
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
                        scheduled_date.isoformat()
                    )
                )
                added += 1
                logger.info(f"Added to queue: {movie['movie_title']} (score: {movie['normalized_score']:.1f})")

                # Add Plex label if enabled
                if plex_enabled and plex_config["label_text"]:
                    logger.debug(f"Would add Plex label for {movie['movie_title']}")
            except Exception as e:
                logger.error(f"Failed to add {movie['movie_title']} to queue: {e}")

        conn.commit()
        logger.info(f"Score cycle complete: added {added} movies to queue")

    except Exception as e:
        logger.error(f"Score cycle failed: {e}")
    finally:
        conn.close()
        await release_run_lock()


async def run_cull_cycle():
    """Delete movies that have passed their scheduled deletion date."""
    lock_acquired = await acquire_run_lock("cull")
    if not lock_acquired:
        logger.info("Cull run skipped - another run in progress")
        return

    try:
        conn = get_connection()

        # Load Radarr config
        radarr_config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.error("Radarr not configured, cannot run cull cycle")
            return

        # Get due movies
        now = datetime.now().isoformat()
        due_movies = conn.execute(
            "SELECT * FROM scheduled_deletions WHERE status = 'scheduled' AND scheduled_date <= ?",
            (now,)
        ).fetchall()

        if not due_movies:
            logger.info("No movies due for deletion")
            return

        logger.info(f"Found {len(due_movies)} movies due for deletion")

        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])

        deleted = 0
        failed = 0

        for movie in due_movies:
            logger.info(f"Deleting: {movie['movie_title']} (scheduled: {movie['scheduled_date']})")

            try:
                result = await radarr_client.delete_movie_file_only(movie["movie_id"])

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
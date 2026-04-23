from fastapi import APIRouter, HTTPException, Query
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.utils.logger import get_logger
import json

router = APIRouter()
logger = get_logger()


@router.get("/dashboard/queue-status")
async def get_queue_status():
    """Get queue status and system health."""
    conn = get_connection()
    try:
        queue_stats = conn.execute("""
            SELECT
                COUNT(*) as scheduled_count,
                SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) as active_count
            FROM scheduled_deletions
        """).fetchone()

        settings = conn.execute("SELECT max_queued FROM settings WHERE id = 1").fetchone()
        max_queued = settings["max_queued"] if settings else 20
        scheduled_count = queue_stats["scheduled_count"] if queue_stats else 0

        # Radarr status
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        radarr_configured = bool(radarr_config and radarr_config["url"] and radarr_config["api_key"])
        radarr_status = "unknown"
        if radarr_configured:
            try:
                client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
                ok, _ = await client.test_connection()
                radarr_status = "connected" if ok else "error"
            except Exception:
                radarr_status = "error"

        # Plex status
        plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
        plex_configured = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])
        plex_status = "unknown"
        plex_details = None
        if plex_configured:
            try:
                client = PlexClient(plex_config["url"], plex_config["api_key"])
                ok, _ = await client.test_connection()
                plex_status = "connected" if ok else "error"
                if ok:
                    # Use cached play count total from last score run rather than
                    # making a live Plex API call on every dashboard load
                    cached = conn.execute(
                        "SELECT COUNT(*) as total FROM scored_movies_cache WHERE plex_play_count > 0"
                    ).fetchone()
                    watched_count = cached["total"] if cached else 0
                    plex_details = f"{watched_count} watched items (from last score run)"
            except Exception:
                plex_status = "error"
    finally:
        conn.close()

    return {
        "scheduled_count": scheduled_count,
        "max_queued": max_queued,
        "percent_used": round((scheduled_count / max_queued) * 100, 1) if max_queued > 0 else 0,
        "radarr": {
            "configured": radarr_configured,
            "status": radarr_status,
        },
        "plex": {
            "configured": plex_configured,
            "status": plex_status,
            "details": plex_details,
        },
    }


@router.get("/dashboard/scheduled")
async def get_scheduled_deletions(limit: int = Query(50, ge=1, le=200)):
    """Get scheduled deletions queue."""
    conn = get_connection()
    try:
        scheduled = conn.execute("""
            SELECT id, movie_id, movie_title, movie_year, size_gb, quality, score, scheduled_date, status
            FROM scheduled_deletions
            WHERE status = 'scheduled'
            ORDER BY scheduled_date ASC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    return {
        "items": [dict(row) for row in scheduled],
        "count": len(scheduled),
    }


@router.delete("/dashboard/scheduled/{movie_id}")
async def remove_from_queue(movie_id: int):
    """Remove a movie from the scheduled deletions queue."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, movie_title FROM scheduled_deletions WHERE movie_id = ? AND status = 'scheduled'",
            (movie_id,)
        ).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Movie not found in queue")

        conn.execute(
            "DELETE FROM scheduled_deletions WHERE movie_id = ? AND status = 'scheduled'",
            (movie_id,)
        )
        conn.commit()
        logger.info(f"Manually removed from queue: {existing['movie_title']}")
        return {"success": True, "message": f"Removed {existing['movie_title']} from queue"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove movie {movie_id} from queue: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove movie from queue")
    finally:
        conn.close()


@router.get("/dashboard/score-queue")
async def get_score_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    refresh: bool = False
):
    """
    Get scored movies from cache. If refresh=True or cache is empty,
    triggers a live fetch from Radarr and rebuilds the cache.
    """
    conn = get_connection()
    try:
        # Check if cache table exists and has data
        cache_count = conn.execute(
            "SELECT COUNT(*) as count FROM scored_movies_cache"
        ).fetchone()
        has_cache = cache_count and cache_count["count"] > 0
    except Exception:
        has_cache = False
    finally:
        conn.close()

    if refresh or not has_cache:
        await _rebuild_score_cache()

    return await _get_score_queue_from_cache(page, per_page)


async def _rebuild_score_cache():
    """Fetch movies from Radarr, score them, and write to cache table."""
    conn = get_connection()
    try:
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.warning("Radarr not configured, cannot rebuild score cache")
            return

        plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])
        settings = conn.execute("SELECT protection_days, collection_grouping FROM settings WHERE id = 1").fetchone()
    finally:
        conn.close()

    try:
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        movies = await radarr_client.get_movies()

        plex_play_counts = None
        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await plex_client.test_connection()
            if ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()

        conn = get_connection()
        try:
            engine = ScoringEngine(conn)
            if settings:
                engine.protection_days = settings["protection_days"]
                engine.collection_grouping = bool(settings["collection_grouping"])

            scored = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

            # Rebuild cache atomically
            conn.execute("DELETE FROM scored_movies_cache")
            for movie in scored:
                play_count = 0
                if plex_play_counts and movie.get("tmdb_id"):
                    entry = plex_play_counts.get(str(movie["tmdb_id"]))
                    if entry:
                        play_count = entry.get("play_count", 0)

                conn.execute("""
                    INSERT INTO scored_movies_cache
                    (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                     size_gb, age_days, quality, monitored, normalized_score,
                     raw_score, factors, plex_play_count, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    movie["movie_id"],
                    movie["movie_title"],
                    movie["movie_year"],
                    movie.get("tmdb_id"),
                    movie["tmdb_rating"],
                    movie["size_gb"],
                    movie["age_days"],
                    movie["quality"],
                    1 if movie["monitored"] else 0,
                    movie["normalized_score"],
                    movie["raw_score"],
                    json.dumps(movie["factors"]),
                    play_count,
                ))
            conn.commit()
            logger.info(f"Score cache rebuilt with {len(scored)} movies")
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"Failed to rebuild score cache: {e}")


async def _get_score_queue_from_cache(page: int, per_page: int) -> dict:
    """Read paginated score queue from cache, excluding already-scheduled movies."""
    conn = get_connection()
    try:
        scheduled_ids = conn.execute(
            "SELECT movie_id FROM scheduled_deletions"
        ).fetchall()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

        all_cached = conn.execute("""
            SELECT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                   size_gb, age_days, quality, monitored, normalized_score,
                   raw_score, factors, plex_play_count, cached_at
            FROM scored_movies_cache
            ORDER BY normalized_score DESC
        """).fetchall()

        available = [dict(row) for row in all_cached if row["movie_id"] not in scheduled_id_set]

        total = len(available)
        offset = (page - 1) * per_page
        paginated = available[offset:offset + per_page]
        pages = (total + per_page - 1) // per_page if total > 0 else 1

        # Parse factors JSON for each item
        for item in paginated:
            try:
                item["factors"] = json.loads(item["factors"]) if item["factors"] else []
            except Exception:
                item["factors"] = []

        return {
            "items": paginated,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }
    finally:
        conn.close()


@router.get("/dashboard/failed")
async def get_failed_deletions():
    """Get failed deletions that need manual attention."""
    conn = get_connection()
    try:
        failed = conn.execute("""
            SELECT id, movie_title, movie_year, size_gb, score, error_message, deleted_at
            FROM deletion_history
            WHERE status = 'failed'
            ORDER BY deleted_at DESC
            LIMIT 50
        """).fetchall()
    finally:
        conn.close()

    return {"items": [dict(row) for row in failed]}


@router.get("/dashboard/settings-summary")
async def get_settings_summary():
    """Get summary of current settings for dashboard display."""
    conn = get_connection()
    try:
        settings = conn.execute("""
            SELECT delete_after_days, protection_days, collection_grouping, max_queued
            FROM settings WHERE id = 1
        """).fetchone()

        weights = conn.execute("""
            SELECT age_weight, size_weight, rating_weight, quality_weight, monitored_weight, watched_weight
            FROM scoring_weights WHERE id = 1
        """).fetchone()
    finally:
        conn.close()

    return {
        "delete_after_days": settings["delete_after_days"] if settings else 7,
        "protection_days": settings["protection_days"] if settings else 30,
        "collection_grouping": bool(settings["collection_grouping"]) if settings else False,
        "max_queued": settings["max_queued"] if settings else 20,
        "weights": dict(weights) if weights else {},
    }
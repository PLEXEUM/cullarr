from fastapi import APIRouter, HTTPException, Query
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.core.run_engine import acquire_run_lock, release_run_lock
from app.utils.logger import get_logger
import json

router = APIRouter()
logger = get_logger()


@router.get("/dashboard/queue-status")
async def get_queue_status():
    """Get queue status and system health."""
    conn = get_connection()

    # Queue status
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
            ok, msg = await client.test_connection()
            radarr_status = "connected" if ok else "error"
        except:
            radarr_status = "error"

    # Plex status
    plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
    plex_configured = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])
    plex_status = "unknown"
    plex_details = None
    if plex_configured:
        try:
            client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, msg = await client.test_connection()
            plex_status = "connected" if ok else "error"
            if ok:
                play_counts = await client.get_all_play_history()
                user_count = len(set(v.get("user_id", 0) for v in play_counts.values()))
                plex_details = f"{user_count} users, {len(play_counts)} items"
        except:
            plex_status = "error"

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
    scheduled = conn.execute("""
        SELECT id, movie_title, movie_year, size_gb, quality, score, scheduled_date, status
        FROM scheduled_deletions
        WHERE status = 'scheduled'
        ORDER BY scheduled_date ASC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    return {
        "items": [dict(row) for row in scheduled],
        "count": len(scheduled),
    }


@router.get("/dashboard/score-queue")
async def get_score_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    refresh: bool = False
):
    """Get scored movies (live calculation)."""
    conn = get_connection()

    # Get Radarr config
    radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
    if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
        return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}

    # Get Plex config
    plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
    plex_enabled = plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"]

    # Get settings
    settings = conn.execute("SELECT protection_days, collection_grouping FROM settings WHERE id = 1").fetchone()
    conn.close()

    try:
        # Fetch movies from Radarr
        client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        movies = await client.get_movies()

        # Get Plex play counts if enabled
        plex_play_counts = None
        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await plex_client.test_connection()
            if ok:
                plex_play_counts = await plex_client.get_all_play_history()

        # Score movies
        engine = ScoringEngine(get_connection())
        engine.protection_days = settings["protection_days"] if settings else 30
        engine.collection_grouping = settings["collection_grouping"] if settings else False

        scored = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

        # Filter out already scheduled movies
        conn = get_connection()
        scheduled_ids = conn.execute("SELECT movie_id FROM scheduled_deletions").fetchall()
        conn.close()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

        available = [m for m in scored if m["movie_id"] not in scheduled_id_set]

        # Pagination
        total = len(available)
        offset = (page - 1) * per_page
        paginated = available[offset:offset + per_page]
        pages = (total + per_page - 1) // per_page if total > 0 else 1

        return {
            "items": paginated,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }

    except Exception as e:
        logger.error(f"Failed to get score queue: {e}")
        return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}


@router.get("/dashboard/failed")
async def get_failed_deletions():
    """Get failed deletions that need manual attention."""
    conn = get_connection()
    failed = conn.execute("""
        SELECT id, movie_title, movie_year, size_gb, score, error_message, deleted_at
        FROM deletion_history
        WHERE status = 'failed'
        ORDER BY deleted_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()

    return {"items": [dict(row) for row in failed]}


@router.get("/dashboard/settings-summary")
async def get_settings_summary():
    """Get summary of current settings for dashboard display."""
    conn = get_connection()

    settings = conn.execute("""
        SELECT delete_after_days, protection_days, collection_grouping, max_queued
        FROM settings WHERE id = 1
    """).fetchone()

    weights = conn.execute("""
        SELECT age_weight, size_weight, rating_weight, quality_weight, monitored_weight, watched_weight
        FROM scoring_weights WHERE id = 1
    """).fetchone()

    conn.close()

    return {
        "delete_after_days": settings["delete_after_days"] if settings else 7,
        "protection_days": settings["protection_days"] if settings else 30,
        "collection_grouping": bool(settings["collection_grouping"]) if settings else False,
        "max_queued": settings["max_queued"] if settings else 20,
        "weights": dict(weights) if weights else {},
    }
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import asyncio
import uuid
from app.db.database import get_connection
from app.core.run_engine import run_score_cycle, run_cull_cycle
from app.core.scheduler import get_next_score_run, get_next_cull_run
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

# AsyncIO lock to prevent race conditions on _active_run
_run_lock = asyncio.Lock()

# Global state for tracking active runs
_active_run = {
    "is_running": False,
    "run_id": None,
    "run_type": None,
    "current": 0,
    "total": 0,
    "current_movie": "",
    "cancelled": False,
    "dry_run": False,
    "dry_run_results": None,
}


async def _set_run_active(run_id: str, run_type: str, dry_run: bool = False):
    """Safely set run as active using lock."""
    async with _run_lock:
        if _active_run["is_running"]:
            return False
        _active_run["is_running"] = True
        _active_run["run_id"] = run_id
        _active_run["run_type"] = run_type
        _active_run["cancelled"] = False
        _active_run["dry_run"] = dry_run
        _active_run["dry_run_results"] = None
        _active_run["current"] = 0
        _active_run["total"] = 0
        _active_run["current_movie"] = ""
        return True


async def _set_run_inactive():
    """Safely clear run state using lock."""
    async with _run_lock:
        _active_run["is_running"] = False
        _active_run["run_id"] = None
        _active_run["run_type"] = None
        _active_run["dry_run"] = False


async def _run_dry_score(run_id: str):
    """
    Dry run — scores movies and stores results in memory without
    writing anything to the scheduled_deletions table.
    """
    from app.core.radarr_client import RadarrClient
    from app.core.plex_client import PlexClient
    from app.core.scoring_engine import ScoringEngine

    try:
        conn = get_connection()
        radarr_config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        plex_config = conn.execute("SELECT * FROM plex_config WHERE id = 1").fetchone()
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()

        scheduled_ids = conn.execute(
            "SELECT movie_id FROM scheduled_deletions"
        ).fetchall()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}
        conn.close()

        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.error("Radarr not configured, cannot run dry score")
            return

        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, radarr_msg = await radarr_client.test_connection()
        if not radarr_ok:
            logger.error(f"Radarr connection failed: {radarr_msg}")
            return

        # Plex play counts if enabled
        plex_play_counts = None
        plex_enabled = bool(
            plex_config and plex_config["enabled"] and
            plex_config["url"] and plex_config["api_key"]
        )
        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await plex_client.test_connection()
            if ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()

        _active_run["current_movie"] = "Fetching movies from Radarr..."
        movies = await radarr_client.get_movies()
        _active_run["total"] = len(movies)

        conn = get_connection()
        try:
            engine = ScoringEngine(conn)
            scored = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)
        finally:
            conn.close()

        # Filter already-queued movies and cap at max_queued
        max_queued = settings["max_queued"] if settings else 20
        candidates = [m for m in scored if m["movie_id"] not in scheduled_id_set]
        would_queue = candidates[:max_queued]

        _active_run["dry_run_results"] = would_queue
        _active_run["current"] = len(movies)
        _active_run["current_movie"] = f"Dry run complete — {len(would_queue)} movies would be queued"
        logger.info(f"Dry score run complete: {len(would_queue)} movies would be queued")

    except Exception as e:
        logger.error(f"Dry score run failed: {e}")
    finally:
        await _set_run_inactive()


@router.post("/run/score")
async def trigger_score_run(dry_run: bool = False, background_tasks: BackgroundTasks = None):
    """Manually trigger a score run."""
    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        raise HTTPException(status_code=400, detail="Cullarr is disabled. Enable in Settings first.")

    acquired = await _set_run_active(str(uuid.uuid4()), "score", dry_run=dry_run)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail=f"A {_active_run['run_type']} run is already in progress"
        )

    run_id = _active_run["run_id"]

    async def run_and_reset():
        try:
            if dry_run:
                logger.info(f"Dry score run started (ID: {run_id})")
                await _run_dry_score(run_id)
            else:
                await run_score_cycle()
        except Exception as e:
            logger.error(f"Score run failed: {e}")
        finally:
            await _set_run_inactive()

    if background_tasks:
        background_tasks.add_task(run_and_reset)
    else:
        asyncio.create_task(run_and_reset())

    return {
        "success": True,
        "message": f"{'Dry ' if dry_run else ''}Score run started",
        "run_id": run_id,
        "dry_run": dry_run,
    }


@router.post("/run/cull")
async def trigger_cull_run(background_tasks: BackgroundTasks = None):
    """Manually trigger a cull run."""
    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        raise HTTPException(status_code=400, detail="Cullarr is disabled. Enable in Settings first.")

    acquired = await _set_run_active(str(uuid.uuid4()), "cull")
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail=f"A {_active_run['run_type']} run is already in progress"
        )

    async def run_and_reset():
        try:
            await run_cull_cycle()
        except Exception as e:
            logger.error(f"Cull run failed: {e}")
        finally:
            await _set_run_inactive()

    if background_tasks:
        background_tasks.add_task(run_and_reset)
    else:
        asyncio.create_task(run_and_reset())

    return {
        "success": True,
        "message": "Cull run started",
        "run_id": _active_run["run_id"],
    }


@router.get("/run/status")
async def get_run_status():
    """Get current run status including dry run results if available."""
    return {
        "is_running": _active_run["is_running"],
        "run_id": _active_run["run_id"],
        "run_type": _active_run["run_type"],
        "current": _active_run["current"],
        "total": _active_run["total"],
        "current_movie": _active_run["current_movie"],
        "cancelled": _active_run["cancelled"],
        "dry_run": _active_run["dry_run"],
        "dry_run_results": _active_run["dry_run_results"],
    }


@router.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a running run."""
    if not _active_run["is_running"]:
        raise HTTPException(status_code=400, detail="No active run")

    if _active_run["run_id"] != run_id:
        raise HTTPException(status_code=400, detail="Run ID mismatch")

    _active_run["cancelled"] = True
    logger.info(f"Run {run_id} cancellation requested")
    return {"success": True, "message": "Cancellation requested"}


@router.get("/run/next")
async def get_next_runs():
    """Get next scheduled run times."""
    return {
        "next_score_run": get_next_score_run(),
        "next_cull_run": get_next_cull_run(),
    }
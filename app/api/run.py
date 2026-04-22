from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import uuid
from app.db.database import get_connection
from app.core.run_engine import run_score_cycle, run_cull_cycle, acquire_run_lock, release_run_lock
from app.core.scheduler import get_next_score_run, get_next_cull_run
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

# Global state for tracking active runs
_active_run = {
    "is_running": False,
    "run_id": None,
    "run_type": None,
    "current": 0,
    "total": 0,
    "current_movie": "",
    "cancelled": False,
}


@router.post("/run/score")
async def trigger_score_run(dry_run: bool = False, background_tasks: BackgroundTasks = None):
    """Manually trigger a score run."""
    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        raise HTTPException(status_code=400, detail="Cullarr is disabled. Enable in Settings first.")

    if _active_run["is_running"]:
        raise HTTPException(status_code=409, detail=f"A {_active_run['run_type']} run is already in progress")

    run_id = str(uuid.uuid4())
    _active_run["is_running"] = True
    _active_run["run_id"] = run_id
    _active_run["run_type"] = "score"
    _active_run["cancelled"] = False

    async def run_and_reset():
        try:
            if dry_run:
                logger.info(f"Dry score run started (ID: {run_id})")
                # TODO: Implement dry run mode
            else:
                await run_score_cycle()
        except Exception as e:
            logger.error(f"Score run failed: {e}")
        finally:
            _active_run["is_running"] = False
            _active_run["run_id"] = None
            _active_run["run_type"] = None

    if background_tasks:
        background_tasks.add_task(run_and_reset)
    else:
        import asyncio
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

    if _active_run["is_running"]:
        raise HTTPException(status_code=409, detail=f"A {_active_run['run_type']} run is already in progress")

    run_id = str(uuid.uuid4())
    _active_run["is_running"] = True
    _active_run["run_id"] = run_id
    _active_run["run_type"] = "cull"
    _active_run["cancelled"] = False

    async def run_and_reset():
        try:
            await run_cull_cycle()
        except Exception as e:
            logger.error(f"Cull run failed: {e}")
        finally:
            _active_run["is_running"] = False
            _active_run["run_id"] = None
            _active_run["run_type"] = None

    if background_tasks:
        background_tasks.add_task(run_and_reset)
    else:
        import asyncio
        asyncio.create_task(run_and_reset())

    return {
        "success": True,
        "message": "Cull run started",
        "run_id": run_id,
    }


@router.get("/run/status")
async def get_run_status():
    """Get current run status."""
    return {
        "is_running": _active_run["is_running"],
        "run_id": _active_run["run_id"],
        "run_type": _active_run["run_type"],
        "current": _active_run["current"],
        "total": _active_run["total"],
        "current_movie": _active_run["current_movie"],
        "cancelled": _active_run["cancelled"],
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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_connection
from app.utils.logger import get_logger
from app.utils.validators import validate_cron, validate_delete_after_days, validate_protection_days, validate_max_queued
from app.core.scheduler import update_score_schedule, update_cull_schedule

router = APIRouter()
logger = get_logger()


class ScoringWeightsInput(BaseModel):
    age_weight: int
    size_weight: int
    rating_weight: int
    quality_weight: int
    monitored_weight: int
    watched_weight: int
    age_max_days: int
    size_max_gb: int


class SettingsInput(BaseModel):
    enabled: bool
    score_cron: str
    cull_cron: str
    max_queued: int
    delete_after_days: int
    protection_days: int
    collection_grouping: bool


@router.get("/settings/weights")
async def get_scoring_weights():
    """Get current scoring weights."""
    conn = get_connection()
    try:
        weights = conn.execute("SELECT * FROM scoring_weights WHERE id = 1").fetchone()
    finally:
        conn.close()

    if not weights:
        return {
            "age_weight": 25,
            "size_weight": 25,
            "rating_weight": 15,
            "quality_weight": 15,
            "monitored_weight": 10,
            "watched_weight": 10,
            "age_max_days": 365,
            "size_max_gb": 100,
        }

    return dict(weights)


@router.post("/settings/weights")
async def save_scoring_weights(data: ScoringWeightsInput):
    """Save scoring weights."""
    total = data.age_weight + data.size_weight + data.rating_weight + data.quality_weight + data.monitored_weight + data.watched_weight
    if total != 100:
        raise HTTPException(status_code=400, detail=f"Weights must sum to 100, got {total}")

    if not all(0 <= w <= 100 for w in [data.age_weight, data.size_weight, data.rating_weight, data.quality_weight, data.monitored_weight, data.watched_weight]):
        raise HTTPException(status_code=400, detail="Each weight must be between 0 and 100")

    if data.age_max_days < 1 or data.age_max_days > 3650:
        raise HTTPException(status_code=400, detail="Age max days must be between 1 and 3650")

    if data.size_max_gb < 1 or data.size_max_gb > 10000:
        raise HTTPException(status_code=400, detail="Size max GB must be between 1 and 10000")

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE scoring_weights SET
                age_weight = ?, size_weight = ?, rating_weight = ?,
                quality_weight = ?, monitored_weight = ?, watched_weight = ?,
                age_max_days = ?, size_max_gb = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (data.age_weight, data.size_weight, data.rating_weight,
             data.quality_weight, data.monitored_weight, data.watched_weight,
             data.age_max_days, data.size_max_gb)
        )
        conn.commit()
        logger.info("Scoring weights saved")
        return {"success": True, "message": "Weights saved"}
    except Exception as e:
        logger.error(f"Failed to save weights: {e}")
        raise HTTPException(status_code=500, detail="Failed to save weights")
    finally:
        conn.close()


@router.get("/settings")
async def get_settings():
    """Get all application settings."""
    conn = get_connection()
    try:
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    finally:
        conn.close()

    if not settings:
        return {
            "enabled": False,
            "score_cron": "0 3 * * 0",
            "cull_cron": "0 2 * * *",
            "max_queued": 20,
            "delete_after_days": 7,
            "protection_days": 30,
            "collection_grouping": False,
        }

    return dict(settings)


@router.post("/settings")
async def save_settings(data: SettingsInput):
    """Save application settings."""
    is_valid, error = validate_cron(data.score_cron)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Score cron: {error}")

    is_valid, error = validate_cron(data.cull_cron)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Cull cron: {error}")

    is_valid, error = validate_max_queued(data.max_queued)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    is_valid, error = validate_delete_after_days(data.delete_after_days)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    is_valid, error = validate_protection_days(data.protection_days)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE settings SET
                enabled = ?, score_cron = ?, cull_cron = ?,
                max_queued = ?, delete_after_days = ?, protection_days = ?,
                collection_grouping = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (1 if data.enabled else 0, data.score_cron, data.cull_cron,
             data.max_queued, data.delete_after_days, data.protection_days,
             1 if data.collection_grouping else 0)
        )
        conn.commit()

        if data.enabled:
            update_score_schedule(data.score_cron)
            update_cull_schedule(data.cull_cron)
            logger.info(f"Schedules updated: score={data.score_cron}, cull={data.cull_cron}")
        else:
            logger.info("Cullarr disabled via settings")

        return {"success": True, "message": "Settings saved"}
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings")
    finally:
        conn.close()
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
    min_score_threshold: int = 0


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

    result = dict(weights)

    return result


@router.post("/settings/weights")
async def save_scoring_weights(data: ScoringWeightsInput):
    """Save scoring weights."""
    if not all(1 <= w <= 10 for w in [data.age_weight, data.size_weight, data.rating_weight, data.quality_weight, data.watched_weight]):
        raise HTTPException(status_code=400, detail="Each weight must be between 1 and 10")

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
                age_max_days = ?, size_max_gb = ?,
                updated_at = CURRENT_TIMESTAMP
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
            "min_score_threshold": 0,
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
    
    if data.min_score_threshold < 0 or data.min_score_threshold > 100:
        raise HTTPException(status_code=400, detail="Minimum score threshold must be between 0 and 100")

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE settings SET
                enabled = ?, score_cron = ?, cull_cron = ?,
                max_queued = ?, delete_after_days = ?, protection_days = ?,
                collection_grouping = ?, min_score_threshold = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (1 if data.enabled else 0, data.score_cron, data.cull_cron,
             data.max_queued, data.delete_after_days, data.protection_days,
             1 if data.collection_grouping else 0, data.min_score_threshold)
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

@router.post("/settings/recalibrate")
async def recalibrate_advanced_settings():
    """
    Automatically calculate age_max_days and size_max_gb based on library statistics.
    Sets age_max_days = 90th percentile age × 1.5
    Sets size_max_gb = 95th percentile size
    """
    from app.core.radarr_client import RadarrClient
    from datetime import datetime

    def percentile(data: list, p: float) -> float:
        """Calculate the p-th percentile of a list of numbers."""
        if not data:
            return 0
        data_sorted = sorted(data)
        n = len(data_sorted)
        index = (n - 1) * (p / 100)
        lower = int(index)
        upper = lower + 1
        if upper >= n:
            return data_sorted[-1]
        weight = index - lower
        return data_sorted[lower] * (1 - weight) + data_sorted[upper] * weight

    conn = get_connection()
    try:
        # Get Radarr config
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        # Fetch movies
        client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, _ = await client.test_connection()
        if not radarr_ok:
            raise HTTPException(status_code=400, detail="Cannot connect to Radarr")

        movies = await client.get_movies()

        # Collect ages and sizes from movies with files
        ages = []
        sizes = []

        for movie in movies:
            movie_file = movie.get("movieFile")
            if not movie_file:
                continue

            # Size in GB
            size_gb = movie_file.get("size", 0) / (1024 ** 3)
            if size_gb > 0:
                sizes.append(size_gb)

            # Age in days
            added_str = movie_file.get("dateAdded") or movie.get("added")
            if added_str:
                try:
                    added_str_clean = added_str.replace("Z", "+00:00")
                    added = datetime.fromisoformat(added_str_clean)
                    if added.tzinfo is not None:
                        added = added.replace(tzinfo=None)
                    age_days = (datetime.now() - added).days
                    if age_days > 0:
                        ages.append(age_days)
                except Exception:
                    pass

        if not ages or not sizes:
            raise HTTPException(status_code=400, detail="Not enough movie data to calibrate")

        # Calculate percentiles
        age_max_days = int(percentile(ages, 90) * 1.5)
        size_max_gb = float(percentile(sizes, 95))

        # Ensure reasonable bounds
        age_max_days = max(30, min(age_max_days, 3650))
        size_max_gb = max(5, min(size_max_gb, 1000))

        # Update scoring_weights
        conn.execute(
            """UPDATE scoring_weights SET
                age_max_days = ?, size_max_gb = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (age_max_days, size_max_gb)
        )
        conn.commit()

        return {
            "success": True,
            "age_max_days": age_max_days,
            "size_max_gb": round(size_max_gb, 1),
            "message": f"Calibrated: age_max_days={age_max_days}, size_max_gb={size_max_gb:.1f}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Recalibration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Recalibration failed: {str(e)}")
    finally:
        conn.close()

@router.post("/settings/preview")
async def get_score_preview(weights_data: dict):
    """
    Calculate score for a representative movie using provided weights.
    Used by the live preview feature in settings.
    """
    from app.core.radarr_client import RadarrClient
    from app.core.scoring_engine import ScoringEngine
    from app.db.database import get_connection
    from datetime import datetime

    conn = get_connection()
    try:
        # Get Radarr config
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            return {"movie": None, "error": "Radarr not configured"}

        # Get Plex config (for watched status)
        plex_config = conn.execute("SELECT enabled, url, api_key FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])

        # Fetch movies from Radarr
        client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, _ = await client.test_connection()
        if not radarr_ok:
            return {"movie": None, "error": "Cannot connect to Radarr"}

        movies = await client.get_movies()

        # Find first movie with a file (for preview)
        preview_movie = None
        for movie in movies:
            if movie.get("movieFile"):
                preview_movie = movie
                break

        if not preview_movie:
            return {"movie": None, "error": "No movies with files found"}

        # Fetch Plex play counts if enabled
        plex_play_counts = None
        if plex_enabled:
            from app.core.plex_client import PlexClient
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await plex_client.test_connection()
            if ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()

        # Create a temporary scoring engine with the provided weights
        # We need to temporarily override the weights in the engine
        engine = ScoringEngine(conn)
        
        # Save original weights
        original_weights = {
            "age_weight": engine.age_weight,
            "size_weight": engine.size_weight,
            "rating_weight": engine.rating_weight,
            "quality_weight": engine.quality_weight,
            "watched_weight": engine.watched_weight,
            "age_max_days": engine.age_max_days,
            "size_max_gb": engine.size_max_gb,
        }
        
        try:
            # Apply preview weights
            engine.age_weight = weights_data.get("age_weight", 25) / 100.0
            engine.size_weight = weights_data.get("size_weight", 25) / 100.0
            engine.rating_weight = weights_data.get("rating_weight", 15) / 100.0
            engine.quality_weight = weights_data.get("quality_weight", 15) / 100.0
            engine.watched_weight = weights_data.get("watched_weight", 10) / 100.0
            engine.age_max_days = weights_data.get("age_max_days", 365)
            engine.size_max_gb = weights_data.get("size_max_gb", 100)
            engine.monitored_weight = 0.0  # Always disabled
            
            # Calculate score for preview movie
            result = engine.calculate_movie_score(preview_movie, plex_play_counts, plex_enabled)
            
            if not result.get("eligible"):
                return {"movie": None, "error": "Selected movie has no file"}
            
            return {
                "movie": {
                    "movie_title": preview_movie.get("title"),
                    "movie_year": preview_movie.get("year"),
                    "size_gb": result.get("size_gb", 0),
                    "age_days": result.get("age_days", 0),
                    "raw_score": result.get("score", 0),
                    "factors": result.get("factors", []),
                }
            }
        finally:
            # Restore original weights
            engine.age_weight = original_weights["age_weight"]
            engine.size_weight = original_weights["size_weight"]
            engine.rating_weight = original_weights["rating_weight"]
            engine.quality_weight = original_weights["quality_weight"]
            engine.watched_weight = original_weights["watched_weight"]
            engine.age_max_days = original_weights["age_max_days"]
            engine.size_max_gb = original_weights["size_max_gb"]

    except Exception as e:
        logger.error(f"Preview failed: {e}")
        return {"movie": None, "error": str(e)}
    finally:
        conn.close()
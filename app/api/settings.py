from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_connection
from app.utils.logger import get_logger
from app.utils.validators import validate_cron, validate_delete_after_days, validate_max_queued
from app.core.scheduler import update_score_schedule, update_cull_schedule
from app.core.scoring_engine import ScoringEngine, apply_score_penalty

router = APIRouter()
logger = get_logger()


class ScoringWeightsInput(BaseModel):
    age_raw: int
    size_raw: int
    rating_raw: int
    quality_raw: int
    watched_raw: int
    age_max_days: int
    size_max_gb: int
    protection_days: int


class SettingsInput(BaseModel):
    enabled: bool
    score_cron: str
    cull_cron: str
    max_queued: int
    deletions_per_day: int = 0
    delete_after_days: int
    collection_grouping: bool
    min_score_threshold: int = 0


@router.get("/settings/weights")
async def get_scoring_weights() -> dict:
    """Get current scoring weights (raw 1-10 values only)."""
    conn = get_connection()
    try:
        weights = conn.execute("SELECT * FROM scoring_weights WHERE id = 1").fetchone()
    finally:
        conn.close()

    if not weights:
        return {
            "age_raw": 5,
            "size_raw": 5,
            "rating_raw": 5,
            "quality_raw": 5,
            "watched_raw": 5,
            "age_max_days": 365,
            "size_max_gb": 100,
        }

    # Return only the fields the frontend actually uses
    return {
        "age_raw": weights["age_raw"] if weights["age_raw"] is not None else 5,
        "size_raw": weights["size_raw"] if weights["size_raw"] is not None else 5,
        "rating_raw": weights["rating_raw"] if weights["rating_raw"] is not None else 5,
        "quality_raw": weights["quality_raw"] if weights["quality_raw"] is not None else 5,
        "watched_raw": weights["watched_raw"] if weights["watched_raw"] is not None else 5,
        "age_max_days": weights["age_max_days"],
        "size_max_gb": weights["size_max_gb"],
    }


@router.post("/settings/weights")
async def save_scoring_weights(data: ScoringWeightsInput):
    """
    Save scoring weights.
    Raw values (1-10) are received from frontend.
    Percentages are calculated and stored in original columns for scoring engine.
    monitored_weight is permanently set to 0.
    """
    # Validate raw values are 1-10
    for name, val in [
        ("age_raw", data.age_raw),
        ("size_raw", data.size_raw),
        ("rating_raw", data.rating_raw),
        ("quality_raw", data.quality_raw),
        ("watched_raw", data.watched_raw),
    ]:
        if val < 1 or val > 10:
            raise HTTPException(status_code=400, detail=f"{name} must be between 1 and 10")

    if data.age_max_days < 1 or data.age_max_days > 3650:
        raise HTTPException(status_code=400, detail="Age max days must be between 1 and 3650")

    if data.size_max_gb < 1 or data.size_max_gb > 10000:
        raise HTTPException(status_code=400, detail="Size max GB must be between 1 and 10000")

    # Calculate weights independently (slider 1-10 maps to 2-20%)
    # Slider 10 = 20%, Slider 1 = 2%
    age_weight = int(round((data.age_raw / 10) * 20))
    size_weight = int(round((data.size_raw / 10) * 20))
    rating_weight = int(round((data.rating_raw / 10) * 20))
    quality_weight = int(round((data.quality_raw / 10) * 20))
    watched_weight = int(round((data.watched_raw / 10) * 20))

    # Ensure minimum 1% and maximum 20%
    age_weight = max(1, min(20, age_weight))
    size_weight = max(1, min(20, size_weight))
    rating_weight = max(1, min(20, rating_weight))
    quality_weight = max(1, min(20, quality_weight))
    watched_weight = max(1, min(20, watched_weight))

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE scoring_weights SET
                age_raw = ?, size_raw = ?, rating_raw = ?, quality_raw = ?, watched_raw = ?,
                age_weight = ?, size_weight = ?, rating_weight = ?, quality_weight = ?, watched_weight = ?,
                monitored_weight = 0,
                age_max_days = ?, size_max_gb = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (data.age_raw, data.size_raw, data.rating_raw, data.quality_raw, data.watched_raw,
             age_weight, size_weight, rating_weight, quality_weight, watched_weight,
             data.age_max_days, data.size_max_gb)
        )

        # Save protection_days to settings table
        conn.execute(
            "UPDATE settings SET protection_days = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (data.protection_days,)
        )

        conn.commit()
        logger.info(f"Scoring weights saved (raw: age={data.age_raw}, size={data.size_raw}, rating={data.rating_raw}, quality={data.quality_raw}, watched={data.watched_raw})")
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
            "deletions_per_day": 0,
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
    
    # Validate deletions_per_day (0 = unlimited, 1-100 = max per day)
    if data.deletions_per_day < 0 or data.deletions_per_day > 100:
        raise HTTPException(status_code=400, detail="Deletions per day must be between 0 and 100")
    
    if data.min_score_threshold < 0 or data.min_score_threshold > 100:
        raise HTTPException(status_code=400, detail="Minimum score threshold must be between 0 and 100")

    is_valid, error = validate_delete_after_days(data.delete_after_days)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE settings SET
                enabled = ?, score_cron = ?, cull_cron = ?,
                max_queued = ?, deletions_per_day = ?, delete_after_days = ?,
                collection_grouping = ?, min_score_threshold = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (1 if data.enabled else 0, data.score_cron, data.cull_cron,
            data.max_queued, data.deletions_per_day, data.delete_after_days,
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
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            raise HTTPException(status_code=400, detail="Radarr not configured")

        client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, _ = await client.test_connection()
        if not radarr_ok:
            raise HTTPException(status_code=400, detail="Cannot connect to Radarr")

        movies = await client.get_movies()

        ages = []
        sizes = []

        for movie in movies:
            movie_file = movie.get("movieFile")
            if not movie_file:
                continue

            size_gb = movie_file.get("size", 0) / (1024 ** 3)
            if size_gb > 0:
                sizes.append(size_gb)

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

        # ===== ADD DEBUG HERE =====
        logger.info(f"RECALIBRATE DEBUG: Size samples collected: {len(sizes)}")
        if sizes:
            logger.info(f"RECALIBRATE DEBUG: Min size: {min(sizes):.2f} GB")
            logger.info(f"RECALIBRATE DEBUG: Max size: {max(sizes):.2f} GB")
            logger.info(f"RECALIBRATE DEBUG: 95th percentile raw = {percentile(sizes, 95):.2f} GB")
        # ===== END DEBUG =====

        age_max_days = int(percentile(ages, 90) * 1.5)
        size_max_gb = float(percentile(sizes, 95))

        # ===== ADD DEBUG HERE =====
        logger.info(f"RECALIBRATE DEBUG: Raw calculation - age_max_days={age_max_days}, size_max_gb={size_max_gb:.2f}")
        # ===== END DEBUG =====

        age_max_days = max(30, min(age_max_days, 3650))
        size_max_gb = max(5, min(size_max_gb, 1000))

        # Log the bounded values for debugging
        logger.info(f"Recalibration result - age_max_days: {age_max_days}, size_max_gb: {size_max_gb:.1f}")

        # ===== ADD DEBUG HERE =====
        logger.info(f"RECALIBRATE DEBUG: After bounds - age_max_days={age_max_days}, size_max_gb={size_max_gb:.2f}")
        current = conn.execute("SELECT age_max_days, size_max_gb FROM scoring_weights WHERE id = 1").fetchone()
        logger.info(f"RECALIBRATE DEBUG: Current DB before update - age={current[0]}, size={current[1]}")
        # ===== END DEBUG =====

        conn.execute(
            """UPDATE scoring_weights SET
                age_max_days = ?, size_max_gb = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (age_max_days, size_max_gb)
        )
        conn.commit()

        # ===== ADD DEBUG HERE =====
        new_current = conn.execute("SELECT age_max_days, size_max_gb FROM scoring_weights WHERE id = 1").fetchone()
        logger.info(f"RECALIBRATE DEBUG: After UPDATE - age={new_current[0]}, size={new_current[1]}")
        # ===== END DEBUG =====

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
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            return {"movie": None, "error": "Radarr not configured"}

        plex_config = conn.execute("SELECT enabled, url, api_key FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])

        client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        radarr_ok, _ = await client.test_connection()
        if not radarr_ok:
            return {"movie": None, "error": "Cannot connect to Radarr"}

        movies = await client.get_movies()

        preview_movie = None
        for movie in movies:
            if movie.get("movieFile"):
                preview_movie = movie
                break

        if not preview_movie:
            return {"movie": None, "error": "No movies with files found"}

        plex_play_counts = None
        if plex_enabled:
            from app.core.plex_client import PlexClient
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await plex_client.test_connection()
            if ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()

        engine = ScoringEngine(conn)
        
        original_weights = {
            "age_weight": engine.age_weight,
            "size_weight": engine.size_weight,
            "rating_weight": engine.rating_weight,
            "quality_weight": engine.quality_weight,
            "watched_weight": engine.watched_weight,
            "age_max_days": engine.age_max_days,
            "size_max_gb": engine.size_max_gb,
            "protection_days": engine.protection_days,
        }
        
        try:
            # Map slider values (1-10) to weights (2-20%)
            age_raw = weights_data.get("age_raw", 5)
            size_raw = weights_data.get("size_raw", 5)
            rating_raw = weights_data.get("rating_raw", 5)
            quality_raw = weights_data.get("quality_raw", 5)
            watched_raw = weights_data.get("watched_raw", 5)

            engine.age_weight = (age_raw / 10) * 0.20
            engine.size_weight = (size_raw / 10) * 0.20
            engine.rating_weight = (rating_raw / 10) * 0.20
            engine.quality_weight = (quality_raw / 10) * 0.20
            engine.watched_weight = (watched_raw / 10) * 0.20

            # Load advanced settings from database
            advanced = conn.execute("SELECT age_max_days, size_max_gb FROM scoring_weights WHERE id = 1").fetchone()
            if advanced:
                engine.age_max_days = advanced["age_max_days"]
                engine.size_max_gb = advanced["size_max_gb"]
            else:
                engine.age_max_days = weights_data.get("age_max_days", 365)
                engine.size_max_gb = weights_data.get("size_max_gb", 100)
            
            # Load protection_days from settings table separately
            settings_row = conn.execute("SELECT protection_days FROM settings WHERE id = 1").fetchone()
            if settings_row:
                engine.protection_days = settings_row["protection_days"]
            else:
                engine.protection_days = weights_data.get("protection_days", 30)

            engine.monitored_weight = 0.0
            
            result = engine.calculate_movie_score(preview_movie, plex_play_counts, plex_enabled)
            
            if not result.get("eligible"):
                return {"movie": None, "error": "Selected movie has no file"}
            
            # Apply penalty to the raw score 
            boosted_raw = apply_score_penalty(result.get("score", 0)) 
            
            return {
                "movie": {
                    "movie_title": preview_movie.get("title"),
                    "movie_year": preview_movie.get("year"),
                    "size_gb": result.get("size_gb", 0),
                    "age_days": result.get("age_days", 0),
                    "raw_score": boosted_raw,
                    "factors": result.get("factors", []),
                }
            }
        finally:
            engine.age_weight = original_weights["age_weight"]
            engine.size_weight = original_weights["size_weight"]
            engine.rating_weight = original_weights["rating_weight"]
            engine.quality_weight = original_weights["quality_weight"]
            engine.watched_weight = original_weights["watched_weight"]
            engine.age_max_days = original_weights["age_max_days"]
            engine.size_max_gb = original_weights["size_max_gb"]
            engine.protection_days = original_weights["protection_days"]

    except Exception as e:
        logger.error(f"Preview failed: {e}")
        return {"movie": None, "error": str(e)}
    finally:
        conn.close()
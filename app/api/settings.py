from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_connection
from app.utils.logger import get_logger
from app.utils.validators import validate_cron, validate_delete_after_days, validate_max_queued
from app.core.scheduler import update_score_schedule, update_cull_schedule

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
    Uses cached movie data for instant preview - no Radarr/Plex API calls.
    """
    from app.core.scoring_engine import QUALITY_SCORES
    from app.db.database import get_connection
    import math

    conn = get_connection()
    try:
        # Get a cached movie with all its data
        cached_movie = conn.execute("""
            SELECT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                   size_gb, age_days, quality, monitored, plex_play_count,
                   raw_score, factors
            FROM scored_movies_cache
            LIMIT 1
        """).fetchone()

        if not cached_movie:
            return {"movie": None, "error": "No cached movies found. Run a score cycle first."}

        # Get advanced settings from database
        advanced = conn.execute("SELECT age_max_days, size_max_gb FROM scoring_weights WHERE id = 1").fetchone()
        age_max_days = advanced["age_max_days"] if advanced else 365
        size_max_gb = advanced["size_max_gb"] if advanced else 100

        # Get protection days from settings
        settings = conn.execute("SELECT protection_days FROM settings WHERE id = 1").fetchone()
        protection_days = settings["protection_days"] if settings else 30

        # Map slider values (1-10) to weights (2-20%)
        age_raw = weights_data.get("age_raw", 5)
        size_raw = weights_data.get("size_raw", 5)
        rating_raw = weights_data.get("rating_raw", 5)
        quality_raw = weights_data.get("quality_raw", 5)
        watched_raw = weights_data.get("watched_raw", 5)

        age_weight = (age_raw / 10) * 0.20
        size_weight = (size_raw / 10) * 0.20
        rating_weight = (rating_raw / 10) * 0.20
        quality_weight = (quality_raw / 10) * 0.20
        watched_weight = (watched_raw / 10) * 0.20

        # Calculate each factor using the movie's cached data
        age_days = cached_movie["age_days"] or 0
        size_gb = cached_movie["size_gb"] or 0
        tmdb_rating = cached_movie["tmdb_rating"] or 5.0
        quality = cached_movie["quality"] or "Unknown"
        plex_play_count = cached_movie["plex_play_count"] or 0

        # Age factor (with protection)
        effective_age = age_days if age_days >= protection_days else 0
        age_raw_score = min((effective_age / age_max_days) ** 0.5, 1.0)

        # Size factor
        size_raw_score = min((size_gb / size_max_gb) ** 0.7, 1.0)

        # Rating factor (reverse sigmoid)
        rating_normalized = tmdb_rating / 10.0
        steepness = 10.0
        rating_raw_score = 1.0 / (1.0 + math.exp(steepness * (rating_normalized - 0.50)))

        # Quality factor
        quality_lower = quality.lower() if quality else ""
        if "2160p" in quality_lower or "4k" in quality_lower:
            quality_raw_score = 0.0
        elif "1080p" in quality_lower:
            quality_raw_score = 0.3
        elif "720p" in quality_lower:
            quality_raw_score = 0.6
        elif "dvd" in quality_lower:
            quality_raw_score = 0.9
        elif "sd" in quality_lower:
            quality_raw_score = 1.0
        else:
            quality_raw_score = 0.5

        # Watched factor (using cached plex_play_count)
        if plex_play_count == 0:
            # Unwatched - use age-based grace period
            if age_days < protection_days:
                watched_raw_score = 0.0
            elif age_days < protection_days + 730:
                progress = (age_days - protection_days) / 730
                watched_raw_score = progress
            else:
                watched_raw_score = 1.0
        else:
            # Watched - stepped score based on play count
            play_scores = {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.4, 4: 0.3, 5: 0.25, 6: 0.2, 7: 0.15, 8: 0.1, 9: 0.05}
            play_score = play_scores.get(min(plex_play_count, 9), 0.0)
            watched_raw_score = play_score  # Simplified (no recency for preview)

        # Calculate contributions
        age_contrib = age_raw_score * age_weight
        size_contrib = size_raw_score * size_weight
        rating_contrib = rating_raw_score * rating_weight
        quality_contrib = quality_raw_score * quality_weight
        watched_contrib = watched_raw_score * watched_weight

        raw_score = age_contrib + size_contrib + rating_contrib + quality_contrib + watched_contrib
        normalized_score = raw_score * 100

        # Build factors for display
        factors = [
            {"name": "Age", "contribution": age_contrib, "details": f"{age_days} days" + (f" (protected: {protection_days} days)" if age_days < protection_days else "")},
            {"name": "Size", "contribution": size_contrib, "details": f"{size_gb:.1f} GB"},
            {"name": "Rating", "contribution": rating_contrib, "details": f"{tmdb_rating:.1f}/10"},
            {"name": "Quality", "contribution": quality_contrib, "details": quality},
            {"name": "Watched", "contribution": watched_contrib, "details": f"Play count: {plex_play_count}" + (f" (protected)" if age_days < protection_days and plex_play_count == 0 else "")},
        ]

        return {
            "movie": {
                "movie_title": cached_movie["movie_title"],
                "movie_year": cached_movie["movie_year"],
                "size_gb": size_gb,
                "age_days": age_days,
                "raw_score": raw_score,
                "factors": factors,
            }
        }

    except Exception as e:
        logger.error(f"Preview failed: {e}")
        return {"movie": None, "error": str(e)}
    finally:
        conn.close()
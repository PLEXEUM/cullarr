import json
import math
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from app.utils.logger import get_logger

logger = get_logger()

# Quality to score mapping (0 = keep, 1 = delete priority)
QUALITY_SCORES = {
    "4K": 0.0,
    "2160p": 0.0,
    "Bluray-2160p": 0.01,
    "Bluray-4K": 0.03,
    "WEBDL-2160p": 0.05,
    "WEB-DL-2160p": 0.07,
    "WEBrip-2160p": 0.09,
    "1440p": 0.20,
    "Bluray-1440p": 0.21,
    "WEBDL-1440p": 0.25,
    "WEBrip-1440p": 0.29,
    "1080p": 0.3,
    "Bluray-1080p": 0.37,
    "BluRay-1080p": 0.33,
    "WEBDL-1080p": 0.35,
    "WEB-DL-1080p": 0.37,
    "WEBrip-1080p": 0.39,
    "720p": 0.5,
    "Bluray-720p": 0.51,
    "BluRay-720p": 0.53,
    "WEBDL-720p": 0.55,
    "WEB-DL-720p": 0.57,
    "WEBrip-720p": 0.59,
    "480p": 0.70,       
    "Bluray-480p": 0.71,
    "WEBDL-480p": 0.75,
    "WEBrip-480p": 0.79,
    "DVD": 0.9,
    "DVD-Rip": 0.9,
    "DVDRip": 0.9,
    "SD": 1.0,
    "Unknown": 0.9,
}


def get_quality_score(quality_name: str) -> float:
    """Get score for quality name (0-1, higher = more deletable)."""
    if not quality_name:
        return 0.5
    if quality_name in QUALITY_SCORES:
        return QUALITY_SCORES[quality_name]
    for key, score in QUALITY_SCORES.items():
        if quality_name.lower() == key.lower():
            return score
    q_lower = quality_name.lower()
    if "2160p" in q_lower or "4k" in q_lower:
        return 0.0
    elif "1080p" in q_lower:
        return 0.3
    elif "720p" in q_lower:
        return 0.6
    elif "dvd" in q_lower:
        return 0.9
    elif "sd" in q_lower:
        return 1.0
    return 0.5


# Play count scoring lookup (stepped)
PLAY_SCORES = {
    0: 1.00, 1: 0.80, 2: 0.60, 3: 0.40, 4: 0.30,
    5: 0.25, 6: 0.20, 7: 0.15, 8: 0.10, 9: 0.05,
}

def get_play_score(play_count: int) -> float:
    """Get play count score from stepped lookup. 10+ plays = 0.0."""
    return PLAY_SCORES.get(min(play_count, 9), 0.0)


def get_watched_score(play_count: int, last_viewed_timestamp: int = 0) -> float:
    """
    Combined watch score based on play count AND recency.
    Returns score from 0.0 (protected) to 1.0 (deletable).
    
    Play count scoring: Stepped - 0=1.0, 1=0.8, 2=0.6, 3=0.4, 4=0.3, 
    5=0.25, 6=0.2, 7=0.15, 8=0.1, 9=0.05, 10+=0.0
    Recency scoring: S-Curve (^0.7) on days since last watch
    Final score = play_score * recency_score (both factors contribute)
    """
    import time
    from datetime import datetime
    
    # Calculate play count score with stepped lookup
    play_score = get_play_score(play_count)
    
    # Calculate recency score with S-Curve
    recency_score = 1.0  # Default to deletable if no data
    days_since_last_watch = None
    watched_max_days = 730  # 2 years (configurable)

    if last_viewed_timestamp and last_viewed_timestamp > 0:
        current_time = int(time.time())
        days_since_last_watch = (current_time - last_viewed_timestamp) / 86400
    
        # S-Curve on recency: 0 days=0.0, 365 days=0.62, 730 days=1.0
        recency_raw = days_since_last_watch / watched_max_days
        recency_score = min(recency_raw ** 0.7, 1.0)
    else:
        days_since_last_watch = None  # No watch history
    
    # Combined score (multiplicative) - both factors contribute
    final_score = play_score * recency_score
    
    # Store recency info for display
    final_score_details = {
        "score": final_score,
        "play_count": play_count,
        "play_score": play_score,
        "days_since_last_watch": days_since_last_watch,
        "recency_score": recency_score
    }
    
    return final_score_details

def apply_score_penalty(raw_score: float) -> float:
    """
    Apply tiered penalty to boost bad movies.
    raw_score is 0-1 scale (raw score from factors).
    Returns boosted score 0-1.
    
    Tier mapping:
    - Best movies (0-0.20): No penalty
    - Good movies (0.21-0.40): +5% boost
    - Average movies (0.41-0.60): +10% boost
    - Bad movies (0.61-0.80): +15% boost
    - Worst movies (0.81-1.00): +20% boost (capped at 1.0)
    """
    if raw_score <= 0.20:
        return raw_score
    elif raw_score <= 0.40:
        return min(1.0, raw_score + 0.05)
    elif raw_score <= 0.60:
        return min(1.0, raw_score + 0.10)
    elif raw_score <= 0.80:
        return min(1.0, raw_score + 0.15)
    else:
        return 1.0  # Cap at 1.0 for worst movies

def extract_collection(movie: Dict) -> Optional[Tuple[int, str]]:
    """
    Extract TMDB collection ID and name from a Radarr movie object.
    Returns (collection_tmdb_id, collection_title) or None if not in a collection.
    """
    collection = movie.get("collection")
    if not collection:
        return None
    collection_id = collection.get("tmdbId")
    collection_title = collection.get("title", "Unknown Collection")
    if collection_id:
        return (int(collection_id), collection_title)
    return None


class ScoringEngine:
    def __init__(self, conn):
        self.conn = conn
        self._load_weights()
        self._load_settings()

    def _load_weights(self):
        """Load scoring weights from database."""
        weights = self.conn.execute(
            "SELECT * FROM scoring_weights WHERE id = 1"
        ).fetchone()

        if weights:
            self.age_weight = weights["age_weight"] / 100.0
            self.size_weight = weights["size_weight"] / 100.0
            self.rating_weight = weights["rating_weight"] / 100.0
            self.quality_weight = weights["quality_weight"] / 100.0
            # monitored_weight is permanently disabled (set to 0)
            self.monitored_weight = 0.0
            self.watched_weight = weights["watched_weight"] / 100.0
            self.age_max_days = weights["age_max_days"]
            self.size_max_gb = weights["size_max_gb"]
        else:
            self.age_weight = 0.25
            self.size_weight = 0.25
            self.rating_weight = 0.15
            self.quality_weight = 0.15
            self.monitored_weight = 0.0  # permanently disabled
            self.watched_weight = 0.10
            self.age_max_days = 365
            self.size_max_gb = 100

        self.raw_weights = {
            "age": int(self.age_weight * 100),
            "size": int(self.size_weight * 100),
            "rating": int(self.rating_weight * 100),
            "quality": int(self.quality_weight * 100),
            # "monitored" removed from UI weights
            "watched": int(self.watched_weight * 100),
        }

    def _load_settings(self):
        """Load settings (protection days, collection grouping)."""
        settings = self.conn.execute(
            "SELECT protection_days, collection_grouping FROM settings WHERE id = 1"
        ).fetchone()
        if settings:
            self.protection_days = settings["protection_days"]
            self.collection_grouping = bool(settings["collection_grouping"])
        else:
            self.protection_days = 30
            self.collection_grouping = False

    def reload_config(self):
        """Reload weights and settings from database."""
        self._load_weights()
        self._load_settings()

    def calculate_movie_score(
        self,
        movie: Dict,
        plex_play_counts: Optional[Dict] = None,
        plex_enabled: bool = False
    ) -> Dict[str, Any]:
        """Calculate score for a single movie with factor breakdown."""
        movie_file = movie.get("movieFile", {})
        if not movie_file:
            return {"score": 0, "eligible": False, "reason": "No file", "factors": []}

        size_gb = movie_file.get("size", 0) / (1024 ** 3)

        # Age calculation - Get date from movie file
        added_str = None
        
        # First try to get dateAdded from movieFile
        if movie_file:
            added_str = movie_file.get("dateAdded")
        
        # Fallback to movie root level if not found
        if not added_str:
            added_str = movie.get("added") or movie.get("addedDate") or movie.get("dateAdded")
        
        # DEBUG: Log first movie's date to verify
        if not hasattr(self, '_debug_shown'):
            logger.debug(f"DEBUG - Movie: {movie.get('title')}")
            logger.debug(f"DEBUG - added_str: '{added_str}'")
            logger.debug(f"DEBUG - movie_file keys: {list(movie_file.keys()) if movie_file else 'No movie_file'}")
            self._debug_shown = True

        if added_str:
            try:
                # Handle ISO format with Zulu timezone
                added_str_clean = added_str.replace("Z", "+00:00")
                added = datetime.fromisoformat(added_str_clean)
                # Remove timezone info to make it naive for subtraction
                if added.tzinfo is not None:
                    added = added.replace(tzinfo=None)
                age_days = (datetime.now() - added).days
            except Exception as e:
                logger.warning(f"Failed to parse date '{added_str}' for {movie.get('title')}: {e}")
                age_days = 0
        else:
            age_days = 0

        # Apply protection penalty (age = 0 if within protection window)
        effective_age_raw = age_days
        if age_days < self.protection_days:
            effective_age_raw = 0

        # Size scoring: stepped, sensitive in 0-5GB range
        def get_size_score(size_gb: float) -> float:
            """Stepped size scoring based on file size."""
            if size_gb <= 0:
                return 0.0
            elif size_gb <= 1.5:
                return 0.00
            elif size_gb <= 2.5:
                return 0.20
            elif size_gb <= 3.5:
                return 0.40
            elif size_gb <= 5.0:
                return 0.60
            elif size_gb <= 10.0:
                return 0.80
            else:
                return 1.00

        size_raw = get_size_score(size_gb)

        # TMDB rating (0-10, lower rating = higher deletion score)
        # Reverse sigmoid: high ratings → low raw, low ratings → high raw
        tmdb_rating = movie.get("ratings", {}).get("tmdb", {}).get("value") or movie.get("tmdbRating") or 5.0
        rating_normalized = tmdb_rating / 10.0
        steepness = 12.0  # More aggressive than previous 8.0
        rating_raw = 1.0 / (1.0 + math.exp(steepness * (rating_normalized - 0.45)))
        # 3.0 = 0.94, 4.0 = 0.79, 5.0 = 0.45, 6.0 = 0.16, 7.0 = 0.04

        # Quality
        current_quality = "Unknown"
        file_quality_wrapper = movie_file.get("quality", {})
        if isinstance(file_quality_wrapper, dict):
            file_quality_obj = file_quality_wrapper.get("quality", {})
            if isinstance(file_quality_obj, dict):
                current_quality = file_quality_obj.get("name", "Unknown")
        quality_raw = get_quality_score(current_quality)

        # Monitored status - PERMANENTLY DISABLED (weight set to 0 in _load_weights)
        # The code below is commented out because monitored no longer affects scores
        # monitored = movie.get("monitored", True)
        # monitored_raw = 0.0

        # ===== FIX: Check Plex watch history FIRST, then apply protection =====
        # Watched status (from Plex) — includes play count AND recency
        watched_raw = 0.0
        play_count = 0
        watched_details = None

        # Step 1: Get Plex watch history if available (regardless of protection status)
        if plex_enabled and plex_play_counts:
            tmdb_id = movie.get("tmdbId") or movie.get("tmdb_id")
            if tmdb_id and str(tmdb_id) in plex_play_counts:
                play_count = plex_play_counts[str(tmdb_id)].get("play_count", 0)
                last_viewed = plex_play_counts[str(tmdb_id)].get("last_viewed", 0)
                watched_result = get_watched_score(play_count, last_viewed)
                watched_raw = watched_result["score"]
                watched_details = watched_result
                # Store the actual play count for display
                watched_details["play_count"] = play_count

        # Step 2: Apply protection (overrides watched_raw but preserves details for display)
        if age_days < self.protection_days:
            # Movie was added recently — Watched score = 0 (protected)
            watched_raw = 0.0
            if watched_details is None:
                # No Plex history, create basic protected details
                watched_details = {
                    "score": 0.0,
                    "play_count": 0,
                    "protected": True,
                    "protection_days": self.protection_days
                }
            else:
                # Add protection info to existing details
                watched_details["protected"] = True
                watched_details["protection_days"] = self.protection_days
                watched_details["score"] = 0.0  # Override score to 0 for protection
        # ===== END FIX =====

        # Calculate contributions (monitored removed)
        age_contrib = age_raw * self.age_weight
        size_contrib = size_raw * self.size_weight
        rating_contrib = rating_raw * self.rating_weight
        quality_contrib = quality_raw * self.quality_weight
        # monitored_contrib removed (always 0)
        watched_contrib = watched_raw * self.watched_weight

        raw_score = (
            age_contrib + size_contrib + rating_contrib +
            quality_contrib + watched_contrib  # monitored removed
        )

        # Build factor breakdown (monitored factor removed)
        factors = [
            {
                "name": "Age", "key": "age", "raw_score": age_raw,
                "weight": self.raw_weights["age"], "contribution": age_contrib,
                "details": f"{age_days} days" + (
                    f" (protected: {self.protection_days} days)" if age_days < self.protection_days else ""
                )
            },
            {
                "name": "Size", "key": "size", "raw_score": size_raw,
                "weight": self.raw_weights["size"], "contribution": size_contrib,
                "details": f"{size_gb:.1f} GB"
            },
            {
                "name": "Rating", "key": "rating", "raw_score": rating_raw,
                "weight": self.raw_weights["rating"], "contribution": rating_contrib,
                "details": f"{tmdb_rating}/10"
            },
            {
                "name": "Quality", "key": "quality", "raw_score": quality_raw,
                "weight": self.raw_weights["quality"], "contribution": quality_contrib,
                "details": current_quality
            },
            {
                "name": "Watched", "key": "watched", "raw_score": watched_raw,
                "weight": self.raw_weights["watched"], "contribution": watched_contrib,
                "details": self._get_watched_details(play_count, watched_details, plex_enabled, age_days, self.protection_days),
                "skipped": not plex_enabled,
                "skip_reason": "Plex not configured" if not plex_enabled else None,
            },
        ]

        return {
            "score": raw_score,
            "eligible": True,
            "size_gb": size_gb,
            "age_days": age_days,
            "tmdb_rating": tmdb_rating,
            "quality": current_quality,
            "monitored": movie.get("monitored", True),
            "tmdb_id": movie.get("tmdbId") or movie.get("tmdb_id"),
            "factors": factors,
            "watched_details": watched_details,
            "plex_play_count": play_count,
        }
    
    def _get_watched_details(self, play_count: int, watched_details: dict, plex_enabled: bool, age_days: int, protection_days: int) -> str:
        """Generate human-readable watched factor details."""
        if not plex_enabled:
            return "Play count: N/A (Plex disabled)"
    
        # Build the base details
        if play_count == 0:
            # Check if we have protected status but no play count
            if watched_details and watched_details.get("protected"):
                details = "Never watched"
            else:
                details = "Never watched"
        else:
            details = f"Play count: {play_count}"
            if watched_details and watched_details.get("days_since_last_watch") is not None:
                days = watched_details["days_since_last_watch"]
                if days < 1:
                    details += " | Last watched: Today"
                elif days == 1:
                    details += " | Last watched: Yesterday"
                else:
                    details += f" | Last watched: {int(days)} days ago"
    
        # Add protection info if within protection window
        if age_days < protection_days:
            details += f" (protected: {protection_days} days)"
    
        return details

    def normalize_scores(self, scored_movies: List[Dict]) -> List[Dict]:
        """Normalize raw scores to 0-100 scale."""
        if not scored_movies:
            return []

        max_score = max(m.get("raw_score", 0) for m in scored_movies)
        if max_score == 0:
            for m in scored_movies:
                m["normalized_score"] = 0
        else:
            for m in scored_movies:
                m["normalized_score"] = (m["raw_score"] / max_score) * 100

        return scored_movies

    def group_into_collections(self, scored_movies: List[Dict]) -> List[Dict]:
        """
        Group individually scored movies into collection groups.

        Each collection becomes a single entry in the returned list with:
        - collection_score: average raw score of all movies in the collection
        - movies: list of all individual movie entries in the group
        - size_gb: total combined size of all movies in the collection
        - is_collection: True flag so callers can distinguish groups from singles

        Movies not in any collection are returned as-is with is_collection: False.
        Only collections where ALL movies have files are grouped — if any movie
        in the collection is missing a file it is treated as an individual.
        """
        # Separate into collection buckets and standalone movies
        collection_buckets: Dict[int, List[Dict]] = {}
        standalone: List[Dict] = []

        for movie in scored_movies:
            coll = movie.get("collection")
            if coll:
                coll_id = coll[0]
                if coll_id not in collection_buckets:
                    collection_buckets[coll_id] = []
                collection_buckets[coll_id].append(movie)
            else:
                standalone.append(movie)

        result = list(standalone)

        for coll_id, movies in collection_buckets.items():
            coll_title = movies[0]["collection"][1]
            avg_score = sum(m["raw_score"] for m in movies) / len(movies)
            total_size = sum(m["size_gb"] for m in movies)
            oldest_age = max(m["age_days"] for m in movies)

            result.append({
                "is_collection": True,
                "collection_id": coll_id,
                "collection_title": coll_title,
                "movie_title": coll_title,
                "movie_year": None,
                "movies": movies,
                "movie_count": len(movies),
                "raw_score": avg_score,
                "normalized_score": avg_score * 100,  # raw × 100 for display
                "size_gb": total_size,
                "age_days": oldest_age,
                "tmdb_rating": sum(m["tmdb_rating"] for m in movies) / len(movies),
                "quality": movies[0]["quality"],
                "monitored": all(m["monitored"] for m in movies),
                "factors": movies[0]["factors"],  # representative factors from first movie
                "plex_play_count": sum(m.get("plex_play_count", 0) for m in movies),
                "poster_url": movies[0].get("poster_url"),
            })

        return result

    def get_scored_movies(
        self,
        movies: List[Dict],
        plex_play_counts: Optional[Dict] = None,
        plex_enabled: bool = False
    ) -> List[Dict]:
        """
        Calculate scores for all movies and return sorted list.
        If collection_grouping is enabled, collections are grouped into
        single entries scored by their average and counted as one queue slot.
        """
        scored = []

        for movie in movies:
            result = self.calculate_movie_score(movie, plex_play_counts, plex_enabled)
            if result["eligible"]:
                # Extract collection info from Radarr movie object
                collection = extract_collection(movie)

                scored.append({
                    "movie_id": movie.get("id"),
                    "movie_title": movie.get("title"),
                    "movie_year": movie.get("year"),
                    "tmdb_id": result.get("tmdb_id"),
                    "tmdb_rating": result["tmdb_rating"],
                    "size_gb": result["size_gb"],
                    "age_days": result["age_days"],
                    "quality": result["quality"],
                    "monitored": result["monitored"],
                    "raw_score": result["score"],
                    "factors": result["factors"],
                    "collection": collection,
                    "is_collection": False,
                    "plex_play_count": result.get("plex_play_count", 0),
                    "poster_url": movie.get("remotePoster"),
                })

        # Sort by raw score (highest first)
        scored.sort(key=lambda x: x["raw_score"], reverse=True)

        # Group into collections if enabled
        if self.collection_grouping:
            scored = self.group_into_collections(scored)
            # Re-sort after grouping since collection avg scores may differ
            scored.sort(key=lambda x: x["raw_score"], reverse=True)

        # Convert raw scores (0-1) to 0-100 scale (raw_score * 100)
        # No normalization against library max
        # Apply penalty to boost bad movies
        for movie in scored:
            movie["raw_score_original"] = movie["raw_score"] 
            boosted_raw = apply_score_penalty(movie["raw_score"])  # ← Penalty applied
            movie["score"] = boosted_raw * 100
            movie["normalized_score"] = boosted_raw * 100

            # For collections, ensure individual movies keep their original scores
            if movie.get("is_collection") and movie.get("movies"):
                for member in movie["movies"]:
                    boosted_member_raw = apply_score_penalty(member["raw_score"])
                    member["score"] = boosted_member_raw * 100
                    member["normalized_score"] = boosted_member_raw * 100
                    member["raw_score_original"] = member["raw_score"] 
                    member["raw_score"] = boosted_member_raw

        return scored
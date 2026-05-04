from fastapi import APIRouter, HTTPException, Query
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.utils.logger import get_logger
from plexapi.server import PlexServer
import json
import asyncio

router = APIRouter()
logger = get_logger()


@router.get("/dashboard/queue-status")
async def get_queue_status():
    """Get queue status and system health."""
    conn = get_connection()
    try:
        # Count slots used — each unique collection = 1 slot, each individual = 1 slot
        queue_stats = conn.execute("""
            SELECT
                COUNT(*) as total_movies,
                COUNT(DISTINCT CASE WHEN collection_name IS NOT NULL THEN collection_name END) as collection_slots,
                COUNT(CASE WHEN collection_name IS NULL THEN 1 END) as individual_slots
            FROM scheduled_deletions
            WHERE status = 'scheduled'
        """).fetchone()

         # Safe default if no results
        if queue_stats is None:
            queue_stats = {"total_movies": 0, "collection_slots": 0, "individual_slots": 0}

        settings = conn.execute("SELECT max_queued FROM settings WHERE id = 1").fetchone()
        max_queued = settings["max_queued"] if settings else 20

        collection_slots = queue_stats["collection_slots"] if queue_stats else 0
        individual_slots = queue_stats["individual_slots"] if queue_stats else 0
        scheduled_count = collection_slots + individual_slots
        total_movies = queue_stats["total_movies"] if queue_stats else 0

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
        "total_movies": total_movies,
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
    """
    Get scheduled deletions queue.
    Collections are grouped into single entries with a movies list.
    """
    conn = get_connection()
    try:
        scheduled = conn.execute("""
            SELECT id, movie_id, movie_title, movie_year, size_gb, quality,
                   score, scheduled_date, status, collection_name
            FROM scheduled_deletions
            WHERE status = 'scheduled'
            ORDER BY collection_name ASC, scheduled_date ASC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    # Group collection members together for display
    collections: dict = {}
    individuals: list = []

    for row in scheduled:
        item = dict(row)
        if item["collection_name"]:
            cname = item["collection_name"]
            if cname not in collections:
                collections[cname] = {
                    "is_collection": True,
                    "collection_name": cname,
                    "movie_title": cname,
                    "movie_id": None,
                    "movie_year": None,
                    "year_min": None,
                    "score": item["score"],
                    "scheduled_date": item["scheduled_date"],
                    "status": item["status"],
                    "movies": [],
                    "size_gb": 0.0,
                }
            
            # Set movie_id from the first movie in the collection
            if collections[cname]["movie_id"] is None and item.get("movie_id"):
                collections[cname]["movie_id"] = item["movie_id"]
            
            # Track earliest year in collection
            if item["movie_year"]:
                if collections[cname]["year_min"] is None or item["movie_year"] < collections[cname]["year_min"]:
                    collections[cname]["year_min"] = item["movie_year"]
            
            collections[cname]["movies"].append(item)
            collections[cname]["size_gb"] += item["size_gb"] or 0.0
        else:
            item["is_collection"] = False
            individuals.append(item)
    
    # After the loop, set movie_year for collections from year_min
    for cname in collections:
        if collections[cname].get("year_min"):
            collections[cname]["movie_year"] = collections[cname]["year_min"]
        # Clean up temporary field
        if "year_min" in collections[cname]:
            del collections[cname]["year_min"]

    items = individuals + list(collections.values())
    items.sort(key=lambda x: x["scheduled_date"])

    return {
        "items": items,
        "count": len(items),
        "total_movies": len(scheduled),
    }


@router.delete("/dashboard/scheduled/{movie_id}")
async def remove_from_queue(movie_id: int):
    """
    Remove a movie from the scheduled deletions queue.
    If the movie is part of a collection, removes all members of that collection.
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, movie_title, collection_name FROM scheduled_deletions WHERE movie_id = ? AND status = 'scheduled'",
            (movie_id,)
        ).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Movie not found in queue")

        if existing["collection_name"]:
            # Remove all members of the collection together
            collection_name = existing["collection_name"]
            
            # ===== FIRST: Remove all movies in collection from Plex =====
            try:
                # Get Plex config
                plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
                if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
                    # Get all movies in this collection
                    collection_members = conn.execute(
                        "SELECT movie_title, movie_year FROM scheduled_deletions WHERE collection_name = ? AND status = 'scheduled'",
                        (collection_name,)
                    ).fetchall()
                    
                    if collection_members:
                        plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                        collection_key = plex_config["collection_key"]
                        
                        # Get library map for title|year lookups
                        library_items = await plex_client.get_library_items()
                        library_map = {}
                        for item in library_items:
                            title = item.get("title")
                            year = item.get("year")
                            rating_key = item.get("rating_key")
                            if title and year and rating_key:
                                key = f"{title.lower()}|{year}"
                                library_map[key] = rating_key
                        
                        # Remove each movie from Plex collection
                        for member in collection_members:
                            movie_title = member["movie_title"]
                            movie_year = member["movie_year"]
                            lookup_key = f"{movie_title.lower()}|{movie_year}"
                            rating_key = library_map.get(lookup_key)
                            if rating_key:
                                success = await plex_client.remove_from_collection(collection_key, rating_key)
                                if success:
                                    logger.info(f"Removed '{movie_title}' from Plex collection for collection '{collection_name}'")
                                else:
                                    logger.warning(f"Failed to remove '{movie_title}' from Plex collection")
                            else:
                                logger.debug(f"Could not find rating key for '{movie_title} ({movie_year})'")
            except Exception as plex_error:
                logger.warning(f"Failed to remove collection movies from Plex: {plex_error}")
            # ===== END PLEX CLEANUP =====
            
            # Then delete all members from scheduled_deletions
            members = conn.execute(
                "SELECT movie_title FROM scheduled_deletions WHERE collection_name = ? AND status = 'scheduled'",
                (collection_name,)
            ).fetchall()
            conn.execute(
                "DELETE FROM scheduled_deletions WHERE collection_name = ? AND status = 'scheduled'",
                (collection_name,)
            )
            titles = [m["movie_title"] for m in members]
            conn.commit()
            logger.info(f"Removed collection '{collection_name}' from queue ({len(titles)} movies)")
            return {
                "success": True,
                "message": f"Removed collection '{collection_name}' ({len(titles)} movies) from queue"
            }
        else:
            # ===== FIRST: Remove from Plex collection (BEFORE deleting from queue) =====
            try:
                # Get Plex config
                plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
                if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
                    # Get movie title and year to find rating key by title/year lookup
                    movie_data = conn.execute(
                        "SELECT movie_title, movie_year FROM scheduled_deletions WHERE movie_id = ? AND status = 'scheduled'",
                        (movie_id,)
                    ).fetchone()
            
                    if movie_data:
                        movie_title = movie_data["movie_title"]
                        movie_year = movie_data["movie_year"]
                        collection_key = plex_config["collection_key"]
                        plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                
                        # Get library map to find rating key by title|year
                        library_items = await plex_client.get_library_items()
                        library_map = {}
                        for item in library_items:
                            title = item.get("title")
                            year = item.get("year")
                            rating_key = item.get("rating_key")
                            if title and year and rating_key:
                                key = f"{title.lower()}|{year}"
                                library_map[key] = rating_key
                
                        lookup_key = f"{movie_title.lower()}|{movie_year}"
                        rating_key = library_map.get(lookup_key)
                
                        if rating_key:
                            # Use the new READ-MODIFY-WRITE remove method
                            success = await plex_client.remove_from_collection(collection_key, rating_key)
                            if success:
                                logger.info(f"Manually removed '{movie_title}' from Plex collection 'Movies Leaving Soon'")
                            else:
                                logger.warning(f"Failed to remove '{movie_title}' from Plex collection")
                        else:
                            logger.debug(f"Could not find rating key for '{movie_title} ({movie_year})', skipping Plex removal")
                    else:
                        logger.debug(f"No movie data found for movie_id {movie_id}, skipping Plex removal")
    
            except Exception as plex_error:
                logger.warning(f"Failed to remove from Plex collection for manually removed movie: {plex_error}")
            # ===== END PLEX CLEANUP =====
    
            # THEN delete from the queue
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
    refresh: bool = False,
    sort_by: str = Query("score", description="Sort column: score, title, year, age, size, rating, quality, watched"),
    sort_order: str = Query("desc", description="Sort order: asc or desc")
) -> dict:
    """
    Get scored movies from cache. If refresh=True or cache is empty,
    triggers a live fetch from Radarr and rebuilds the cache.
    """
    conn = get_connection()
    try:
        cache_count = conn.execute(
            "SELECT COUNT(*) as count FROM scored_movies_cache"
        ).fetchone()
        has_cache = cache_count and cache_count["count"] > 0
        
        # Check if Plex is enabled but cache has no play counts
        plex_config = conn.execute("SELECT enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"]) if plex_config else False
        
        play_counts_exist = False
        if has_cache and plex_enabled:
            # Check if any cache entries have play counts > 0
            play_count_check = conn.execute(
                "SELECT COUNT(*) as count FROM scored_movies_cache WHERE plex_play_count > 0"
            ).fetchone()
            play_counts_exist = play_count_check and play_count_check["count"] > 0
        
        # Rebuild if: refresh requested, OR no cache, OR (Plex enabled but no play counts in cache)
        needs_rebuild = refresh or not has_cache or (plex_enabled and not play_counts_exist)
        
    except Exception:
        has_cache = False
        needs_rebuild = True
    finally:
        conn.close()

    if needs_rebuild:
        await _rebuild_score_cache()

    return await _get_score_queue_from_cache(page, per_page, sort_by, sort_order)


async def _rebuild_score_cache():
    """Fetch movies from Radarr, score them, and write to cache table with transaction safety."""
    conn = get_connection()
    try:
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            logger.warning("Radarr not configured, cannot rebuild score cache")
            return

        plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"])
        
        logger.info(f"Plex enabled: {plex_enabled}")
        if plex_enabled:
            logger.info(f"Plex URL: {plex_config['url']}")
            logger.info(f"Plex API Key exists: {bool(plex_config['api_key'])}")
        
        settings = conn.execute("SELECT protection_days, collection_grouping FROM settings WHERE id = 1").fetchone()
        if settings is None:
            settings = {"protection_days": 30, "collection_grouping": 0}
    finally:
        conn.close()

    try:
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        movies = await radarr_client.get_movies()

        plex_play_counts = None
        if plex_enabled:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, connection_msg = await plex_client.test_connection()
            logger.info(f"Plex connection test: {ok} - {connection_msg}")
            if ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()
                logger.info(f"Fetched {len(plex_play_counts)} play counts from Plex")
                sample_items = list(plex_play_counts.items())[:5]
                for tmdb_id, data in sample_items:
                    logger.info(f"Sample play count - TMDb: {tmdb_id}, plays: {data.get('play_count', 0)}")
            else:
                logger.warning(f"Plex connection failed: {connection_msg}")
        else:
            logger.info("Plex not enabled, skipping play counts")

        # ===== WRAP HEAVY DB OPERATIONS IN THREAD POOL =====
        def _rebuild_db_operations():
            conn = get_connection()
            try:
                # Start transaction
                conn.execute("BEGIN TRANSACTION")
                
                engine = ScoringEngine(conn)
                if settings:
                    engine.protection_days = settings["protection_days"]
                    engine.collection_grouping = bool(settings["collection_grouping"])

                scored = engine.get_scored_movies(movies, plex_play_counts, plex_enabled)

                # Clear old cache
                conn.execute("DELETE FROM scored_movies_cache")

                for entry in scored:
                    if entry.get("is_collection"):
                        for member in entry.get("movies", []):
                            play_count = 0
                            if plex_play_counts and member.get("tmdb_id"):
                                plex_entry = plex_play_counts.get(str(member["tmdb_id"]))
                                if plex_entry:
                                    play_count = plex_entry.get("play_count", 0)

                            conn.execute("""
                                INSERT INTO scored_movies_cache
                                (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                                 size_gb, age_days, quality, monitored, normalized_score,
                                 raw_score, factors, plex_play_count,
                                 collection_name, collection_id, is_collection, cached_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                            """, (
                                member["movie_id"],
                                member["movie_title"],
                                member["movie_year"],
                                member.get("tmdb_id"),
                                member["tmdb_rating"],
                                member["size_gb"],
                                member["age_days"],
                                member["quality"],
                                1 if member["monitored"] else 0,
                                entry["normalized_score"],
                                entry["raw_score"],
                                json.dumps(member["factors"]),
                                play_count,
                                entry.get("collection_title"),
                                entry.get("collection_id"),
                            ))
                    else:
                        play_count = 0
                        if plex_play_counts and entry.get("tmdb_id"):
                            plex_entry = plex_play_counts.get(str(entry["tmdb_id"]))
                            if plex_entry:
                                play_count = plex_entry.get("play_count", 0)

                        conn.execute("""
                            INSERT INTO scored_movies_cache
                            (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                             size_gb, age_days, quality, monitored, normalized_score,
                             raw_score, factors, plex_play_count,
                             collection_name, collection_id, is_collection, cached_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                        """, (
                            entry["movie_id"],
                            entry["movie_title"],
                            entry["movie_year"],
                            entry.get("tmdb_id"),
                            entry["tmdb_rating"],
                            entry["size_gb"],
                            entry["age_days"],
                            entry["quality"],
                            1 if entry["monitored"] else 0,
                            entry["normalized_score"],
                            entry["raw_score"],
                            json.dumps(entry["factors"]),
                            play_count,
                            None,
                            None,
                        ))

                conn.commit()
                return len(scored)
                
            except Exception as e:
                conn.execute("ROLLBACK")
                logger.error(f"Failed to rebuild score cache, transaction rolled back: {e}")
                raise
            finally:
                conn.close()

        # Run DB operations in thread pool to prevent blocking
        entry_count = await asyncio.to_thread(_rebuild_db_operations)
        logger.info(f"Score cache rebuilt with {entry_count} entries")
        # ===== END THREAD POOL WRAPPER =====

    except Exception as e:
        logger.error(f"Failed to rebuild score cache: {e}")


async def _get_score_queue_from_cache(page: int, per_page: int, sort_by: str = "score", sort_order: str = "desc") -> dict:
    """
    Read paginated score queue from cache, excluding already-scheduled movies.
    Collections are grouped into single entries for display.
    """
    conn = get_connection()
    try:
        # Check if Plex is enabled
        plex_config = conn.execute("SELECT enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"]) if plex_config else False
        
        scheduled_ids = conn.execute(
            "SELECT movie_id FROM scheduled_deletions"
        ).fetchall()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}

        all_cached = conn.execute("""
            SELECT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                   size_gb, age_days, quality, monitored, normalized_score,
                   raw_score, factors, plex_play_count,
                   collection_name, collection_id, is_collection, cached_at
            FROM scored_movies_cache
        """).fetchall()
    finally:
        conn.close()

    # Group collections and filter out already-scheduled entries
    collections: dict = {}
    individuals: list = []

    for row in all_cached:
        item = dict(row)
        try:
            item["factors"] = json.loads(item["factors"]) if item["factors"] else []
        except Exception:
            item["factors"] = []

        if item["movie_id"] in scheduled_id_set:
            continue

        if item["is_collection"] and item["collection_name"]:
            cname = item["collection_name"]
            if cname not in collections:
                collections[cname] = {
                    "is_collection": True,
                    "collection_name": cname,
                    "collection_id": item["collection_id"],
                    "movie_title": cname,
                    "movie_year": None,
                    "year_min": None, 
                    "tmdb_rating_sum": 0.0, 
                    "normalized_score": item["normalized_score"],
                    "raw_score": item["raw_score"],
                    "size_gb": 0.0,
                    "age_days": 0,
                    "tmdb_rating": 0.0,
                    "quality": item["quality"],
                    "movies": [],
                    "movie_count": 0,
                    "plex_play_count": 0,
                }
            
            if item["movie_year"]:
                if collections[cname]["year_min"] is None or item["movie_year"] < collections[cname]["year_min"]:
                    collections[cname]["year_min"] = item["movie_year"]

            collections[cname]["movies"].append(item)
            collections[cname]["movie_count"] += 1
            collections[cname]["size_gb"] += item["size_gb"] or 0.0
            collections[cname]["age_days"] = max(
                collections[cname]["age_days"], item["age_days"] or 0
            )
            collections[cname]["tmdb_rating_sum"] += (item["tmdb_rating"] or 0.0)
            collections[cname]["plex_play_count"] = (collections[cname].get("plex_play_count") or 0) + (item["plex_play_count"] or 0)
        else:
            individuals.append(item)

    # After building all collections, set movie_year and calculate final rating average
    for cname in collections:
        if collections[cname].get("year_min"):
            collections[cname]["movie_year"] = collections[cname]["year_min"]
        # Calculate final TMDB rating
        if collections[cname]["movie_count"] > 0:
            collections[cname]["tmdb_rating"] = collections[cname]["tmdb_rating_sum"] / collections[cname]["movie_count"]
        # Clean up temporary fields
        if "year_min" in collections[cname]:
            del collections[cname]["year_min"]
        if "tmdb_rating_sum" in collections[cname]:
            del collections[cname]["tmdb_rating_sum"]
    
    available = individuals + list(collections.values())
    
    # Apply sorting — using raw_score instead of normalized_score
    sort_mapping = {
        "score": "raw_score",  # ← CHANGED: was "normalized_score"
        "title": "movie_title",
        "year": "movie_year",
        "age": "age_days",
        "size": "size_gb",
        "rating": "tmdb_rating",
        "quality": "quality",
        "watched": "plex_play_count",
    }
    
    sort_column = sort_mapping.get(sort_by, "raw_score")
    reverse = sort_order.lower() == "desc"
    
    # Handle None values for sorting - safely compare mixed types
    def get_sort_key(x):
        val = x.get(sort_column)
        # For numeric columns - convert None to 0
        if sort_column in ["raw_score", "normalized_score", "age_days", "size_gb", "tmdb_rating", "plex_play_count"]:
            return val if isinstance(val, (int, float)) else 0
        # For string columns - convert None to empty string
        return str(val) if val is not None else ""

    available.sort(key=get_sort_key, reverse=reverse)

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
        "plex_enabled": plex_enabled,
    }

@router.get("/dashboard/score-queue/search")
async def search_score_queue(
    q: str = Query("", min_length=1, max_length=100, description="Search query"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("score", description="Sort column: score, title, year, age, size, rating, quality, watched"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    refresh: bool = False  # <-- ADD THIS LINE
):
    """
    Search scored movies cache by title or collection name.
    Returns paginated results matching the search query.
    """
    conn = get_connection()
    try:
        # Check if Plex is enabled
        plex_config = conn.execute("SELECT enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"]) if plex_config else False
        
        # Rebuild cache if refresh=True
        if refresh:
            # Run cache rebuild in background or synchronously
            await _rebuild_score_cache()

        # Get scheduled movie IDs to exclude
        scheduled_ids = conn.execute(
            "SELECT movie_id FROM scheduled_deletions"
        ).fetchall()
        scheduled_id_set = {row["movie_id"] for row in scheduled_ids}
        
        # Build search query - search in movie_title and collection_name
        search_term = f"%{q.lower()}%"
        
        # First, get all matching movies (for count and pagination)
        # We need to handle collections properly - a collection matches if any member matches
        matching_movies = conn.execute("""
            SELECT DISTINCT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                   size_gb, age_days, quality, monitored, normalized_score,
                   raw_score, factors, plex_play_count,
                   collection_name, collection_id, is_collection
            FROM scored_movies_cache
            WHERE LOWER(movie_title) LIKE ? 
               OR LOWER(collection_name) LIKE ?
            ORDER BY 
                CASE WHEN ? = 'score' THEN normalized_score END {sort_order},
                CASE WHEN ? = 'title' THEN movie_title END {sort_order},
                CASE WHEN ? = 'year' THEN movie_year END {sort_order},
                CASE WHEN ? = 'age' THEN age_days END {sort_order},
                CASE WHEN ? = 'size' THEN size_gb END {sort_order},
                CASE WHEN ? = 'rating' THEN tmdb_rating END {sort_order},
                CASE WHEN ? = 'quality' THEN quality END {sort_order},
                CASE WHEN ? = 'watched' THEN plex_play_count END {sort_order}
        """.format(sort_order="ASC" if sort_order.lower() == "asc" else "DESC"),
            (search_term, search_term, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by)
        ).fetchall()
        
        # Filter out already-scheduled movies and group collections
        collections: dict = {}
        individuals: list = []
        
        for row in matching_movies:
            item = dict(row)
            item["factors"] = json.loads(item["factors"]) if item["factors"] else []
            
            # Skip if already scheduled
            if item["movie_id"] in scheduled_id_set:
                continue
            
            if item["is_collection"] and item["collection_name"]:
                cname = item["collection_name"]
                if cname not in collections:
                    collections[cname] = {
                        "is_collection": True,
                        "collection_name": cname,
                        "collection_id": item["collection_id"],
                        "movie_title": cname,
                        "movie_year": None,
                        "year_min": None,
                        "tmdb_rating_sum": 0.0,
                        "normalized_score": item["normalized_score"],
                        "raw_score": item["raw_score"],
                        "size_gb": 0.0,
                        "age_days": 0,
                        "tmdb_rating": 0.0,
                        "quality": item["quality"],
                        "movies": [],
                        "movie_count": 0,
                        "plex_play_count": 0,
                    }
                
                # Track earliest year (min)
                if item["movie_year"]:
                    if collections[cname]["year_min"] is None or item["movie_year"] < collections[cname]["year_min"]:
                        collections[cname]["year_min"] = item["movie_year"]

                collections[cname]["movies"].append(item)
                collections[cname]["movie_count"] += 1
                collections[cname]["size_gb"] += item["size_gb"] or 0.0
                collections[cname]["age_days"] = max(
                    collections[cname]["age_days"], item["age_days"] or 0
                )
                collections[cname]["tmdb_rating_sum"] += (item["tmdb_rating"] or 0.0)
                
                # Aggregate play count (sum of all members)
                collections[cname]["plex_play_count"] += item["plex_play_count"] or 0
            else:
                individuals.append(item)

        # After building all collections, set movie_year and calculate final rating average
        for cname in collections:
            if collections[cname].get("year_min"):
                collections[cname]["movie_year"] = collections[cname]["year_min"]
            # Calculate final TMDB rating
            if collections[cname]["movie_count"] > 0:
                collections[cname]["tmdb_rating"] = collections[cname]["tmdb_rating_sum"] / collections[cname]["movie_count"]
            # Clean up temporary fields
            if "year_min" in collections[cname]:
                del collections[cname]["year_min"]
            if "tmdb_rating_sum" in collections[cname]:
                del collections[cname]["tmdb_rating_sum"]
        
        # Merge and sort
        available = individuals + list(collections.values())
        
        # Apply sorting
        sort_mapping = {
            "score": "normalized_score",
            "title": "movie_title",
            "year": "movie_year",
            "age": "age_days",
            "size": "size_gb",
            "rating": "tmdb_rating",
            "quality": "quality",
            "watched": "plex_play_count",
        }
        sort_column = sort_mapping.get(sort_by, "normalized_score")
        reverse = sort_order.lower() == "desc"
        available.sort(key=lambda x: x.get(sort_column) or (0 if isinstance(x.get(sort_column), (int, float)) else ""), reverse=reverse)
        
        # Paginate
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
            "plex_enabled": plex_enabled,
            "search_query": q,
        }
        
    except Exception as e:
        logger.error(f"Search score queue failed: {e}")
        raise HTTPException(status_code=500, detail="Search failed")
    finally:
        conn.close()


@router.get("/dashboard/failed")
async def get_failed_deletions():
    """Get deletion history (both successful and failed deletions)."""
    conn = get_connection()
    try:
        history = conn.execute("""
            SELECT id, movie_title, movie_year, size_gb, score, status, error_message, deleted_at
            FROM deletion_history
            ORDER BY deleted_at DESC
            LIMIT 50
        """).fetchall()
    finally:
        conn.close()
    return {"items": [dict(row) for row in history]}


@router.delete("/dashboard/failed")
async def clear_failed_deletions():
    """Clear all deletion history records (both successful and failed)."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM deletion_history")
        conn.commit()
        logger.info("Cleared all deletion history records")
        return {"success": True, "message": "Deletion history cleared"}
    except Exception as e:
        logger.error(f"Failed to clear deletion history: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear records")
    finally:
        conn.close()


@router.get("/dashboard/settings-summary")
async def get_settings_summary():
    """Get summary of current settings for dashboard display."""
    conn = get_connection()
    try:
        settings = conn.execute("""
            SELECT delete_after_days, protection_days, collection_grouping, max_queued, deletions_per_day
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
        "deletions_per_day": settings["deletions_per_day"] if settings else 0,
        "weights": dict(weights) if weights else {},
    }
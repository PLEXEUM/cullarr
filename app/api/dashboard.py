from fastapi import APIRouter, HTTPException, Query
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.core.plex_client import PlexClient
from app.core.scoring_engine import ScoringEngine
from app.utils.logger import get_logger
from plexapi.server import PlexServer
import json
import asyncio
from typing import Optional
from app.core.run_engine import _apply_plex_collections

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
            FROM scored_movies_cache
            WHERE scheduled_for_deletion = 1
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

        # Get count of manually queued movies
        manual_count = conn.execute("""
            SELECT COUNT(*) as count 
            FROM scored_movies_cache 
            WHERE scheduled_for_deletion = 1 AND manual_for_deletion = 1
        """).fetchone()["count"]

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
        "manual_count": manual_count,
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


@router.delete("/dashboard/scheduled/clear")
async def clear_all_scheduled_deletions():
    """
    Clear all scheduled deletions (remove all movies from the queue).
    """
    logger.info("=== CLEAR ALL ENDPOINT REACHED ===")
    conn = get_connection()
    try:
        logger.info("Fetching scheduled movies...")
        # Get Plex config for collection cleanup
        plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        # Get all scheduled movies to remove from Plex collections
        scheduled_movies = conn.execute(
            """SELECT movie_id, movie_title, movie_year, collection_name 
               FROM scored_movies_cache 
               WHERE scheduled_for_deletion = 1"""
        ).fetchall()
        logger.info(f"Found {len(scheduled_movies)} scheduled movies to clear")  # ← ADD THIS
        
        # Remove from Plex collection if configured
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"] and scheduled_movies:
            try:
                plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                collection_key = plex_config["collection_key"]
                
                library_items = await plex_client.get_library_items()
                library_map = {}
                for item in library_items:
                    title = item.get("title")
                    year = item.get("year")
                    rating_key = item.get("rating_key")
                    if title and year and rating_key:
                        key = f"{title.lower()}|{year}"
                        library_map[key] = rating_key
                
                for movie in scheduled_movies:
                    lookup_key = f"{movie['movie_title'].lower()}|{movie['movie_year']}"
                    rating_key = library_map.get(lookup_key)
                    if rating_key:
                        await plex_client.remove_from_collection(collection_key, rating_key)
                        logger.info(f"Removed '{movie['movie_title']}' from Plex collection")
            except Exception as plex_error:
                logger.warning(f"Failed to remove from Plex collection during clear all: {plex_error}")
        
        # Clear all scheduled flags
        logger.info("Executing database clear...")  # ← ADD THIS
        conn.execute(
            """UPDATE scored_movies_cache 
               SET scheduled_for_deletion = 0, scheduled_date = NULL, manual_for_deletion = 0 
               WHERE scheduled_for_deletion = 1"""
        )
        conn.commit()
        
        logger.info(f"Cleared all scheduled deletions ({len(scheduled_movies)} movies)")
        return {"success": True, "message": f"Cleared {len(scheduled_movies)} movies from queue"}
        
    except Exception as e:
        logger.error(f"Failed to clear scheduled deletions: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear scheduled deletions")
    finally:
        conn.close()


@router.delete("/dashboard/scheduled/{movie_id}")
async def remove_from_queue(movie_id: int):
    """
    Remove a movie from the scheduled deletions queue by clearing its scheduled flag.
    If the movie is part of a collection, removes all members of that collection.
    """
    conn = get_connection()
    try:
        # Check if movie exists in cache and is scheduled
        existing = conn.execute(
            """SELECT movie_id, movie_title, collection_name 
               FROM scored_movies_cache 
               WHERE movie_id = ? AND scheduled_for_deletion = 1""",
            (movie_id,)
        ).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Movie not found in scheduled queue")

        # Get Plex config for collection cleanup
        plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        if existing["collection_name"]:
            # Remove all members of the collection
            collection_name = existing["collection_name"]
            
            # Get all movies in this collection
            collection_members = conn.execute(
                """SELECT movie_id, movie_title, movie_year 
                   FROM scored_movies_cache 
                   WHERE collection_name = ? AND scheduled_for_deletion = 1""",
                (collection_name,)
            ).fetchall()
            
            # Remove from Plex collection if configured
            if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
                try:
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
                    
                    for member in collection_members:
                        lookup_key = f"{member['movie_title'].lower()}|{member['movie_year']}"
                        rating_key = library_map.get(lookup_key)
                        if rating_key:
                            await plex_client.remove_from_collection(collection_key, rating_key)
                            logger.info(f"Removed '{member['movie_title']}' from Plex collection")
                except Exception as plex_error:
                    logger.warning(f"Failed to remove from Plex collection: {plex_error}")
            
            # Clear scheduled flags for all collection members
            conn.execute(
                """UPDATE scored_movies_cache 
                   SET scheduled_for_deletion = 0, scheduled_date = NULL 
                   WHERE collection_name = ? AND scheduled_for_deletion = 1""",
                (collection_name,)
            )
            conn.commit()
            
            logger.info(f"Removed collection '{collection_name}' from queue ({len(collection_members)} movies)")
            return {
                "success": True,
                "message": f"Removed collection '{collection_name}' ({len(collection_members)} movies) from queue"
            }
        else:
            # Remove single movie
            # Remove from Plex collection if configured
            if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
                try:
                    # Get movie details
                    movie_data = conn.execute(
                        "SELECT movie_title, movie_year FROM scored_movies_cache WHERE movie_id = ?",
                        (movie_id,)
                    ).fetchone()
                    
                    if movie_data:
                        plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                        collection_key = plex_config["collection_key"]
                        
                        # Get library map
                        library_items = await plex_client.get_library_items()
                        library_map = {}
                        for item in library_items:
                            title = item.get("title")
                            year = item.get("year")
                            rating_key = item.get("rating_key")
                            if title and year and rating_key:
                                key = f"{title.lower()}|{year}"
                                library_map[key] = rating_key
                        
                        lookup_key = f"{movie_data['movie_title'].lower()}|{movie_data['movie_year']}"
                        rating_key = library_map.get(lookup_key)
                        if rating_key:
                            await plex_client.remove_from_collection(collection_key, rating_key)
                            logger.info(f"Removed '{movie_data['movie_title']}' from Plex collection")
                except Exception as plex_error:
                    logger.warning(f"Failed to remove from Plex collection: {plex_error}")
            
            # Clear scheduled flag and manual flag
            conn.execute(
                """UPDATE scored_movies_cache 
                SET scheduled_for_deletion = 0, scheduled_date = NULL, manual_for_deletion = 0 
                WHERE movie_id = ?""",
                (movie_id,)
            )
            conn.commit()
            
            logger.info(f"Removed '{existing['movie_title']}' from scheduled queue")
            return {"success": True, "message": f"Removed {existing['movie_title']} from queue"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove movie {movie_id} from queue: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove movie from queue")
    finally:
        conn.close()


@router.post("/dashboard/scheduled/{movie_id}")
async def manually_queue_movie(movie_id: int):
    """
    Manually add a movie to scheduled deletions queue.
    Bypasses all protection rules - user override.
    """
    from app.core.radarr_client import RadarrClient
    from app.core.plex_client import PlexClient
    from app.core.scoring_engine import ScoringEngine
    from datetime import datetime, timedelta
    import json
    
    conn = get_connection()
    try:
        # Get settings and configs
        settings = conn.execute("SELECT delete_after_days, collection_grouping FROM settings WHERE id = 1").fetchone()
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            raise HTTPException(status_code=400, detail="Radarr not configured")
        
        delete_after_days = settings["delete_after_days"] if settings else 7
        collection_grouping = bool(settings["collection_grouping"]) if settings else False
        
        # Fetch movie from cache first, fallback to Radarr live
        movie_data = conn.execute(
            "SELECT * FROM scored_movies_cache WHERE movie_id = ?",
            (movie_id,)
        ).fetchone()
        
        # Get movie details from Radarr if not in cache
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        movie = await radarr_client.get_movie(movie_id)
        
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found in Radarr")
        
        movie_title = movie.get("title", "Unknown")
        movie_year = movie.get("year")
        tmdb_id = movie.get("tmdbId") or movie.get("tmdb_id")
        
        # Check if this is a collection
        is_collection = False
        collection_movies = []
        collection_name = None
        collection_id = None
        
        if collection_grouping:
            # Check if movie is part of a collection
            collection_info = None
            from app.core.scoring_engine import extract_collection
            collection_info = extract_collection(movie)
            
            if collection_info:
                collection_id, collection_name = collection_info
                is_collection = True
                
                # Get all movies in this collection from Radarr
                all_movies = await radarr_client.get_movies()
                for m in all_movies:
                    m_collection = extract_collection(m)
                    if m_collection and m_collection[0] == collection_id:
                        collection_movies.append(m)
                
                if not collection_movies:
                    collection_movies = [movie]
                    is_collection = False
            else:
                collection_movies = [movie]
        else:
            collection_movies = [movie]
        
        # Calculate scheduled date
        scheduled_date = datetime.now() + timedelta(days=delete_after_days)
        scheduled_date_str = scheduled_date.isoformat()
        
        # Get or create cache entries for each movie
        for m in collection_movies:
            m_id = m.get("id")
            
            # Check if already in cache
            existing = conn.execute(
                "SELECT * FROM scored_movies_cache WHERE movie_id = ?",
                (m_id,)
            ).fetchone()
            
            if not existing:
                # Need to score this movie on the fly
                # Get Plex play counts if enabled
                plex_play_counts = None
                plex_enabled = False
                plex_client = None
                
                if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"]:
                    plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                    plex_ok, _ = await plex_client.test_connection()
                    if plex_ok:
                        plex_play_counts = await plex_client.get_play_counts_by_tmdb()
                        plex_enabled = True
                
                # Score the movie
                engine = ScoringEngine(conn)
                score_result = engine.calculate_movie_score(m, plex_play_counts, plex_enabled)
                
                # Get quality string
                movie_file = m.get("movieFile", {})
                current_quality = "Unknown"
                if movie_file:
                    file_quality_wrapper = movie_file.get("quality", {})
                    if isinstance(file_quality_wrapper, dict):
                        file_quality_obj = file_quality_wrapper.get("quality", {})
                        if isinstance(file_quality_obj, dict):
                            current_quality = file_quality_obj.get("name", "Unknown")
                
                # Insert into cache
                conn.execute("""
                    INSERT OR REPLACE INTO scored_movies_cache
                    (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                     size_gb, age_days, quality, monitored, normalized_score,
                     raw_score, factors, plex_play_count,
                     collection_name, collection_id, is_collection,
                     scheduled_for_deletion, scheduled_date, cached_at, manual_for_deletion)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, 1)
                """, (
                    m_id,
                    m.get("title"),
                    m.get("year"),
                    tmdb_id,
                    score_result.get("tmdb_rating", 0),
                    score_result.get("size_gb", 0),
                    score_result.get("age_days", 0),
                    current_quality,
                    1 if m.get("monitored", True) else 0,
                    score_result.get("score", 0) * 100,  # normalized_score
                    score_result.get("score", 0),  # raw_score
                    json.dumps(score_result.get("factors", [])),
                    score_result.get("plex_play_count", 0),
                    collection_name if is_collection else None,
                    collection_id if is_collection else None,
                    1 if is_collection else 0,
                    scheduled_date_str
                ))
            else:
                # Update existing cache entry
                conn.execute("""
                    UPDATE scored_movies_cache 
                    SET scheduled_for_deletion = 1, 
                        scheduled_date = ?,
                        manual_for_deletion = 1
                    WHERE movie_id = ?
                """, (scheduled_date_str, m_id))
        
        conn.commit()
        
        # Add to Plex collection if configured
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
            try:
                # Build library map and add to collection
                plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                plex_ok, _ = await plex_client.test_connection()
                if plex_ok:
                    # Get library map for rating keys
                    library_items = await plex_client.get_library_items()
                    library_map = {}
                    for item in library_items:
                        title = item.get("title")
                        year = item.get("year")
                        rating_key = item.get("rating_key")
                        if title and year and rating_key:
                            key = f"{title.lower()}|{year}"
                            library_map[key] = rating_key
                    
                    # Prepare movie list for Plex collection function
                    movies_for_plex = []
                    for m in collection_movies:
                        movies_for_plex.append({
                            "movie_id": m.get("id"),
                            "movie_title": m.get("title"),
                            "movie_year": m.get("year")
                        })
                    
                    await _apply_plex_collections(plex_client, movies_for_plex, library_map)
                    logger.info(f"MANUAL: Added {len(collection_movies)} movies to Plex collection")
            except Exception as plex_error:
                logger.warning(f"Failed to add to Plex collection: {plex_error}")
        
        # Log the manual queue action
        if is_collection:
            logger.info(f"MANUAL: Queued collection '{collection_name}' with {len(collection_movies)} movies for deletion on {scheduled_date_str}")
        else:
            logger.info(f"MANUAL: Queued '{movie_title}' for deletion on {scheduled_date_str}")
        
        return {
            "success": True,
            "message": f"Queued {movie_title} for deletion on {scheduled_date.strftime('%Y-%m-%d')}",
            "scheduled_date": scheduled_date_str,
            "is_collection": is_collection,
            "movie_count": len(collection_movies)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to manually queue movie {movie_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue movie: {str(e)}")
    finally:
        conn.close()
            

@router.post("/dashboard/scheduled/collection/{collection_id}")
async def manually_queue_collection(collection_id: int):
    """
    Manually add an entire collection to scheduled deletions queue.
    Bypasses all protection rules - user override.
    """
    from app.core.radarr_client import RadarrClient
    from app.core.plex_client import PlexClient
    from app.core.scoring_engine import ScoringEngine, extract_collection
    from datetime import datetime, timedelta
    import json
    
    conn = get_connection()
    try:
        # Get settings and configs
        settings = conn.execute("SELECT delete_after_days, collection_grouping FROM settings WHERE id = 1").fetchone()
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        if not radarr_config or not radarr_config["url"] or not radarr_config["api_key"]:
            raise HTTPException(status_code=400, detail="Radarr not configured")
        
        if not settings or not settings["collection_grouping"]:
            raise HTTPException(status_code=400, detail="Collection grouping is disabled in settings")
        
        delete_after_days = settings["delete_after_days"] if settings else 7
        
        # Get all movies from Radarr
        radarr_client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
        all_movies = await radarr_client.get_movies()
        
        # Find all movies in this collection
        collection_movies = []
        collection_name = None
        
        for movie in all_movies:
            coll_info = extract_collection(movie)
            if coll_info and coll_info[0] == collection_id:
                collection_movies.append(movie)
                if not collection_name:
                    collection_name = coll_info[1]
        
        if not collection_movies:
            raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found or empty")
        
        # Calculate scheduled date
        scheduled_date = datetime.now() + timedelta(days=delete_after_days)
        scheduled_date_str = scheduled_date.isoformat()
        
        # Get Plex play counts if enabled
        plex_play_counts = None
        plex_enabled = False
        plex_client = None
        
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"]:
            plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
            plex_ok, _ = await plex_client.test_connection()
            if plex_ok:
                plex_play_counts = await plex_client.get_play_counts_by_tmdb()
                plex_enabled = True
        
        engine = ScoringEngine(conn)
        
        # Queue each movie in the collection
        for movie in collection_movies:
            m_id = movie.get("id")
            
            # Check if already in cache
            existing = conn.execute(
                "SELECT * FROM scored_movies_cache WHERE movie_id = ?",
                (m_id,)
            ).fetchone()
            
            if not existing:
                # Score the movie
                score_result = engine.calculate_movie_score(movie, plex_play_counts, plex_enabled)
                
                # Get quality string
                movie_file = movie.get("movieFile", {})
                current_quality = "Unknown"
                if movie_file:
                    file_quality_wrapper = movie_file.get("quality", {})
                    if isinstance(file_quality_wrapper, dict):
                        file_quality_obj = file_quality_wrapper.get("quality", {})
                        if isinstance(file_quality_obj, dict):
                            current_quality = file_quality_obj.get("name", "Unknown")
                
                tmdb_id = movie.get("tmdbId") or movie.get("tmdb_id")
                
                # Insert into cache
                conn.execute("""
                    INSERT OR REPLACE INTO scored_movies_cache
                    (movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                     size_gb, age_days, quality, monitored, normalized_score,
                     raw_score, factors, plex_play_count,
                     collection_name, collection_id, is_collection,
                     scheduled_for_deletion, scheduled_date, cached_at, manual_for_deletion)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, CURRENT_TIMESTAMP, 1)
                """, (
                    m_id,
                    movie.get("title"),
                    movie.get("year"),
                    tmdb_id,
                    score_result.get("tmdb_rating", 0),
                    score_result.get("size_gb", 0),
                    score_result.get("age_days", 0),
                    current_quality,
                    1 if movie.get("monitored", True) else 0,
                    score_result.get("score", 0) * 100,
                    score_result.get("score", 0),
                    json.dumps(score_result.get("factors", [])),
                    score_result.get("plex_play_count", 0),
                    collection_name,
                    collection_id,
                ))
            else:
                # Update existing cache entry
                conn.execute("""
                    UPDATE scored_movies_cache 
                    SET scheduled_for_deletion = 1, 
                        scheduled_date = ?,
                        manual_for_deletion = 1
                    WHERE movie_id = ?
                """, (scheduled_date_str, m_id))
        
        conn.commit()
        
        # Add to Plex collection if configured
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
            try:
                # Build library map and add to collection
                plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                plex_ok, _ = await plex_client.test_connection()
                if plex_ok:
                    library_items = await plex_client.get_library_items()
                    library_map = {}
                    for item in library_items:
                        title = item.get("title")
                        year = item.get("year")
                        rating_key = item.get("rating_key")
                        if title and year and rating_key:
                            key = f"{title.lower()}|{year}"
                            library_map[key] = rating_key
                    
                    movies_for_plex = []
                    for m in collection_movies:
                        movies_for_plex.append({
                            "movie_id": m.get("id"),
                            "movie_title": m.get("title"),
                            "movie_year": m.get("year")
                        })
                    
                    await _apply_plex_collections(plex_client, movies_for_plex, library_map)
                    logger.info(f"MANUAL: Added collection '{collection_name}' ({len(collection_movies)} movies) to Plex collection")
            except Exception as plex_error:
                logger.warning(f"Failed to add to Plex collection: {plex_error}")
        
        logger.info(f"MANUAL: Queued collection '{collection_name}' with {len(collection_movies)} movies for deletion on {scheduled_date_str}")
        
        return {
            "success": True,
            "message": f"Queued collection '{collection_name}' ({len(collection_movies)} movies) for deletion",
            "scheduled_date": scheduled_date_str,
            "is_collection": True,
            "movie_count": len(collection_movies)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to manually queue collection {collection_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue collection: {str(e)}")
    finally:
        conn.close()


@router.delete("/dashboard/scheduled/collection/{collection_id}")
async def remove_collection_by_id(collection_id: int):
    """
    Remove an entire collection from scheduled deletions by collection ID.
    """
    conn = get_connection()
    try:
        # First, get the collection name from the cache using collection_id
        collection_info = conn.execute(
            """SELECT DISTINCT collection_name 
               FROM scored_movies_cache 
               WHERE collection_id = ? AND scheduled_for_deletion = 1""",
            (collection_id,)
        ).fetchone()
        
        if not collection_info:
            raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found in scheduled queue")
        
        collection_name = collection_info["collection_name"]
        
        # Get all scheduled movies in this collection
        collection_members = conn.execute(
            """SELECT movie_id, movie_title, movie_year 
               FROM scored_movies_cache 
               WHERE collection_id = ? AND scheduled_for_deletion = 1""",
            (collection_id,)
        ).fetchall()
        
        if not collection_members:
            raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found in scheduled queue")
        
        # Get Plex config for collection cleanup
        plex_config = conn.execute("SELECT url, api_key, enabled, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        # Remove from Plex collection if configured
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"] and plex_config["collection_key"]:
            try:
                plex_client = PlexClient(plex_config["url"], plex_config["api_key"])
                collection_key = plex_config["collection_key"]
                
                library_items = await plex_client.get_library_items()
                library_map = {}
                for item in library_items:
                    title = item.get("title")
                    year = item.get("year")
                    rating_key = item.get("rating_key")
                    if title and year and rating_key:
                        key = f"{title.lower()}|{year}"
                        library_map[key] = rating_key
                
                for member in collection_members:
                    lookup_key = f"{member['movie_title'].lower()}|{member['movie_year']}"
                    rating_key = library_map.get(lookup_key)
                    if rating_key:
                        await plex_client.remove_from_collection(collection_key, rating_key)
                        logger.info(f"Removed '{member['movie_title']}' from Plex collection")
            except Exception as plex_error:
                logger.warning(f"Failed to remove from Plex collection: {plex_error}")
        
        # Clear scheduled flags for all collection members
        conn.execute(
            """UPDATE scored_movies_cache 
               SET scheduled_for_deletion = 0, scheduled_date = NULL, manual_for_deletion = 0 
               WHERE collection_id = ? AND scheduled_for_deletion = 1""",
            (collection_id,)
        )
        conn.commit()
        
        logger.info(f"Removed collection '{collection_name}' (ID: {collection_id}) from queue ({len(collection_members)} movies)")
        return {
            "success": True,
            "message": f"Removed collection '{collection_name}' ({len(collection_members)} movies) from queue"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove collection {collection_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove collection")
    finally:
        conn.close()


@router.get("/dashboard/score-queue")
async def get_score_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=500),
    sort_by: str = Query("score"),
    sort_order: str = Query("desc"),
    scheduled: Optional[int] = Query(None, ge=0, le=1, description="0=unscheduled, 1=scheduled, omit=all")
) -> dict:
    return await _get_score_queue_from_cache(page, per_page, sort_by, sort_order, scheduled)


async def _get_score_queue_from_cache(page: int, per_page: int, sort_by: str = "score", sort_order: str = "desc", scheduled: Optional[int] = None) -> dict:
    """
    Read paginated score queue from cache, excluding already-scheduled movies.
    Collections are grouped into single entries for display.
    """
    conn = get_connection()
    try:
        # Check if Plex is enabled
        plex_config = conn.execute("SELECT enabled FROM plex_config WHERE id = 1").fetchone()
        plex_enabled = bool(plex_config and plex_config["enabled"]) if plex_config else False
        
        # Apply scheduled filter only when specified (0 or 1)
        if scheduled == 0:
            where_clause = "WHERE scheduled_for_deletion = 0"
        elif scheduled == 1:
            where_clause = "WHERE scheduled_for_deletion = 1"
        else:
            where_clause = ""

        query = f"""
            SELECT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                    size_gb, age_days, quality, monitored, normalized_score,
                    raw_score, factors, plex_play_count,
                    collection_name, collection_id, is_collection, cached_at,
                    scheduled_for_deletion, scheduled_date, manual_for_deletion, poster_url, individual_normalized_score, individual_raw_score
            FROM scored_movies_cache
            {where_clause}
        """
        all_cached = conn.execute(query).fetchall()
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
                    "scheduled_for_deletion": False,
                    "manual_for_deletion": False,
                    "poster_url": None,
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
        
            # Track if any movie in collection is scheduled
            if item.get("scheduled_for_deletion"):
                collections[cname]["scheduled_for_deletion"] = True

            # Track if any movie in collection is manually queued
            if item.get("manual_for_deletion"):
                collections[cname]["manual_for_deletion"] = True

            # Track the earliest scheduled_date (or just any non-null date)
            if item.get("scheduled_date") and not collections[cname].get("scheduled_date"):
                collections[cname]["scheduled_date"] = item["scheduled_date"]
        
        else:
            individuals.append(item)

    # After building all collections, set movie_year and calculate final rating average
    for cname in collections:
        if collections[cname].get("year_min"):
            collections[cname]["movie_year"] = collections[cname]["year_min"]
        # Calculate final TMDB rating
        if collections[cname]["movie_count"] > 0:
            collections[cname]["tmdb_rating"] = collections[cname]["tmdb_rating_sum"] / collections[cname]["movie_count"]
         # Set poster_url from first movie in collection
        if collections[cname].get("movies") and len(collections[cname]["movies"]) > 0:
            collections[cname]["poster_url"] = collections[cname]["movies"][0].get("poster_url")
        # Clean up temporary fields
        if "year_min" in collections[cname]:
            del collections[cname]["year_min"]
        if "tmdb_rating_sum" in collections[cname]:
            del collections[cname]["tmdb_rating_sum"]
    
    available = individuals + list(collections.values())
    
    # For scheduled view (scheduled=1), sort by scheduled_date (soonest first)
    if scheduled == 1:
        # Sort by scheduled_date (soonest first), then by raw_score (highest first)
        available.sort(key=lambda x: (
            x.get("scheduled_date") is None,
            x.get("scheduled_date") or "9999-12-31",
            -x.get("raw_score", 0)  # Negative for descending order
        ))
    else:
        # Apply sorting — using raw_score instead of normalized_score
        sort_mapping = {
            "score": "raw_score",
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
        
        def get_sort_key(x):
            val = x.get(sort_column)
            if sort_column in ["raw_score", "normalized_score", "age_days", "size_gb", "tmdb_rating", "plex_play_count"]:
                return val if isinstance(val, (int, float)) else 0
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
        
        # Build search query - search in movie_title and collection_name
        search_term = f"%{q.lower()}%"
        
        # First, get all matching movies (for count and pagination)
        # We need to handle collections properly - a collection matches if any member matches
        # Check if collection grouping is enabled
        settings = conn.execute("SELECT collection_grouping FROM settings WHERE id = 1").fetchone()
        collection_grouping = bool(settings["collection_grouping"]) if settings else False

        search_term = f"%{q.lower()}%"

        if collection_grouping:
            # Enhanced: Return collection card if ANY movie in collection matches
            matching_movies = conn.execute("""
                SELECT DISTINCT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                    size_gb, age_days, quality, monitored, normalized_score,
                    raw_score, factors, plex_play_count,
                    collection_name, collection_id, is_collection, manual_for_deletion, 
                    scheduled_for_deletion, poster_url, individual_normalized_score, individual_raw_score
                FROM scored_movies_cache
                WHERE LOWER(movie_title) LIKE ? 
                OR LOWER(collection_name) LIKE ?
                OR collection_id IN (
                    SELECT DISTINCT collection_id 
                    FROM scored_movies_cache 
                    WHERE LOWER(movie_title) LIKE ? AND collection_id IS NOT NULL
                )
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
                (search_term, search_term, search_term, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by, sort_by)
            ).fetchall()
        else:
            # Simple: Return individual matches only (original behavior)
            matching_movies = conn.execute("""
                SELECT DISTINCT movie_id, movie_title, movie_year, tmdb_id, tmdb_rating,
                    size_gb, age_days, quality, monitored, normalized_score,
                    raw_score, factors, plex_play_count,
                    collection_name, collection_id, is_collection, manual_for_deletion, 
                    scheduled_for_deletion, poster_url, individual_normalized_score, individual_raw_score
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
                        "scheduled_for_deletion": False,
                        "manual_for_deletion": False,
                         "scheduled_date": None,
                         "poster_url": None,
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

                # Track if any movie in collection is scheduled
                if item.get("scheduled_for_deletion"):
                    collections[cname]["scheduled_for_deletion"] = True

                # Track if any movie in collection is manually queued
                if item.get("manual_for_deletion"):
                    collections[cname]["manual_for_deletion"] = True

                # Track the earliest scheduled_date (or just any non-null date)
                if item.get("scheduled_date") and not collections[cname].get("scheduled_date"):
                    collections[cname]["scheduled_date"] = item["scheduled_date"]

            else:
                individuals.append(item)

        # After building all collections, set movie_year and calculate final rating average
        for cname in collections:
            if collections[cname].get("year_min"):
                collections[cname]["movie_year"] = collections[cname]["year_min"]
            # Calculate final TMDB rating
            if collections[cname]["movie_count"] > 0:
                collections[cname]["tmdb_rating"] = collections[cname]["tmdb_rating_sum"] / collections[cname]["movie_count"]
            # Set poster_url from first movie in collection
            if collections[cname].get("movies") and len(collections[cname]["movies"]) > 0:
                collections[cname]["poster_url"] = collections[cname]["movies"][0].get("poster_url") 
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
            SELECT id, movie_title, movie_year, size_gb, score, status, error_message, deleted_at,
                   age_days, tmdb_rating, quality
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
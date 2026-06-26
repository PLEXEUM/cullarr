from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_connection
from app.core.plex_client import PlexClient
from app.utils.logger import get_logger
from app.utils.validators import validate_url

router = APIRouter()
logger = get_logger()


class PlexConfigInput(BaseModel):
    url: str
    collection_key: Optional[str] = None
    enabled: bool = False


@router.get("/plex/config")
async def get_plex_config():
    """Get current Plex configuration."""
    conn = get_connection()
    config = conn.execute("SELECT * FROM plex_config WHERE id = 1").fetchone()
    conn.close()

    if not config:
        return {"configured": False, "enabled": False}

    return {
        "configured": bool(config["url"] and config["api_key"]),
        "url": config["url"],
        "api_key": "[REDACTED]" if config["api_key"] else None,
        "collection_key": config["collection_key"],
        "enabled": bool(config["enabled"]),
    }


@router.post("/plex/config")
async def save_plex_config(data: PlexConfigInput):
    """Save Plex connection settings."""
    if data.url:
        is_valid, error = validate_url(data.url)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error)

    conn = get_connection()
    try:
        existing = conn.execute("SELECT api_key FROM plex_config WHERE id = 1").fetchone()
        
        if existing and existing["api_key"]:
            conn.execute(
                """UPDATE plex_config SET
                    url = ?, collection_key = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1""",
                (data.url.rstrip("/") if data.url else None, data.collection_key, 1 if data.enabled else 0)
            )
        else:
            conn.execute(
                """UPDATE plex_config SET
                    url = ?, collection_key = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1""",
                (data.url.rstrip("/") if data.url else None, data.collection_key, 1 if data.enabled else 0)
            )
        
        conn.commit()
        logger.info(f"Plex config saved (enabled: {data.enabled})")
        return {"success": True, "message": "Configuration saved"}
    except Exception as e:
        logger.error(f"Failed to save Plex config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    finally:
        conn.close()


@router.post("/plex/config/test")
async def test_plex_connection():
    """Test connection to Plex using stored token."""
    conn = get_connection()
    config = conn.execute("SELECT url, api_key FROM plex_config WHERE id = 1").fetchone()
    conn.close()

    if not config or not config["url"] or not config["api_key"]:
        raise HTTPException(status_code=400, detail="No Plex configuration found. Please authenticate with Plex first.")

    client = PlexClient(config["url"], config["api_key"])
    success, message = await client.test_connection()

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.delete("/plex/config")
async def clear_plex_config():
    """Clear Plex configuration."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE plex_config SET
                url = NULL, collection_key = NULL,
                enabled = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1"""
        )
        conn.commit()
        logger.info("Plex configuration cleared")
        return {"success": True, "message": "Configuration cleared"}
    except Exception as e:
        logger.error(f"Failed to clear Plex config: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear configuration")
    finally:
        conn.close()


@router.get("/plex/collections")
async def get_plex_collections():
    """Get all collections from ALL movie libraries in Plex."""
    conn = get_connection()
    config = conn.execute("SELECT url, api_key FROM plex_config WHERE id = 1").fetchone()
    conn.close()

    if not config or not config["url"] or not config["api_key"]:
        raise HTTPException(status_code=400, detail="Plex not configured. Please authenticate first.")

    from plexapi.server import PlexServer

    try:
        server = PlexServer(config["url"], config["api_key"])
        
        all_collections = []
        seen_keys = set()
        
        for section in server.library.sections():
            if section.type == "movie":
                for collection in section.collections():
                    key = str(collection.ratingKey)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_collections.append({
                            "key": key,
                            "title": collection.title
                        })
        
        all_collections.sort(key=lambda x: x["title"].lower())
        return {"collections": all_collections}
        
    except Exception as e:
        logger.error(f"Failed to fetch Plex collections: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch collections: {str(e)}")


@router.post("/plex/collection/repair")
async def repair_collection_key():
    """
    Verify the stored collection key is valid. If not, attempt to find the collection
    by name and update the database with the correct key.
    """
    conn = get_connection()
    try:
        # Get current Plex config
        config = conn.execute("SELECT url, api_key, collection_key FROM plex_config WHERE id = 1").fetchone()
        
        if not config or not config["url"] or not config["api_key"]:
            raise HTTPException(status_code=400, detail="Plex not configured. Please authenticate first.")
        
        if not config["collection_key"]:
            return {
                "success": True,
                "message": "No collection key stored. Please select a collection in Settings.",
                "repaired": False,
                "collection_key": None,
                "collection_name": None
            }
        
        from plexapi.server import PlexServer
        from plexapi.exceptions import NotFound
        
        server = PlexServer(config["url"], config["api_key"])
        stored_key = config["collection_key"]
        collection_name = None
        
        # Try to fetch the collection by stored key
        try:
            collection_obj = server.fetchItem(int(stored_key))
            collection_name = collection_obj.title
            logger.info(f"Collection key {stored_key} is valid: '{collection_name}'")
            return {
                "success": True,
                "message": f"Collection key is valid: '{collection_name}'",
                "repaired": False,
                "collection_key": stored_key,
                "collection_name": collection_name
            }
        except NotFound:
            logger.warning(f"Collection key {stored_key} not found in Plex. Attempting repair...")
            
            # Try to find the collection by name
            # Use fallback name "Movies Leaving Soon"
            fallback_name = "Movies Leaving Soon"
            found_collection = None
            
            for section in server.library.sections():
                if section.type == "movie":
                    for collection in section.collections():
                        if collection.title == fallback_name:
                            found_collection = {
                                "key": str(collection.ratingKey),
                                "title": collection.title
                            }
                            break
                    if found_collection:
                        break
            
            if found_collection:
                # Update the database with the new key
                new_key = found_collection["key"]
                conn.execute(
                    "UPDATE plex_config SET collection_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                    (new_key,)
                )
                conn.commit()
                logger.info(f"✅ Repaired collection key: Updated to '{new_key}' for collection '{found_collection['title']}'")
                return {
                    "success": True,
                    "message": f"Repaired collection key: Found '{found_collection['title']}' with key {new_key}",
                    "repaired": True,
                    "collection_key": new_key,
                    "collection_name": found_collection["title"]
                }
            else:
                logger.warning(f"Collection '{fallback_name}' not found in Plex")
                return {
                    "success": True,
                    "message": f"Collection '{fallback_name}' not found in Plex. Please select a collection in Settings.",
                    "repaired": False,
                    "collection_key": None,
                    "collection_name": None
                }
                
    except Exception as e:
        logger.error(f"Failed to repair collection key: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to repair collection key: {str(e)}")
    finally:
        conn.close()
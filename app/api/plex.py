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
    """Save Plex connection settings (URL and label only)."""
    # Validate URL if provided
    if data.url:
        is_valid, error = validate_url(data.url)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error)

    conn = get_connection()
    try:
        # Get existing config to preserve token
        existing = conn.execute("SELECT api_key FROM plex_config WHERE id = 1").fetchone()
        
        if existing and existing["api_key"]:
            # Keep existing token, just update other fields
            conn.execute(
                """UPDATE plex_config SET
                    url = ?, collection_key = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1""",
                (data.url.rstrip("/") if data.url else None, data.collection_key, 1 if data.enabled else 0)
            )
        else:
            # No token yet, just save URL and collection name
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
    """Clear Plex configuration (keep token for re-authentication)."""
    conn = get_connection()
    try:
        # Only clear URL and label, keep token for re-authentication
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
    """
    Get all collections from Plex for the dropdown selector.
    Returns a list of collections with their keys and titles.
    """
    conn = get_connection()
    config = conn.execute("SELECT url, api_key FROM plex_config WHERE id = 1").fetchone()
    conn.close()

    if not config or not config["url"] or not config["api_key"]:
        raise HTTPException(status_code=400, detail="Plex not configured. Please authenticate first.")

    from app.core.plex_client import PlexClient
    from plexapi.server import PlexServer

    try:
        client = PlexClient(config["url"], config["api_key"])
        
        # Get the movie library section ID
        library_id = await client.get_movie_library_section_id()
        if not library_id:
            raise HTTPException(status_code=404, detail="No movie library found in Plex")
        
        # Connect to Plex and get collections
        server = PlexServer(config["url"], config["api_key"])
        section = server.library.sectionByID(int(library_id))
        
        collections = []
        for collection in section.collections():
            collections.append({
                "key": str(collection.ratingKey),
                "title": collection.title
            })
        
        # Sort by title
        collections.sort(key=lambda x: x["title"].lower())
        
        return {"collections": collections}
        
    except Exception as e:
        logger.error(f"Failed to fetch Plex collections: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch collections: {str(e)}")
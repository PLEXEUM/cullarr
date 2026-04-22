from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db.database import get_connection
from app.core.radarr_client import RadarrClient
from app.utils.logger import get_logger
from app.utils.validators import validate_url

router = APIRouter()
logger = get_logger()


class RadarrConfigInput(BaseModel):
    url: str
    api_key: str


@router.get("/radarr/config")
async def get_radarr_config():
    """Get current Radarr configuration."""
    conn = get_connection()
    config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
    conn.close()

    if not config or not config["url"]:
        return {"configured": False}

    return {
        "configured": True,
        "url": config["url"],
        "api_key": "[REDACTED]" if config["api_key"] else None,
    }


@router.post("/radarr/config")
async def save_radarr_config(data: RadarrConfigInput):
    """Save Radarr connection settings."""
    # Validate URL
    is_valid, error = validate_url(data.url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    conn = get_connection()
    try:
        conn.execute(
            """UPDATE radarr_config SET
                url = ?, api_key = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (data.url.rstrip("/"), data.api_key)
        )
        conn.commit()
        logger.info(f"Radarr config saved for URL: {data.url}")
        return {"success": True, "message": "Configuration saved"}
    except Exception as e:
        logger.error(f"Failed to save Radarr config: {e}")
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    finally:
        conn.close()


@router.post("/radarr/config/test")
async def test_radarr_connection(data: RadarrConfigInput = None):
    """Test connection to Radarr."""
    # If no data provided, use saved config
    if not data:
        conn = get_connection()
        config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        conn.close()
        if not config or not config["url"] or not config["api_key"]:
            raise HTTPException(status_code=400, detail="No Radarr configuration found")
        url = config["url"]
        api_key = config["api_key"]
    else:
        url = data.url
        api_key = data.api_key

    client = RadarrClient(url, api_key)
    success, message = await client.test_connection()

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"success": True, "message": message}


@router.delete("/radarr/config")
async def clear_radarr_config():
    """Clear Radarr configuration."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE radarr_config SET url = NULL, api_key = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
        )
        conn.commit()
        logger.info("Radarr configuration cleared")
        return {"success": True, "message": "Configuration cleared"}
    except Exception as e:
        logger.error(f"Failed to clear Radarr config: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear configuration")
    finally:
        conn.close()
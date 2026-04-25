from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import httpx
from app.db.database import get_connection
from app.utils.logger import get_logger
from app.utils.validators import validate_url, sanitize_input, validate_radarr_label

router = APIRouter()
logger = get_logger()

class RadarrConfig(BaseModel):
    url: str
    api_key: str
    enabled: bool
    label: str

class RadarrClient:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.headers = {"X-Api-Key": api_key}

    async def test_connection(self) -> bool:
        """Test the connection to Radarr."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{self.url}/api/v3/system/status", headers=self.headers)
                return response.status_code == 200
            except Exception as e:
                logger.error(f"Radarr connection test failed: {e}")
                return False

    async def get_movies(self) -> List[Dict[str, Any]]:
        """Fetch all movies from Radarr (Required for scoring engine)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{self.url}/api/v3/movie", headers=self.headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Failed to fetch movies from Radarr: {e}")
                return []

    async def delete_movie(self, movie_id: int, delete_files: bool = True):
        """Delete a movie from Radarr."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                params = {"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"}
                response = await client.delete(
                    f"{self.url}/api/v3/movie/{movie_id}", 
                    headers=self.headers, 
                    params=params
                )
                response.raise_for_status()
                return True
            except Exception as e:
                logger.error(f"Failed to delete movie {movie_id} from Radarr: {e}")
                return False

@router.get("/radarr/config")
async def get_radarr_config():
    """Retrieve the current Radarr configuration."""
    conn = get_connection()
    try:
        config = conn.execute("SELECT * FROM radarr_config WHERE id = 1").fetchone()
        if not config:
            return {"url": "", "api_key": "", "enabled": False, "label": "cullarr"}
        return dict(config)
    finally:
        conn.close()

@router.post("/radarr/config")
async def update_radarr_config(config: RadarrConfig):
    """Update and validate the Radarr configuration."""
    # Validate inputs
    is_valid_url, url_error = validate_url(config.url)
    if not is_valid_url:
        raise HTTPException(status_code=400, detail=url_error)

    is_valid_label, label_error = validate_radarr_label(config.label)
    if not is_valid_label:
        raise HTTPException(status_code=400, detail=label_error)

    # Test connection before saving
    client = RadarrClient(config.url, config.api_key)
    if config.enabled and not await client.test_connection():
        raise HTTPException(status_code=400, detail="Could not connect to Radarr with these settings")

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE radarr_config 
            SET url = ?, api_key = ?, enabled = ?, label = ? 
            WHERE id = 1
        """, (config.url, config.api_key, config.enabled, config.label))
        conn.commit()
    finally:
        conn.close()
    
    return {"status": "success"}
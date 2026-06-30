import httpx
import asyncio
import os
from typing import Optional
from datetime import datetime
from app.utils.logger import get_logger
from app.utils.redactor import redact

logger = get_logger()

# Retry configuration (can be adjusted via environment variables in the future)
def _get_retry_attempts() -> int:
    """Get retry attempts from environment with fallback."""
    try:
        value = int(os.getenv("RADARR_RETRY_ATTEMPTS", "3"))
        if value < 1:
            logger.warning(f"RADARR_RETRY_ATTEMPTS must be >= 1, got {value}, using default 3")
            return 3
        return value
    except ValueError:
        logger.warning("Invalid RADARR_RETRY_ATTEMPTS value, using default 3")
        return 3

def _get_retry_delay_base() -> int:
    """Get retry delay base from environment with fallback."""
    try:
        value = int(os.getenv("RADARR_RETRY_DELAY_BASE", "2"))
        if value < 1:
            logger.warning(f"RADARR_RETRY_DELAY_BASE must be >= 1, got {value}, using default 2")
            return 2
        return value
    except ValueError:
        logger.warning("Invalid RADARR_RETRY_DELAY_BASE value, using default 2")
        return 2

DEFAULT_RETRY_ATTEMPTS = _get_retry_attempts()
DEFAULT_RETRY_DELAY_BASE = _get_retry_delay_base()

class RadarrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }
        # Instance-level retry config with environment override support
        self.retry_attempts = _get_retry_attempts()
        self.retry_delay_base = _get_retry_delay_base()

    async def _request(self, method: str, endpoint: str, timeout: int = 60, **kwargs) -> dict:
        """Make an HTTP request with retry logic."""
        url = f"{self.base_url}/api/v3/{endpoint}"
        last_error = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, url, headers=self.headers, **kwargs)
            
                    # For DELETE operations, 404 means the resource is already gone = success
                    if method == "DELETE" and response.status_code == 404:
                        logger.info(f"Radarr DELETE: Resource already gone (404) - treating as success")
                        return {"success": True, "already_deleted": True}
            
                    # For GET operations, 404 means resource not found = return empty dict
                    if method == "GET" and response.status_code == 404:
                        logger.debug(f"Radarr GET: Resource not found (404) - returning empty dict")
                        return {}
            
                    response.raise_for_status()
                    return response.json() if response.text else {}
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"Radarr API error (attempt {attempt}/{self.retry_attempts}): {e.response.status_code}")
            except httpx.RequestError as e:
                last_error = e
                logger.warning(f"Radarr connection error (attempt {attempt}/{self.retry_attempts}): {redact(str(e))}")

            if attempt < self.retry_attempts:
                wait = self.retry_delay_base ** attempt
                logger.debug(f"Radarr request retry {attempt}/{self.retry_attempts} waiting {wait}s")
                await asyncio.sleep(wait)

        raise ConnectionError(f"Radarr API unreachable after {self.retry_attempts} attempts")

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to Radarr."""
        try:
            await self._request("GET", "system/status")
            return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {redact(str(e))}"

    async def get_movies(self) -> list:
        """Fetch all movies from Radarr."""
        logger.info("Fetching all movies from Radarr...")
        data = await self._request("GET", "movie")

        # Radarr /api/v3/movie always returns a flat list — no pagination
        if isinstance(data, list):
            logger.info(f"Fetched {len(data)} movies total")
            # Add debug sample (optional)
            if data and len(data) > 0:
                sample_movie = data[0]
                logger.debug(f"Sample movie: {sample_movie.get('title')} (ID: {sample_movie.get('id')})")
            return data

        # Unexpected response shape — log and return empty
        logger.warning(f"Unexpected response shape from Radarr /movie: {type(data)}")
        return []

    async def get_movie(self, movie_id: int) -> dict:
        """Fetch a single movie by ID."""
        return await self._request("GET", f"movie/{movie_id}")

    async def delete_movie_file_only(self, movie_id: int) -> dict:
        """Delete only the movie file, keep the movie entry in Radarr."""
        try:
            movie = await self.get_movie(movie_id)
            movie_file = movie.get("movieFile")

            if not movie_file:
                logger.info(f"No movie file found for movie {movie_id} - treating as success")
                return {"success": True, "message": "No file to delete (already removed)"}

            file_id = movie_file.get("id")

            await self._request("DELETE", f"moviefile/{file_id}", timeout=120)
            logger.info(f"Deleted movie file (ID: {file_id}) for movie {movie_id}")
            return {"success": True, "message": f"Deleted file ID: {file_id}"}

        except Exception as e:
            logger.error(f"Failed to delete movie file: {e}")
            return {"success": False, "message": str(e)}
        
    async def delete_movie_entirely(self, movie_id: int) -> dict:
        """Delete the entire movie entry from Radarr (file + database entry)."""
        try:
            # DELETE /api/v3/movie/{id}?deleteFiles=true
            # First verify the movie exists
            movie = await self.get_movie(movie_id)
            if not movie:
                logger.warning(f"Movie {movie_id} not found in Radarr")
                return {"success": True, "message": "Movie already deleted (not found in Radarr)"}
        
            movie_title = movie.get("title", "Unknown")
        
            # Perform full deletion with deleteFiles=true
            result = await self._request("DELETE", f"movie/{movie_id}?deleteFiles=true", timeout=120)
        
            # Check if result indicates already_deleted (404 handler)
            if isinstance(result, dict) and result.get("already_deleted"):
                logger.info(f"Movie already gone: {movie_title} (ID: {movie_id})")
                return {"success": True, "message": f"Movie already deleted: {movie_title}"}
        
            logger.info(f"Deleted entire movie entry: {movie_title} (ID: {movie_id}) from Radarr")
            return {"success": True, "message": f"Deleted movie: {movie_title}"}
        
        except Exception as e:
            logger.error(f"Failed to delete movie {movie_id} entirely: {e}")
            return {"success": False, "message": str(e)}
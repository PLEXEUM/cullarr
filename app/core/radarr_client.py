import httpx
import asyncio
from typing import Optional
from datetime import datetime
from app.utils.logger import get_logger
from app.utils.redactor import redact

logger = get_logger()

class RadarrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }

    async def _request(self, method: str, endpoint: str, timeout: int = 60, **kwargs) -> dict:
        """Make an HTTP request with retry logic."""
        url = f"{self.base_url}/api/v3/{endpoint}"
        last_error = None

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, url, headers=self.headers, **kwargs)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"Radarr API error (attempt {attempt}/3): {e.response.status_code}")
            except httpx.RequestError as e:
                last_error = e
                logger.warning(f"Radarr Request error (attempt {attempt}/3): {e}")
            
            await asyncio.sleep(1)
        
        raise last_error

    async def get_all_movie_ids(self) -> list:
        """
        Fetch only movie IDs from Radarr. 
        Optimized for syncing and pruning stale data.
        """
        data = await self._request("GET", "movie")
        if isinstance(data, list):
            return [m.get("id") for m in data if m.get("id")]
        return []

    async def get_all_movies(self) -> list:
        """Fetch all movies from Radarr (Detailed)."""
        logger.info("Fetching all movies from Radarr...")
        data = await self._request("GET", "movie")

        if isinstance(data, list):
            logger.info(f"Fetched {len(data)} movies total")
            return data

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
                logger.info(f"No movie file found for movie {movie_id}")
                return {"success": False, "message": "No file to delete"}

            file_id = movie_file.get("id")

            await self._request("DELETE", f"moviefile/{file_id}", timeout=120)
            logger.info(f"Deleted movie file (ID: {file_id}) for movie {movie_id}")
            return {"success": True, "message": "File deleted"}
        except Exception as e:
            logger.error(f"Failed to delete movie file for {movie_id}: {e}")
            return {"success": False, "message": str(e)}

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to Radarr."""
        try:
            await self._request("GET", "system/status", timeout=10)
            return True, "Successfully connected to Radarr"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
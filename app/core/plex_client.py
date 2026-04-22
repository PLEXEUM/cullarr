import json
import asyncio
from typing import Optional
from datetime import datetime
from app.utils.logger import get_logger
from app.utils.redactor import redact

logger = get_logger()


class PlexClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _request(self, endpoint: str, timeout: int = 30) -> Optional[dict]:
        """Make a request to Plex API."""
        import httpx
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Plex API request failed: {redact(str(e))}")
            return None

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to Plex."""
        try:
            import httpx
            url = f"{self.base_url}/identity?X-Plex-Token={self.api_key}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {redact(str(e))}"

    async def get_all_play_history(self) -> dict:
        """
        Fetch all play history from Plex using paginated history API.
        Returns dict of ratingKey -> play count and last viewed.
        Requires admin token.
        """
        result = {}
        start = 0
        page_size = 1000

        while True:
            endpoint = f"/status/sessions/history/all?X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}"
            data = await self._request(endpoint)

            if not data:
                break

            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            if not metadata:
                break

            for item in metadata:
                rating_key = item.get("ratingKey")
                if not rating_key:
                    continue

                if rating_key not in result:
                    result[rating_key] = {"play_count": 0, "last_viewed": 0}

                result[rating_key]["play_count"] += 1
                viewed_at = item.get("viewedAt", 0)
                if viewed_at > result[rating_key]["last_viewed"]:
                    result[rating_key]["last_viewed"] = viewed_at

            # Check if we have all records
            total_size = data.get("MediaContainer", {}).get("totalSize", 0)
            if total_size > 0 and len(metadata) < page_size:
                break
            if len(metadata) < page_size:
                break

            start += len(metadata)

        logger.info(f"Fetched play history for {len(result)} items from Plex")
        return result

    async def add_label(self, rating_key: str, label: str) -> bool:
        """Add a label to a Plex item."""
        try:
            import httpx
            encoded_label = label.replace(" ", "%20")
            endpoint = f"/library/metadata/{rating_key}/label?label%5B0%5D.tag.tag={encoded_label}&label.locked=1"
            sep = "&" if "?" in endpoint else "?"
            url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.put(url)
                response.raise_for_status()
                logger.info(f"Added label '{label}' to Plex item {rating_key}")
                return True
        except Exception as e:
            logger.error(f"Failed to add label to Plex: {redact(str(e))}")
            return False

    async def remove_label(self, rating_key: str, label: str) -> bool:
        """Remove a label from a Plex item."""
        try:
            import httpx
            encoded_label = label.replace(" ", "%20")
            endpoint = f"/library/metadata/{rating_key}/label?label%5B0%5D.tag.tag-={encoded_label}&label.locked=1"
            sep = "&" if "?" in endpoint else "?"
            url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.put(url)
                response.raise_for_status()
                logger.info(f"Removed label '{label}' from Plex item {rating_key}")
                return True
        except Exception as e:
            logger.error(f"Failed to remove label from Plex: {redact(str(e))}")
            return False

    async def get_rating_key_by_tmdb_id(self, tmdb_id: int) -> Optional[str]:
        """Find Plex rating key for a given TMDb ID."""
        # This requires searching Plex library
        # For now, we'll rely on the mapping from Radarr
        # Full implementation would need to search Plex library
        logger.debug(f"Looking up rating key for TMDb ID {tmdb_id}")
        return None
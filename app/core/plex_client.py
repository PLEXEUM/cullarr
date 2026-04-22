import json
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime
from app.utils.logger import get_logger
from app.utils.redactor import redact

logger = get_logger()


class PlexClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _request(self, endpoint: str, timeout: int = 30) -> Optional[Dict]:
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

    async def get_all_play_history(self) -> Dict[str, Dict]:
        """
        Fetch play counts from Plex library metadata.
        With an admin token, viewCount returns total plays across ALL users.
        """
        result = {}
        
        # Get all library sections
        sections_data = await self._request("/library/sections")
        if not sections_data:
            logger.warning("Failed to fetch Plex library sections")
            return result
        
        for section in sections_data.get("MediaContainer", {}).get("Directory", []):
            section_type = section.get("type")
            if section_type not in ["movie", "show"]:
                continue
            
            section_key = section.get("key")
            logger.debug(f"Scanning Plex section: {section.get('title')} (type: {section_type})")
            
            # Get all items in this section (includeGuids=1 for TMDb ID)
            items_data = await self._request(f"/library/sections/{section_key}/all?includeGuids=1")
            if not items_data:
                continue
            
            for item in items_data.get("MediaContainer", {}).get("Metadata", []):
                rating_key = item.get("ratingKey")
                if not rating_key:
                    continue
                
                # Extract TMDb ID from GUIDs
                tmdb_id = self._extract_tmdb_id(item.get("Guid", []))
                
                view_count = item.get("viewCount", 0)
                last_viewed = item.get("lastViewedAt", 0)
                
                # Store by rating_key for now (will map to TMDb ID later)
                result[rating_key] = {
                    "play_count": view_count,
                    "last_viewed": last_viewed,
                    "tmdb_id": tmdb_id,
                    "title": item.get("title", ""),
                    "year": item.get("year", 0)
                }
        
        logger.info(f"Fetched play history for {len(result)} items from Plex library metadata")
        return result
    
    def _extract_tmdb_id(self, guids: list) -> Optional[int]:
        """Extract TMDb ID from Plex GUID array."""
        if not guids:
            return None
        
        for guid in guids:
            guid_id = guid.get("id", "")
            if guid_id.startswith("tmdb://"):
                try:
                    return int(guid_id.replace("tmdb://", ""))
                except ValueError:
                    pass
        return None

    async def get_play_counts_by_tmdb(self) -> Dict[int, Dict]:
        """
        Returns play counts keyed by TMDb ID instead of ratingKey.
        This is what the scoring engine expects.
        """
        raw_history = await self.get_all_play_history()
        result = {}
        
        for rating_key, data in raw_history.items():
            tmdb_id = data.get("tmdb_id")
            if tmdb_id:
                result[str(tmdb_id)] = {
                    "play_count": data["play_count"],
                    "last_viewed": data["last_viewed"]
                }
        
        logger.info(f"Mapped {len(result)} items to TMDb IDs")
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
        """Find Plex rating key for a given TMDb ID by scanning library."""
        sections_data = await self._request("/library/sections")
        if not sections_data:
            return None
        
        for section in sections_data.get("MediaContainer", {}).get("Directory", []):
            section_type = section.get("type")
            if section_type not in ["movie", "show"]:
                continue
            
            section_key = section.get("key")
            items_data = await self._request(f"/library/sections/{section_key}/all?includeGuids=1")
            if not items_data:
                continue
            
            for item in items_data.get("MediaContainer", {}).get("Metadata", []):
                extracted_id = self._extract_tmdb_id(item.get("Guid", []))
                if extracted_id == tmdb_id:
                    return item.get("ratingKey")
        
        return None
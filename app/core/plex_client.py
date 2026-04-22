import json
import asyncio
from typing import Optional, Dict, Any, List
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

        # Proper Plex headers (matches Maintainerr/Capacitarr)
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": "Cullarr",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
            "X-Plex-Model": "Plex OAuth",
            "X-Plex-Platform": "Web",
            "X-Plex-Platform-Version": "1.0",
            "X-Plex-Device": "Browser",
            "X-Plex-Device-Name": "Cullarr",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers)
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

    async def get_library_items(self) -> List[Dict]:
        """Fetch all movies and shows from Plex libraries with GUIDs for TMDb mapping."""
        items = []
        
        # Get all library sections
        sections_data = await self._request("/library/sections")
        if not sections_data:
            logger.warning("Failed to fetch Plex library sections")
            return items
        
        for section in sections_data.get("MediaContainer", {}).get("Directory", []):
            section_type = section.get("type")
            if section_type not in ["movie", "show"]:
                continue
            
            section_key = section.get("key")
            logger.debug(f"Scanning Plex section: {section.get('title')}")
            
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
                
                items.append({
                    "rating_key": rating_key,
                    "tmdb_id": tmdb_id,
                    "title": item.get("title", ""),
                    "type": item.get("type"),
                })
        
        logger.info(f"Fetched {len(items)} library items from Plex")
        return items
    
    async def get_all_play_history(self) -> Dict[str, Dict]:
        """
        Fetch play history from Plex using /status/sessions/history/all.
        Returns dict keyed by ratingKey with play_count and last_viewed.
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
                
                viewed_at = item.get("viewedAt", 0)
                
                if rating_key not in result:
                    result[rating_key] = {"play_count": 0, "last_viewed": 0}
                
                result[rating_key]["play_count"] += 1
                if viewed_at > result[rating_key]["last_viewed"]:
                    result[rating_key]["last_viewed"] = viewed_at
            
            # Check if we have all records
            total_size = data.get("MediaContainer", {}).get("totalSize", 0)
            if total_size > 0 and len(metadata) < page_size:
                break
            if len(metadata) < page_size:
                break
            
            start += len(metadata)
        
        logger.info(f"Fetched play history for {len(result)} rating keys from Plex")
        return result

    async def get_play_counts_by_tmdb(self) -> Dict[str, Dict]:
        """
        Returns play counts keyed by TMDb ID.
        Matches Capacitarr's approach: map history events to TMDb IDs via library items.
        """
        # Step 1: Get library items with TMDb IDs
        library_items = await self.get_library_items()
        
        # Build rating_key -> tmdb_id map
        rating_to_tmdb = {}
        for item in library_items:
            if item["tmdb_id"]:
                rating_to_tmdb[item["rating_key"]] = str(item["tmdb_id"])
        
        # Step 2: Get play history
        history = await self.get_all_play_history()
        
        # Step 3: Aggregate play counts by TMDb ID
        result = {}
        for rating_key, data in history.items():
            tmdb_id = rating_to_tmdb.get(rating_key)
            if tmdb_id:
                if tmdb_id not in result:
                    result[tmdb_id] = {"play_count": 0, "last_viewed": 0}
                result[tmdb_id]["play_count"] += data["play_count"]
                if data["last_viewed"] > result[tmdb_id]["last_viewed"]:
                    result[tmdb_id]["last_viewed"] = data["last_viewed"]
        
        logger.info(f"Mapped play counts to {len(result)} TMDb IDs")
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
        library_items = await self.get_library_items()
        for item in library_items:
            if item["tmdb_id"] == tmdb_id:
                return item["rating_key"]
        return None
import json
import asyncio
import httpx
import xml.etree.ElementTree as ET
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
        """Make a GET request to Plex API."""
        import httpx
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"

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

    async def _request_put(self, url: str, timeout: int = 30) -> bool:
        """Make a PUT request to Plex API. Returns True on success."""
        import httpx
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": "Cullarr",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
            "X-Plex-Platform": "Web",
            "X-Plex-Device-Name": "Cullarr",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.put(url, headers=headers)
                response.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Plex PUT request failed: {redact(str(e))}")
            return False

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

            items_data = await self._request(f"/library/sections/{section_key}/all?includeGuids=1")
            if not items_data:
                continue

            for item in items_data.get("MediaContainer", {}).get("Metadata", []):
                rating_key = item.get("ratingKey")
                if not rating_key:
                    continue

                tmdb_id = self._extract_tmdb_id(item.get("Guid", []))

                items.append({
                    "rating_key": rating_key,
                    "tmdb_id": tmdb_id,
                    "title": item.get("title", ""),
                    "year": item.get("year"),
                    "type": item.get("type"),
                    "section_key": section_key,
                })

        logger.info(f"Fetched {len(items)} library items from Plex")
        return items

    async def get_movie_library_section_id(self) -> Optional[str]:
        """Get the first movie library section ID using the existing JSON endpoint."""
        sections_data = await self._request("/library/sections")
        if not sections_data:
            return None

        for section in sections_data.get("MediaContainer", {}).get("Directory", []):
            if section.get("type") == "movie":
                return section.get("key")

        logger.error("No movie library section found in Plex")
        return None

    async def get_all_play_history(self) -> Dict[str, Dict]:
        """Fetch play history from Plex using /status/sessions/history/all."""
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

            total_size = data.get("MediaContainer", {}).get("totalSize", 0)
            if total_size > 0 and len(metadata) < page_size:
                break
            if len(metadata) < page_size:
                break

            start += len(metadata)

        logger.info(f"Fetched play history for {len(result)} rating keys from Plex")
        return result

    async def get_play_counts_by_tmdb(self) -> Dict[str, Dict]:
        """Returns play counts keyed by TMDb ID string."""
        library_items, history = await asyncio.gather(
            self.get_library_items(),
            self.get_all_play_history()
        )

        rating_to_tmdb = {}
        for item in library_items:
            if item["tmdb_id"]:
                rating_to_tmdb[item["rating_key"]] = str(item["tmdb_id"])

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

    async def _get_machine_id(self) -> Optional[str]:
        """Get Plex server machine identifier via JSON endpoint."""
        data = await self._request("/identity")
        if not data:
            return None
        machine_id = data.get("MediaContainer", {}).get("machineIdentifier")
        if not machine_id:
            logger.error("machineIdentifier not found in /identity response")
        return machine_id

    # ========== NEW MAINTAINERR-STYLE COLLECTION METHODS ==========

    async def get_item_collections(self, rating_key: str) -> List[str]:
        """
        Get current collection tags for a media item.
        Returns list of collection names (strings), NOT keys/IDs.
        """
        url = f"{self.base_url}/library/metadata/{rating_key}"
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}X-Plex-Token={self.api_key}"
        
        headers = {
            "Accept": "application/xml",
            "X-Plex-Product": "Cullarr",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(full_url, headers=headers)
                response.raise_for_status()
                
                root = ET.fromstring(response.content)
                video = root.find('.//Video')
                
                if video is None:
                    return []
                
                collections = []
                for collection in video.findall('Collection'):
                    tag = collection.get('tag')
                    if tag:
                        collections.append(tag)
                
                return collections
        except Exception as e:
            logger.error(f"Failed to get collections for {rating_key}: {e}")
            return []
    
    async def update_item_collections(self, rating_key: str, collection_names: List[str], item_type: str = "movie") -> bool:
        """Update ALL collections for an item."""
    
        # Build form data
        data = {}
        for i, name in enumerate(collection_names):
            data[f'collection[{i}].tag'] = name
        data['type'] = '1' if item_type == "movie" else '4'
    
        url = f"{self.base_url}/library/metadata/{rating_key}"
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}X-Plex-Token={self.api_key}"
    
        # VERBOSE DEBUG
        logger.info(f"=" * 60)
        logger.info(f"PLEX COLLECTION UPDATE - RatingKey: {rating_key}")
        logger.info(f"Target collections: {collection_names}")
        logger.info(f"Form data being sent: {data}")
        logger.info(f"URL: {full_url}")
    
        headers = {
        "X-Plex-Product": "Cullarr",
        "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
        "Content-Type": "application/x-www-form-urlencoded",
        }
    
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Try both methods - first as form data, if that fails try as params
                response = await client.put(full_url, data=data, headers=headers)
            
                logger.info(f"Response Status: {response.status_code}")
                logger.info(f"Response Headers: {dict(response.headers)}")
                logger.info(f"Response Body: {response.text[:500] if response.text else 'Empty'}")
            
                if response.status_code == 200:
                    # Even with 200, Plex might not have applied the changes
                    # Let's verify by fetching the item immediately after
                    verify_url = f"{self.base_url}/library/metadata/{rating_key}?X-Plex-Token={self.api_key}"
                    verify_response = await client.get(verify_url, headers={"Accept": "application/xml"})
                
                    if verify_response.status_code == 200:
                        root = ET.fromstring(verify_response.content)
                        video = root.find('.//Video')
                        if video is not None:
                            current_collections = [c.get('tag') for c in video.findall('Collection') if c.get('tag')]
                            logger.info(f"VERIFICATION: Current collections after update: {current_collections}")
                        
                            if set(collection_names) == set(current_collections):
                                logger.info(f"✅ SUCCESS: Collections match!")
                            else:
                                logger.warning(f"❌ MISMATCH: Expected {collection_names}, got {current_collections}")
            
                response.raise_for_status()
                return True
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Error {e.response.status_code}: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Exception: {type(e).__name__}: {e}")
            return False
    
    async def sync_collection(self, rating_key: str, target_collection_name: str, should_be_in: bool, item_type: str = "movie") -> bool:
        """Maintainerr-style sync: merge, don't overwrite."""
    
        logger.info(f"🔄 SYNC COLLECTION - RatingKey: {rating_key}, Target: '{target_collection_name}', Should be in: {should_be_in}")
    
        # Step 1: Get current collection tags
        current = await self.get_item_collections(rating_key)
        logger.info(f"Current collections before sync: {current}")
    
        # Step 2: Modify in memory
        if should_be_in and target_collection_name not in current:
            current.append(target_collection_name)
            logger.info(f"Adding '{target_collection_name}' - New list: {current}")
        elif not should_be_in and target_collection_name in current:
            current.remove(target_collection_name)
            logger.info(f"Removing '{target_collection_name}' - New list: {current}")
        else:
            logger.info(f"No change needed - current: {current}, target in current: {target_collection_name in current}")
            return True
    
        # Step 3: Send FULL list back
        return await self.update_item_collections(rating_key, current, item_type)
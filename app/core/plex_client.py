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
        self._machine_id: Optional[str] = None
        self._movie_section_id: Optional[str] = None

    async def _request(self, endpoint: str, timeout: int = 30) -> Optional[Dict]:
        """Make a GET request to Plex API returning JSON."""
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

    async def _request_xml(self, endpoint: str, timeout: int = 30) -> Optional[str]:
        """Make a GET request to Plex API returning raw XML."""
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"
        
        headers = {
            "Accept": "application/xml",
            "X-Plex-Product": "Cullarr",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
        }
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.error(f"Plex XML request failed: {redact(str(e))}")
            return None

    async def _put(self, endpoint: str, timeout: int = 30) -> bool:
        """Make a PUT request to Plex API."""
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"
        
        headers = {
            "X-Plex-Product": "Cullarr",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
        }
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.put(url, headers=headers)
                response.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Plex PUT request failed: {redact(str(e))}")
            return False

    async def _delete(self, endpoint: str, timeout: int = 30) -> bool:
        """Make a DELETE request to Plex API."""
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.base_url}{endpoint}{sep}X-Plex-Token={self.api_key}"
        
        headers = {
            "X-Plex-Product": "Cullarr",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
        }
        
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.delete(url, headers=headers)
                response.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Plex DELETE request failed: {redact(str(e))}")
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to Plex."""
        try:
            url = f"{self.base_url}/identity?X-Plex-Token={self.api_key}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {redact(str(e))}"

    async def get_machine_id(self) -> Optional[str]:
        """Get Plex server machine identifier."""
        if self._machine_id:
            return self._machine_id
        
        xml_data = await self._request_xml("/identity")
        if not xml_data:
            return None
        
        try:
            root = ET.fromstring(xml_data)
            machine_id = root.get("machineIdentifier")
            if machine_id:
                self._machine_id = machine_id
                logger.info(f"Plex machine ID: {machine_id}")
                return machine_id
        except Exception as e:
            logger.error(f"Failed to parse machine ID: {e}")
        
        return None

    async def get_movie_library_section_id(self) -> Optional[str]:
        """Get the first movie library section ID."""
        if self._movie_section_id:
            return self._movie_section_id
        
        xml_data = await self._request_xml("/library/sections")
        if not xml_data:
            return None
        
        try:
            root = ET.fromstring(xml_data)
            for directory in root.findall(".//Directory"):
                if directory.get("type") == "movie":
                    section_id = directory.get("key")
                    if section_id:
                        self._movie_section_id = section_id
                        logger.info(f"Movie library section ID: {section_id}")
                        return section_id
        except Exception as e:
            logger.error(f"Failed to parse library sections: {e}")
        
        return None

    async def get_collections(self) -> List[Dict[str, str]]:
        """
        Get all collections from the movie library.
        Returns list of dicts with 'ratingKey' and 'title'.
        """
        section_id = await self.get_movie_library_section_id()
        if not section_id:
            logger.error("Cannot get collections: no movie library section found")
            return []
        
        xml_data = await self._request_xml(f"/library/sections/{section_id}/collections")
        if not xml_data:
            return []
        
        collections = []
        try:
            root = ET.fromstring(xml_data)
            for directory in root.findall(".//Directory"):
                if directory.get("type") == "collection":
                    rating_key = directory.get("ratingKey")
                    title = directory.get("title")
                    if rating_key and title:
                        collections.append({
                            "ratingKey": rating_key,
                            "title": title,
                        })
            logger.info(f"Found {len(collections)} collections in Plex")
            return collections
        except Exception as e:
            logger.error(f"Failed to parse collections: {e}")
            return []

    async def get_collection_by_name(self, name: str) -> Optional[str]:
        """Get collection ratingKey by its title/name."""
        collections = await self.get_collections()
        for collection in collections:
            if collection["title"].lower() == name.lower():
                return collection["ratingKey"]
        return None

    async def get_library_items(self) -> List[Dict]:
        """Fetch all movies and shows from Plex libraries with GUIDs for TMDb mapping."""
        items = []
        
        section_id = await self.get_movie_library_section_id()
        if not section_id:
            logger.warning("No movie library section found")
            return items
        
        xml_data = await self._request_xml(f"/library/sections/{section_id}/all?includeGuids=1")
        if not xml_data:
            return items
        
        try:
            root = ET.fromstring(xml_data)
            for video in root.findall(".//Video"):
                rating_key = video.get("ratingKey")
                if not rating_key:
                    continue
                
                # Extract TMDb ID from Guid elements
                tmdb_id = None
                for guid in video.findall(".//Guid"):
                    guid_id = guid.get("id", "")
                    if guid_id.startswith("tmdb://"):
                        try:
                            tmdb_id = int(guid_id.replace("tmdb://", ""))
                        except ValueError:
                            pass
                
                items.append({
                    "rating_key": rating_key,
                    "tmdb_id": tmdb_id,
                    "title": video.get("title", ""),
                    "year": video.get("year"),
                    "type": video.get("type"),
                    "section_key": section_id,
                })
        except Exception as e:
            logger.error(f"Failed to parse library items: {e}")
        
        logger.info(f"Fetched {len(items)} library items from Plex")
        return items

    async def get_all_play_history(self) -> Dict[str, Dict]:
        """Fetch play history from Plex using /status/sessions/history/all."""
        result = {}
        start = 0
        page_size = 1000

        while True:
            xml_data = await self._request_xml(f"/status/sessions/history/all?X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}")
            if not xml_data:
                break
            
            try:
                root = ET.fromstring(xml_data)
                metadata = root.findall(".//Video")
                if not metadata:
                    break
                
                for item in metadata:
                    rating_key = item.get("ratingKey")
                    if not rating_key:
                        continue
                    
                    viewed_at = int(item.get("viewedAt", 0))
                    
                    if rating_key not in result:
                        result[rating_key] = {"play_count": 0, "last_viewed": 0}
                    
                    result[rating_key]["play_count"] += 1
                    if viewed_at > result[rating_key]["last_viewed"]:
                        result[rating_key]["last_viewed"] = viewed_at
                
                total_size = int(root.get("totalSize", 0))
                if total_size > 0 and len(metadata) < page_size:
                    break
                if len(metadata) < page_size:
                    break
                
                start += len(metadata)
            except Exception as e:
                logger.error(f"Failed to parse play history: {e}")
                break

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

    # ========== COLLECTION METHODS (WORKING API) ==========

    async def add_to_collection(self, collection_rating_key: str, item_rating_key: str) -> bool:
        """
        Add a media item to a collection using the working Plex API.
        
        Args:
            collection_rating_key: The ratingKey of the collection (e.g., "615787")
            item_rating_key: The ratingKey of the media item (e.g., "100275")
        """
        machine_id = await self.get_machine_id()
        if not machine_id:
            logger.error("Cannot add to collection: failed to get machine ID")
            return False
        
        uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{item_rating_key}"
        endpoint = f"/library/collections/{collection_rating_key}/items?uri={uri}"
        
        logger.info(f"Adding item {item_rating_key} to collection {collection_rating_key}")
        success = await self._put(endpoint)
        
        if success:
            logger.info(f"Successfully added item {item_rating_key} to collection")
        else:
            logger.error(f"Failed to add item {item_rating_key} to collection")
        
        return success

    async def remove_from_collection(self, collection_rating_key: str, item_rating_key: str) -> bool:
        """
        Remove a media item from a collection using the working Plex API.
        
        Args:
            collection_rating_key: The ratingKey of the collection (e.g., "615787")
            item_rating_key: The ratingKey of the media item (e.g., "100275")
        """
        machine_id = await self.get_machine_id()
        if not machine_id:
            logger.error("Cannot remove from collection: failed to get machine ID")
            return False
        
        uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{item_rating_key}"
        endpoint = f"/library/collections/{collection_rating_key}/items?uri={uri}"
        
        logger.info(f"Removing item {item_rating_key} from collection {collection_rating_key}")
        success = await self._delete(endpoint)
        
        if success:
            logger.info(f"Successfully removed item {item_rating_key} from collection")
        else:
            logger.error(f"Failed to remove item {item_rating_key} from collection")
        
        return success

    async def sync_collection(self, item_rating_key: str, collection_name: str, should_be_in: bool) -> bool:
        """
        Maintainerr-style sync: ensure item is in or out of the named collection.
        
        Args:
            item_rating_key: Plex ratingKey for the item
            collection_name: Name of the collection (e.g., "Movies Leaving Soon")
            should_be_in: True to add, False to remove
        """
        # Get collection ratingKey by name
        collection_rating_key = await self.get_collection_by_name(collection_name)
        
        if not collection_rating_key:
            if should_be_in:
                logger.error(f"Collection '{collection_name}' not found in Plex")
            return False
        
        # For remove operations, we don't need to check current state
        # Just attempt to remove - if not in collection, Plex returns success anyway
        if should_be_in:
            return await self.add_to_collection(collection_rating_key, item_rating_key)
        else:
            return await self.remove_from_collection(collection_rating_key, item_rating_key)
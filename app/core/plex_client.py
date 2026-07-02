import json
import asyncio
import httpx
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List, Tuple 
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Plex XML request returned 404: {endpoint}")
            else:
                logger.error(f"Plex XML request failed: {redact(str(e))}")
            return None
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
                logger.debug(f"PUT {endpoint} -> status {response.status_code}")  # <--- ADD THIS DEBUG
                response.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Plex PUT request failed: {e.response.status_code} - {e.response.text[:200]}")  # <--- MODIFY THIS (add response text)
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
        """Get the movie library section ID (hardcoded to section 5)."""
        if self._movie_section_id:
            return self._movie_section_id
    
        # Hardcoded to section 5 (your main Movies library)
        self._movie_section_id = "5"
        logger.info(f"Using movie library section ID: 5 (Movies)")
        return "5"

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

    async def find_collection_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """
        Find a collection by name and return its key and title.
        Returns dict with 'key' and 'title' or None if not found.
        """
        collections = await self.get_collections()
        for collection in collections:
            if collection["title"].lower() == name.lower():
                logger.info(f"Found collection '{collection['title']}' with key {collection['ratingKey']}")
                return {
                    "key": collection["ratingKey"],
                    "title": collection["title"]
                }
        logger.warning(f"Collection '{name}' not found in Plex")
        return None

    async def ensure_collection(self, name: str) -> Optional[str]:
        """
        Get existing collection or create it if it doesn't exist.
        Returns ratingKey of the collection.
        """
        # Try to find by name first
        collection = await self.find_collection_by_name(name)
        if collection:
            logger.info(f"Found existing collection: '{name}' (key: {collection['key']})")
            return collection["key"]
    
        # Not found - create it
        try:
            from plexapi.server import PlexServer
        
            server = PlexServer(self.base_url, self.api_key)
        
            # Find first movie library section
            section = None
            for s in server.library.sections():
                if s.type == "movie":
                    section = s
                    break
        
            if not section:
                logger.error("No movie library found to create collection")
                return None
        
            # Create the collection
            collection_obj = section.createCollection(title=name)
            collection_key = str(collection_obj.ratingKey)
            logger.info(f"Created new Plex collection: '{name}' (key: {collection_key})")
            return collection_key
        
        except Exception as e:
            logger.error(f"Failed to create collection '{name}': {e}")
            return None

    async def get_library_items(self) -> List[Dict]:
        """Fetch all movies from ALL movie library sections for TMDb mapping."""
        items = []
    
        # Get all sections
        xml_data = await self._request_xml("/library/sections")
        if not xml_data:
            logger.warning("Failed to fetch Plex library sections")
            return items
    
        try:
            root = ET.fromstring(xml_data)
            for directory in root.findall(".//Directory"):
                if directory.get("type") == "movie":
                    section_id = directory.get("key")
                    section_title = directory.get("title", "Unknown")
                    if section_id:
                        logger.debug(f"Scanning movie section: {section_title} (ID: {section_id})")
                        section_items = await self._get_library_items_by_section(section_id)
                        items.extend(section_items)
        except Exception as e:
            logger.error(f"Failed to parse library sections: {e}")
    
        logger.info(f"Fetched {len(items)} library items from {len(set(i.get('section_key') for i in items))} movie sections")
        return items

    async def _get_library_items_by_section(self, section_id: str) -> List[Dict]:
        """Fetch items from a specific library section."""
        items = []
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
            logger.error(f"Failed to parse library items for section {section_id}: {e}")
    
        return items

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
            # CRITICAL: allUsers=1 gets ALL users' play history (not just token owner)
            endpoint = f"/status/sessions/history/all?X-Plex-Container-Start={start}&X-Plex-Container-Size={page_size}&allUsers=1"
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
    
        logger.info(f"Fetched play history for {len(result)} items from Plex (all users)")
        # Add debug sample (optional, helps troubleshooting)
        if result:
            sample_keys = list(result.keys())[:3]
            logger.debug(f"Sample play history keys: {sample_keys}")
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

        # Initialize with ALL movies from library (play_count = 0)
        result = {}
        for rating_key, tmdb_id in rating_to_tmdb.items():
            if tmdb_id:
                result[tmdb_id] = {"play_count": 0, "last_viewed": 0}

        # Update with actual play history
        for rating_key, data in history.items():
            tmdb_id = rating_to_tmdb.get(rating_key)
            if tmdb_id:
                result[tmdb_id]["play_count"] += data["play_count"]
                if data["last_viewed"] > result[tmdb_id]["last_viewed"]:
                    result[tmdb_id]["last_viewed"] = data["last_viewed"]

        logger.info(f"Mapped play counts to {len(result)} TMDb IDs")
        return result
    
    async def get_item_collections(self, rating_key: str) -> List[str]:
        """
        Get all collection tag names for a specific media item.
    
        Args:
            rating_key: The ratingKey of the media item (e.g., "100275")
        
        Returns:
            List of collection names (tags) the item belongs to
        """
        endpoint = f"/library/metadata/{rating_key}"
        xml_data = await self._request_xml(endpoint)
    
        if not xml_data:
            return []
    
        collections = []
        try:
            root = ET.fromstring(xml_data)
            for collection in root.findall(".//Collection"):
                tag = collection.get("tag")
                if tag:
                    collections.append(tag)
        except Exception as e:
            logger.error(f"Failed to parse collections from metadata: {e}")
    
        logger.debug(f"Item {rating_key} belongs to collections: {collections}")
        return collections
    
    async def update_item_collections(self, rating_key: str, collection_names: List[str], locked: bool = True) -> bool:
        """
        Update the full collection tag list for a media item.
        This REPLACES all existing collections with the provided list.
        
        Args:
            rating_key: The ratingKey of the media item (e.g., "100275")
            collection_names: List of collection names to set on the item
            locked: Whether to lock the collection field (prevents Plex from auto-modifying)
            
        Returns:
            True if successful, False otherwise
        """
        if not collection_names:
            # If empty list, we need to clear all collections
            params = "collection[0].tag=&collection.locked=0" if locked else "collection[0].tag="
            endpoint = f"/library/metadata/{rating_key}?{params}"
        else:
            # Build query parameters for each collection
            params = []
            for i, name in enumerate(collection_names):
                params.append(f"collection[{i}].tag={name.replace(' ', '%20')}")
            if locked:
                params.append("collection.locked=0")
            endpoint = f"/library/metadata/{rating_key}?{'&'.join(params)}"
        
        success = await self._put(endpoint)
        
        if success:
            logger.info(f"Updated collections for item {rating_key} to: {collection_names}")
        else:
            logger.error(f"Failed to update collections for item {rating_key}")
        
        return success
    
    async def _get_collection_name_by_key(self, collection_rating_key: str) -> Optional[str]:
        """Get collection name from its rating key."""
        endpoint = f"/library/collections/{collection_rating_key}"
        data = await self._request(endpoint)
    
        if not data:
            return None
    
        # Collections are in "Metadata", not "Directory"
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            if str(item.get("ratingKey")) == str(collection_rating_key):
                return item.get("title")
    
        return None

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
        logger.debug(f"PUT endpoint: {endpoint}")
        success = await self._put(endpoint)
        
        if success:
            logger.info(f"Successfully added item {item_rating_key} to collection")
        else:
            logger.error(f"❌ PUT request failed for item {item_rating_key}")  # <--- MODIFY THIS (add ❌)
        
        return success

    async def remove_from_collection(self, collection_rating_key: str, item_rating_key: str) -> bool:
        """
        Remove a media item from a collection using the READ-MODIFY-WRITE pattern.
        This is more reliable than the DELETE endpoint.
        
        Args:
            collection_rating_key: The ratingKey of the collection (e.g., "615787")
            item_rating_key: The ratingKey of the media item (e.g., "100275")
            
        Returns:
            True if successful, False otherwise
        """
        # Step 1: Get the collection name from the collection rating key
        collection_name = await self._get_collection_name_by_key(collection_rating_key)
        if not collection_name:
            logger.error(f"Could not find collection name for key {collection_rating_key}")
            return False
        
        # Step 2: Get current collections for the item
        current_collections = await self.get_item_collections(item_rating_key)
        
        # Step 3: Remove target collection from the list
        if collection_name not in current_collections:
            logger.debug(f"Item {item_rating_key} not in collection '{collection_name}', nothing to remove")
            return True
        
        updated_collections = [c for c in current_collections if c != collection_name]
        
        # Step 4: Write the full updated list back
        success = await self.update_item_collections(item_rating_key, updated_collections, locked=True)
        
        if success:
            logger.info(f"Removed '{collection_name}' from item {item_rating_key}")
        else:
            logger.error(f"Failed to remove '{collection_name}' from item {item_rating_key}")
        
        return success

    async def sync_collection(self, item_rating_key: str, collection_name: str, should_be_in: bool) -> bool:
        """
        Maintainerr-style sync: ensure item is in or out of the named collection.
        
        Args:
            item_rating_key: Plex ratingKey for the item
            collection_name: Name of the collection (e.g., "Movies Leaving Soon")
            should_be_in: True to add, False to remove
        """
        logger.debug(f"sync_collection: item={item_rating_key}, collection='{collection_name}', should_be_in={should_be_in}")  # <--- ADD THIS DEBUG

        # Get collection ratingKey by name
        collection_rating_key = await self.get_collection_by_name(collection_name)
        
        if not collection_rating_key:
            if should_be_in:
                logger.error(f"Collection '{collection_name}' not found in Plex")
            return False

        logger.debug(f"Found collection_rating_key={collection_rating_key}")  # <--- ADD THIS DEBUG
        
        # For remove operations, we don't need to check current state
        # Just attempt to remove - if not in collection, Plex returns success anyway
        if should_be_in:
            return await self.add_to_collection(collection_rating_key, item_rating_key)
        else:
            return await self.remove_from_collection(collection_rating_key, item_rating_key)
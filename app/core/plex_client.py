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
        """
        Get the first movie library section ID using the existing JSON endpoint.
        Replaces the old get_library_sections() XML method.
        """
        sections_data = await self._request("/library/sections")
        if not sections_data:
            return None

        for section in sections_data.get("MediaContainer", {}).get("Directory", []):
            if section.get("type") == "movie":
                return section.get("key")

        logger.error("No movie library section found in Plex")
        return None

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
        Returns play counts keyed by TMDb ID string.
        Fetches library items and play history in parallel for efficiency,
        then maps rating keys to TMDb IDs.
        """
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

    async def get_or_create_collection(self, collection_name: str, first_movie_rating_key: str = None) -> Optional[str]:
        """
        Get existing Plex collection by name or create a new one.
        Returns collection ratingKey or None on failure.
        """
        from plexapi.server import PlexServer
    
        library_id = await self.get_movie_library_section_id()
        if not library_id:
            logger.error("No movie library section found, cannot get/create collection")
            return None
    
        server = PlexServer(self.base_url, self.api_key)
        section = server.library.sectionByID(int(library_id))
    
        # Check for existing collection by exact name
        for collection in section.collections():
            if collection.title == collection_name:
                rating_key = str(collection.ratingKey)
                logger.info(f"Found existing collection: {collection_name} (key: {rating_key})")
                return rating_key
    
        # Create new collection - Plex requires at least one item
        try:
            if first_movie_rating_key:
                movie = server.fetchItem(int(first_movie_rating_key))
                new_collection = section.createCollection(title=collection_name, items=[movie])
            else:
                new_collection = section.createCollection(title=collection_name)
            
            rating_key = str(new_collection.ratingKey)
            logger.info(f"Created collection: {collection_name} (key: {rating_key})")
            return rating_key
        except Exception as e:
            logger.error(f"Failed to create collection '{collection_name}': {redact(str(e))}")
            return None

    async def add_to_collection(self, collection_key: str, rating_key: str) -> bool:
        """
        Add a movie to a Plex collection.
        Explicitly unlocks the collection field if locked before adding.
        """
        try:
            from plexapi.server import PlexServer
        
            server = PlexServer(self.base_url, self.api_key)
            movie = server.fetchItem(int(rating_key))
            collection = server.fetchItem(int(collection_key))
        
            # Step 1: Unlock the collection field if it's locked
            try:
                movie.edit(**{"collection.locked": False})
                logger.debug(f"Unlocked collection field for item {rating_key}")
            except Exception as unlock_err:
                # If unlocking fails, it's likely already unlocked - continue anyway
                logger.debug(f"Unlock attempt had no effect (likely already unlocked): {unlock_err}")
        
            # Step 2: Add to collection using the correct plexapi method
            movie.addCollection(collection)
            logger.info(f"Added item {rating_key} to collection {collection_key}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to add item {rating_key} to collection {collection_key}: {redact(str(e))}")
            return False

    async def remove_from_collection(self, collection_key: str, rating_key: str) -> bool:
        """
        Remove a movie from a Plex collection using python-plexapi.
        """
        try:
            from plexapi.server import PlexServer

            server = PlexServer(self.base_url, self.api_key)
            movie = server.fetchItem(int(rating_key))
            collection = server.fetchItem(int(collection_key))
            collection.removeItems([movie])
            logger.info(f"Removed item {rating_key} from collection {collection_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove item {rating_key} from collection {collection_key}: {redact(str(e))}")
            return False
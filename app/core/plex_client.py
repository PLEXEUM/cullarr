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

    async def add_label(self, rating_key: str, label: str) -> bool:
        """Add a label to a Plex item."""
        import urllib.parse
        encoded_label = urllib.parse.quote(label)
        url = (
            f"{self.base_url}/library/metadata/{rating_key}/label"
            f"?label%5B0%5D.tag.tag={encoded_label}&label.locked=1"
            f"&X-Plex-Token={self.api_key}"
        )
        success = await self._request_put(url)
        if success:
            logger.info(f"Added label '{label}' to Plex item {rating_key}")
        else:
            logger.error(f"Failed to add label '{label}' to Plex item {rating_key}")
        return success

    async def remove_label(self, rating_key: str, label: str) -> bool:
        """Remove a label from a Plex item."""
        import urllib.parse
        encoded_label = urllib.parse.quote(label)
        url = (
            f"{self.base_url}/library/metadata/{rating_key}/label"
            f"?label%5B0%5D.tag.tag-={encoded_label}&label.locked=1"
            f"&X-Plex-Token={self.api_key}"
        )
        success = await self._request_put(url)
        if success:
            logger.info(f"Removed label '{label}' from Plex item {rating_key}")
        else:
            logger.error(f"Failed to remove label '{label}' from Plex item {rating_key}")
        return success

    async def get_or_create_collection(self, collection_name: str) -> Optional[str]:
        """
        Get existing Plex collection by name or create a new one.
        Returns collection ratingKey or None on failure.
        """
        import urllib.parse

        library_id = await self.get_movie_library_section_id()
        if not library_id:
            logger.error("No movie library section found, cannot get/create collection")
            return None

        # Check for existing collection
        collections_data = await self._request(f"/library/sections/{library_id}/collections")
        if collections_data:
            for item in collections_data.get("MediaContainer", {}).get("Metadata", []):
                if item.get("title") == collection_name and item.get("type") == "collection":
                    rating_key = item.get("ratingKey")
                    logger.info(f"Found existing collection: {collection_name} (key: {rating_key})")
                    return rating_key

        # Create new collection
        import httpx
        encoded_name = urllib.parse.quote(collection_name)
        url = (
            f"{self.base_url}/library/collections"
            f"?type=1&title={encoded_name}&sectionId={library_id}"
            f"&X-Plex-Token={self.api_key}"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url)
                response.raise_for_status()
                data = response.json()
                item = data.get("MediaContainer", {}).get("Metadata", [{}])[0]
                rating_key = item.get("ratingKey")
                if rating_key:
                    logger.info(f"Created collection: {collection_name} (key: {rating_key})")
                    return rating_key
                logger.error(f"Collection created but no ratingKey in response")
                return None
        except Exception as e:
            logger.error(f"Failed to create collection '{collection_name}': {redact(str(e))}")
            return None

    async def add_to_collection(self, collection_key: str, rating_key: str) -> bool:
        """
        Add a movie to a Plex collection.
        Tries multiple API approaches in order until one succeeds,
        logging each attempt to help identify the correct Plex API format.
        """
        import httpx

        machine_id = await self._get_machine_id()
        if not machine_id:
            logger.error("Could not get machine ID, cannot add to collection")
            return False

        uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/{rating_key}"
        base_url = f"{self.base_url}/library/collections/{collection_key}/items"
        token_param = f"X-Plex-Token={self.api_key}"

        headers_json = {
            "Accept": "application/json",
            "X-Plex-Product": "Cullarr",
            "X-Plex-Client-Identifier": "cullarr-695b47f5-3c61-4cbd-8eb3-bcc3d6d06ac5",
            "X-Plex-Platform": "Web",
            "X-Plex-Device-Name": "Cullarr",
        }
        headers_form = {**headers_json, "Content-Type": "application/x-www-form-urlencoded"}

        attempts = [
            # Attempt 1: PUT with uri in query string (current approach)
            {
                "label": "PUT uri=query_string",
                "method": "PUT",
                "url": f"{base_url}?uri={uri}&{token_param}",
                "headers": headers_json,
                "data": None,
            },
            # Attempt 2: PUT with uri in form body
            {
                "label": "PUT uri=form_body",
                "method": "PUT",
                "url": f"{base_url}?{token_param}",
                "headers": headers_form,
                "data": f"uri={uri}",
            },
            # Attempt 3: POST with uri in query string
            {
                "label": "POST uri=query_string",
                "method": "POST",
                "url": f"{base_url}?uri={uri}&{token_param}",
                "headers": headers_json,
                "data": None,
            },
            # Attempt 4: POST with uri in form body
            {
                "label": "POST uri=form_body",
                "method": "POST",
                "url": f"{base_url}?{token_param}",
                "headers": headers_form,
                "data": f"uri={uri}",
            },
            # Attempt 5: PUT with Content-Type header and uri in query string
            {
                "label": "PUT uri=query_string content-type=form",
                "method": "PUT",
                "url": f"{base_url}?uri={uri}&{token_param}",
                "headers": headers_form,
                "data": None,
            },
        ]

        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in attempts:
                try:
                    logger.info(f"Collection add attempt [{attempt['label']}]: {attempt['url']}")
                    if attempt["method"] == "PUT":
                        response = await client.put(
                            attempt["url"],
                            headers=attempt["headers"],
                            content=attempt["data"],
                        )
                    else:
                        response = await client.post(
                            attempt["url"],
                            headers=attempt["headers"],
                            content=attempt["data"],
                        )

                    logger.info(f"Collection add [{attempt['label']}] response: {response.status_code}")

                    if response.status_code in (200, 201):
                        logger.info(f"SUCCESS [{attempt['label']}]: Added item {rating_key} to collection {collection_key}")
                        return True
                    else:
                        logger.warning(f"FAILED [{attempt['label']}]: {response.status_code} — {response.text[:200]}")

                except Exception as e:
                    logger.warning(f"EXCEPTION [{attempt['label']}]: {redact(str(e))}")

        logger.error(f"All attempts failed to add item {rating_key} to collection {collection_key}")
        return False

    async def remove_from_collection(self, collection_key: str, rating_key: str) -> bool:
        """Remove a movie from a Plex collection."""
        import httpx
        url = (
            f"{self.base_url}/library/collections/{collection_key}/children/{rating_key}"
            f"?X-Plex-Token={self.api_key}"
        )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.delete(url)
                response.raise_for_status()
                logger.info(f"Removed item {rating_key} from collection {collection_key}")
                return True
        except Exception as e:
            logger.error(f"Failed to remove item {rating_key} from collection {collection_key}: {redact(str(e))}")
            return False
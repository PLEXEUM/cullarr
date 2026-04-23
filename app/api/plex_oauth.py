from fastapi import APIRouter, HTTPException
import httpx
from app.db.database import get_connection
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

CLIENT_ID = "cullarr"

# Store active PINs in memory with a max cap to prevent unbounded growth
MAX_ACTIVE_PINS = 50
active_pins = {}


def _cleanup_stale_pins():
    """Remove oldest pins if we exceed the cap."""
    if len(active_pins) >= MAX_ACTIVE_PINS:
        oldest_keys = list(active_pins.keys())[:len(active_pins) - MAX_ACTIVE_PINS + 1]
        for key in oldest_keys:
            del active_pins[key]
            logger.debug(f"Cleaned up stale PIN: {key}")


@router.post("/plex/oauth/pin")
async def create_pin():
    """Create a new Plex PIN for OAuth authentication."""
    _cleanup_stale_pins()

    headers = {
        "Accept": "application/json",
        "X-Plex-Product": "Cullarr",
        "X-Plex-Client-Identifier": CLIENT_ID,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://plex.tv/api/v2/pins?strong=true",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()

            pin_data = {
                "id": data["id"],
                "code": data["code"],
                "auth_token": None
            }
            active_pins[data["id"]] = pin_data

            auth_url = f"https://app.plex.tv/auth#?clientID={CLIENT_ID}&code={data['code']}"

            logger.info(f"Created Plex PIN: {data['code']} (ID: {data['id']})")
            return {"id": data["id"], "code": data["code"], "auth_url": auth_url}
    except Exception as e:
        logger.error(f"Failed to create Plex PIN: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PIN: {str(e)}")


@router.get("/plex/oauth/pin/{pin_id}")
async def check_pin(pin_id: int):
    """Check if a PIN has been authenticated."""
    if pin_id not in active_pins:
        raise HTTPException(status_code=404, detail="PIN not found")

    headers = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": CLIENT_ID,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"https://plex.tv/api/v2/pins/{pin_id}",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()

            if data.get("authToken"):
                auth_token = data["authToken"]
                active_pins[pin_id]["auth_token"] = auth_token

                # Save token to database (URL will be saved separately by user)
                conn = get_connection()
                try:
                    conn.execute("""
                        UPDATE plex_config
                        SET api_key = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = 1
                    """, (auth_token,))
                    conn.commit()
                    logger.info("Plex OAuth token saved to database")
                finally:
                    conn.close()

                # Clean up pin from memory
                del active_pins[pin_id]

                return {"auth_token": auth_token, "authenticated": True}

            return {"authenticated": False}

    except Exception as e:
        logger.error(f"Failed to check Plex PIN: {e}")
        return {"authenticated": False, "error": str(e)}


@router.delete("/plex/oauth/pin/{pin_id}")
async def clear_pin(pin_id: int):
    """Clear a PIN from memory."""
    if pin_id in active_pins:
        del active_pins[pin_id]
    return {"success": True}
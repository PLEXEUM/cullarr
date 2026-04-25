from fastapi import APIRouter, HTTPException
import httpx
import os
import cryptography.fernet
from app.db.database import get_connection
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

CLIENT_ID = "cullarr"

# Issue #5: Ephemeral key for in-memory encryption
# This key is generated fresh on every app start and never stored on disk.
_MEM_KEY = cryptography.fernet.Fernet.generate_key()
fernet = cryptography.fernet.Fernet(_MEM_KEY)

# Store active PINs in memory with a max cap
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

            pin_id = data.get("id")
            code = data.get("code")

            if not pin_id or not code:
                raise HTTPException(status_code=500, detail="Failed to generate Plex PIN")

            # Store PIN info, but initialize auth_token as None
            active_pins[pin_id] = {
                "code": code,
                "auth_token": None,
                "created_at": os.times()[4] # Internal monotonic clock
            }

            return {"pin_id": pin_id, "code": code}

    except Exception as e:
        logger.error(f"Failed to create Plex PIN: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plex/oauth/pin/{pin_id}")
async def check_pin(pin_id: int):
    """Check if the PIN has been authorized and retrieve the token."""
    if pin_id not in active_pins:
        return {"authenticated": False, "error": "PIN expired or invalid"}

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
                raw_token = data["authToken"]
                
                # Issue #5: Encrypt token before placing it in the memory dictionary
                encrypted_token = fernet.encrypt(raw_token.encode()).decode()
                active_pins[pin_id]["auth_token"] = encrypted_token

                # Save raw token to database (Database is considered "at rest" security)
                conn = get_connection()
                try:
                    conn.execute("""
                        UPDATE plex_config
                        SET api_key = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = 1
                    """, (raw_token,))
                    conn.commit()
                    logger.info("Plex OAuth token securely saved to database")
                finally:
                    conn.close()

                # Immediate cleanup: Remove the encrypted reference from memory 
                # now that it's in the DB.
                del active_pins[pin_id]

                return {"authenticated": True}

            return {"authenticated": False}

    except Exception as e:
        logger.error(f"Failed to check Plex PIN: {e}")
        return {"authenticated": False, "error": str(e)}

@router.delete("/plex/oauth/pin/{pin_id}")
async def clear_pin(pin_id: int):
    """Clear a PIN from memory."""
    if pin_id in active_pins:
        # Zero out the dictionary entry before deletion
        active_pins[pin_id] = None
        del active_pins[pin_id]
        return {"success": True}
    return {"success": False}
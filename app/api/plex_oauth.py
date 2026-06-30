from fastapi import APIRouter, HTTPException
import httpx
import asyncio
from datetime import datetime, timedelta
from app.db.database import get_connection
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

CLIENT_ID = "cullarr"

# Store active PINs with timestamps for cleanup
# Structure: {pin_id: {"code": str, "created_at": datetime, "auth_token": None}}
MAX_ACTIVE_PINS = 50
PIN_TIMEOUT_MINUTES = 10
active_pins = {}


def _cleanup_stale_pins():
    """Remove expired pins (older than PIN_TIMEOUT_MINUTES) and enforce size cap."""
    now = datetime.now()
    expired_keys = []
    
    # Find expired pins
    for pin_id, pin_data in active_pins.items():
        created_at = pin_data.get("created_at")
        if created_at and (now - created_at) > timedelta(minutes=PIN_TIMEOUT_MINUTES):
            expired_keys.append(pin_id)
    
    # Remove expired pins
    for key in expired_keys:
        # Clear any sensitive data before deletion
        if "auth_token" in active_pins[key]:
            active_pins[key]["auth_token"] = None
        del active_pins[key]
        logger.debug(f"Cleaned up expired PIN: {key}")
    
    # Enforce size cap (remove oldest if still exceeds)
    if len(active_pins) >= MAX_ACTIVE_PINS:
        # Sort by created_at and remove oldest
        sorted_pins = sorted(active_pins.items(), key=lambda x: x[1].get("created_at", datetime.min))
        oldest_keys = [k for k, _ in sorted_pins[:len(active_pins) - MAX_ACTIVE_PINS + 1]]
        for key in oldest_keys:
            if "auth_token" in active_pins[key]:
                active_pins[key]["auth_token"] = None
            del active_pins[key]
            logger.debug(f"Cleaned up oldest PIN due to size cap: {key}")


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
                "auth_token": None,
                "created_at": datetime.now(),  # Track creation time for cleanup
                "checked_at": None,  # Track last check time
                "check_count": 0  # Track number of checks
            }
            active_pins[data["id"]] = pin_data

            auth_url = f"https://app.plex.tv/auth#?clientID={CLIENT_ID}&code={data['code']}"

            logger.info(f"Created Plex PIN: {data['code']} (ID: {data['id']})")
            logger.debug(f"Active pins after creation: {len(active_pins)}")
            return {"id": data["id"], "code": data["code"], "auth_url": auth_url}
    except Exception as e:
        logger.error(f"Failed to create Plex PIN: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PIN: {str(e)}")


@router.get("/plex/oauth/pin/{pin_id}")
async def check_pin(pin_id: int):
    """Check if a PIN has been authenticated."""
    if pin_id not in active_pins:
        raise HTTPException(status_code=404, detail="PIN not found or expired")

    pin_data = active_pins[pin_id]
    pin_data["checked_at"] = datetime.now()
    pin_data["check_count"] += 1

    # If already authenticated, return immediately
    if pin_data.get("auth_token"):
        return {"authenticated": True, "auth_token": "[REDACTED]"}

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
                logger.info(f"Plex OAuth: Auth token received for PIN {pin_id}")
    
                # Save token to database immediately
                conn = get_connection()
                try:
                    conn.execute("""
                        UPDATE plex_config
                        SET api_key = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = 1
                    """, (auth_token,))
                    conn.commit()
                    logger.info(f"Plex OAuth token saved to database for PIN {pin_id}")
                    logger.debug(f"Active pins before cleanup: {len(active_pins)}")
                except Exception as db_error:
                    logger.error(f"Failed to save Plex token to database for PIN {pin_id}: {db_error}")
                    # Don't clear pin if DB save fails - let user retry
                    return {"authenticated": False, "error": "Failed to save token to database"}
                finally:
                    conn.close()

                # Clear sensitive data from memory BEFORE marking as authenticated
                pin_data["auth_token"] = auth_token  # Store for immediate return but will be cleared
                logger.debug(f"PIN {pin_id} authenticated, token stored in memory temporarily")
    
                # Return success with redacted token (client doesn't need the actual token)
                return {"authenticated": True, "auth_token": "[REDACTED]"}
            else:
                # Not authenticated yet, but check if pin has expired
                created_at = pin_data.get("created_at")
                if created_at and (datetime.now() - created_at) > timedelta(minutes=PIN_TIMEOUT_MINUTES):
                    # Clean up expired pin
                    logger.info(f"Plex PIN {pin_id} expired (created {created_at})")
                    pin_data["auth_token"] = None
                    del active_pins[pin_id]
                    logger.debug(f"Active pins after expiry cleanup: {len(active_pins)}")
                    return {"authenticated": False, "error": "PIN expired"}
    
                logger.debug(f"Plex PIN {pin_id} still pending (check #{pin_data.get('check_count', 0)})")
                return {"authenticated": False}

    except Exception as e:
        logger.error(f"Failed to check Plex PIN: {e}")
        return {"authenticated": False, "error": str(e)}


@router.delete("/plex/oauth/pin/{pin_id}")
async def clear_pin(pin_id: int):
    """Clear a PIN from memory immediately (used after auth completes or user cancels)."""
    if pin_id in active_pins:
        # Wipe any sensitive data before deletion
        if "auth_token" in active_pins[pin_id]:
            active_pins[pin_id]["auth_token"] = None
        del active_pins[pin_id]
        logger.info(f"Plex PIN {pin_id} cleared from memory")
        logger.debug(f"Active pins after clearance: {len(active_pins)}")
    else:
        logger.debug(f"Plex PIN {pin_id} not found in memory (already cleared)")
    return {"success": True}


@router.post("/plex/oauth/cleanup")
async def force_cleanup_pins():
    """Force cleanup of all expired pins (admin endpoint, optional)."""
    before = len(active_pins)
    _cleanup_stale_pins()
    after = len(active_pins)
    logger.info(f"Force cleanup: removed {before - after} pins, {after} active remain")
    return {"success": True, "active_pins_count": after}
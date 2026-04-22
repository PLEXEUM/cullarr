from fastapi import APIRouter, HTTPException
import httpx
import uuid
from app.db.database import get_connection
from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger()

# Simple client ID (no UUID prefix)
CLIENT_ID = "cullarr"

# Store active PINs in memory
active_pins = {}


@router.post("/plex/oauth/pin")
async def create_pin():
    """Create a new Plex PIN for OAuth authentication."""
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
            
            # Build auth URL (no #! just #?)
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
                
                # Save to database
                conn = get_connection()
                conn.execute("""
                    UPDATE plex_config 
                    SET api_key = ?, enabled = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (auth_token,))
                conn.commit()
                conn.close()
                
                logger.info("Plex OAuth token saved to database")
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
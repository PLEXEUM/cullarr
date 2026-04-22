from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
from app.db.database import get_connection
from app.utils.logger import get_logger
import uuid

router = APIRouter()
logger = get_logger()

# Store active PINs in memory (will be cleared on restart)
active_pins = {}


class PinResponse(BaseModel):
    id: int
    code: str


class TokenResponse(BaseModel):
    auth_token: str


@router.post("/plex/oauth/pin")
async def create_pin():
    """Create a new Plex PIN for OAuth authentication."""
    import httpx
    
    headers = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": str(uuid.uuid4()),
        "X-Plex-Product": "Cullarr",
        "X-Plex-Version": "1.0.0",
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
            
            return {"id": data["id"], "code": data["code"]}
    except Exception as e:
        logger.error(f"Failed to create Plex PIN: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PIN: {str(e)}")


@router.get("/plex/oauth/pin/{pin_id}")
async def check_pin(pin_id: int):
    """Check if a PIN has been authenticated."""
    import httpx
    
    if pin_id not in active_pins:
        raise HTTPException(status_code=404, detail="PIN not found")
    
    headers = {
        "Accept": "application/json",
        "X-Plex-Client-Identifier": str(uuid.uuid4()),
        "X-Plex-Product": "Cullarr",
        "X-Plex-Version": "1.0.0",
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
                
                # Also save to database
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
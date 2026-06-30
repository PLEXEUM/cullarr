from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import asyncio 
import uuid 
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware  

from app.api import radarr, plex, plex_oauth, settings, run, dashboard, logs
from app.db.database import init_db, migrate_db
from app.core.scheduler import start_scheduler, shutdown_scheduler
from app.utils.logger import setup_logger, get_logger

# Setup logging
from app.config import settings as app_settings

# Then use app_settings for logging config
setup_logger(
    log_level=app_settings.log_level,
    log_max_size_mb=app_settings.log_max_size_mb,
    log_max_files=app_settings.max_log_files
)

logger = get_logger()

# ===== ADD REQUEST ID MIDDLEWARE CLASS HERE (after logger, before app creation) =====
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]  # Short ID for readability
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
# ===== END MIDDLEWARE =====

# Create FastAPI app
app = FastAPI(title="Cullarr", version="1.0.0")

# ===== ADD MIDDLEWARE TO APP (right after app creation) =====
app.add_middleware(RequestIDMiddleware)
# ===== END MIDDLEWARE ADD =====

# Setup templates
templates = Jinja2Templates(directory="app/templates")

# Setup static files
static_path = Path("app/static")
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Initialize database and apply any migrations for existing installs
init_db()
migrate_db()
logger.info("Database initialized")

# Register routers
app.include_router(radarr.router, prefix="/api")
app.include_router(plex.router, prefix="/api")
app.include_router(plex_oauth.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(run.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(logs.router, prefix="/api")

# Frontend routes
@app.get("/")
async def dashboard_page(request: Request):
    """Dashboard page"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/settings")
async def settings_page(request: Request):
    """Settings page"""
    return templates.TemplateResponse("settings.html", {"request": request})

@app.get("/logs")
async def logs_page(request: Request):
    """Logs page"""
    return templates.TemplateResponse("logs.html", {"request": request})

# ===== HEALTH CHECK ENDPOINT =====
@app.get("/health")
async def health_check():
    """Health check endpoint for Docker/Kubernetes with dependency status."""
    from app.core.scheduler import scheduler
    from app.core.radarr_client import RadarrClient
    from app.core.plex_client import PlexClient
    from app.db.database import get_connection
    
    status = {
        "status": "healthy",
        "service": "cullarr",
        "timestamp": datetime.now().isoformat(),
        "dependencies": {
            "database": "unknown",
            "radarr": "unknown",
            "plex": "unknown",
            "scheduler": "unknown"
        }
    }
    
    # Check database
    try:
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        status["dependencies"]["database"] = "connected"
    except Exception as e:
        status["dependencies"]["database"] = f"error: {str(e)}"
        status["status"] = "degraded"
    
    # Check Radarr
    conn = get_connection()
    try:
        radarr_config = conn.execute("SELECT url, api_key FROM radarr_config WHERE id = 1").fetchone()
        if radarr_config and radarr_config["url"] and radarr_config["api_key"]:
            client = RadarrClient(radarr_config["url"], radarr_config["api_key"])
            ok, _ = await client.test_connection()
            status["dependencies"]["radarr"] = "connected" if ok else "error"
            if not ok:
                status["status"] = "degraded"
        else:
            status["dependencies"]["radarr"] = "not configured"
    except Exception as e:
        status["dependencies"]["radarr"] = f"error: {str(e)}"
        status["status"] = "degraded"
    finally:
        conn.close()
    
    # Check Plex
    conn = get_connection()
    try:
        plex_config = conn.execute("SELECT url, api_key, enabled FROM plex_config WHERE id = 1").fetchone()
        if plex_config and plex_config["enabled"] and plex_config["url"] and plex_config["api_key"]:
            client = PlexClient(plex_config["url"], plex_config["api_key"])
            ok, _ = await client.test_connection()
            status["dependencies"]["plex"] = "connected" if ok else "error"
            if not ok:
                status["status"] = "degraded"
        else:
            status["dependencies"]["plex"] = "not configured"
    except Exception as e:
        status["dependencies"]["plex"] = f"error: {str(e)}"
        status["status"] = "degraded"
    finally:
        conn.close()
    
    # Check scheduler
    try:
        status["dependencies"]["scheduler"] = "running" if scheduler.running else "stopped"
        if not scheduler.running:
            status["status"] = "degraded"
    except Exception as e:
        status["dependencies"]["scheduler"] = f"error: {str(e)}"
        status["status"] = "degraded"
    
    return status
# ===== END HEALTH CHECK =====

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Start scheduler on app startup"""
    start_scheduler()
    logger.info("Cullarr started")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown scheduler on app stop"""
    shutdown_scheduler()
    await asyncio.sleep(0.5)  # Allow final logs to write
    logger.info("Cullarr stopped")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7447)
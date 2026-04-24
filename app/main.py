from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os

from app.api import radarr, plex, plex_oauth, settings, run, dashboard, logs
from app.db.database import init_db, migrate_db
from app.core.scheduler import start_scheduler, shutdown_scheduler
from app.utils.logger import setup_logger, get_logger

# Setup logging
log_level = os.getenv("LOG_LEVEL", "INFO")
log_max_size_mb = int(os.getenv("LOG_MAX_SIZE_MB", "10"))
log_max_files = int(os.getenv("MAX_LOG_FILES", "5"))
setup_logger(log_level=log_level, log_max_size_mb=log_max_size_mb, log_max_files=log_max_files)

logger = get_logger()

# Create FastAPI app
app = FastAPI(title="Cullarr", version="1.0.0")

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
    logger.info("Cullarr stopped")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7447)
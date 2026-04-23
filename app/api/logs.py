from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from app.utils.logger import get_logger, LOG_PATH

router = APIRouter()
logger = get_logger()

LOG_DIR = Path("/app/logs")


class LogSettingsInput(BaseModel):
    log_level: str = "INFO"
    log_max_size_mb: int = 10
    log_max_files: int = 5


def get_all_log_files() -> list:
    """Get all log files sorted by date (newest first)."""
    if not LOG_DIR.exists():
        return []
    log_files = list(LOG_DIR.glob("cullarr.log*"))
    log_files.sort(reverse=True)
    return log_files


@router.get("/logs")
async def get_logs(lines: int = 100):
    """Return the last N lines of today's log file."""
    log_path = LOG_PATH

    if not log_path.exists():
        all_logs = get_all_log_files()
        if all_logs:
            log_path = all_logs[0]
        else:
            return {"lines": [], "message": "No log file found yet", "total_lines": 0, "showing": 0}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "lines": [line.rstrip("\n") for line in last_lines],
            "total_lines": len(all_lines),
            "showing": len(last_lines),
            "log_file": log_path.name
        }
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to read log file")


@router.get("/logs/download")
async def download_logs():
    """Download today's log file."""
    log_path = LOG_PATH

    if not log_path.exists():
        all_logs = get_all_log_files()
        if all_logs:
            log_path = all_logs[0]
        else:
            raise HTTPException(status_code=404, detail="No log file found")

    return FileResponse(
        path=log_path,
        media_type="text/plain",
        filename=log_path.name
    )


@router.delete("/logs")
async def clear_logs():
    """Clear today's log file."""
    log_path = LOG_PATH

    try:
        if log_path.exists():
            with open(log_path, "w") as f:
                f.write("")
            logger.info(f"Cleared log file: {log_path.name}")
            return {"success": True, "message": f"Cleared {log_path.name}"}
        else:
            return {"success": True, "message": "No log file to clear"}
    except Exception as e:
        logger.error(f"Failed to clear logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear logs")


@router.get("/logs/settings")
async def get_log_settings():
    """Get current log settings from environment."""
    import os
    return {
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_max_size_mb": int(os.getenv("LOG_MAX_SIZE_MB", "10")),
        "log_max_files": int(os.getenv("MAX_LOG_FILES", "5")),
    }


@router.post("/logs/settings")
async def save_log_settings(data: LogSettingsInput):
    """Save log settings (updates environment and reconfigures logger)."""
    import os
    from app.utils.logger import setup_logger

    if data.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise HTTPException(status_code=400, detail="Invalid log level")

    if data.log_max_size_mb < 1 or data.log_max_size_mb > 100:
        raise HTTPException(status_code=400, detail="Log max size must be between 1 and 100 MB")

    if data.log_max_files < 1 or data.log_max_files > 20:
        raise HTTPException(status_code=400, detail="Log max files must be between 1 and 20")

    os.environ["LOG_LEVEL"] = data.log_level.upper()
    os.environ["LOG_MAX_SIZE_MB"] = str(data.log_max_size_mb)
    os.environ["MAX_LOG_FILES"] = str(data.log_max_files)

    setup_logger(
        log_level=data.log_level,
        log_max_size_mb=data.log_max_size_mb,
        log_max_files=data.log_max_files
    )

    logger.info("Log settings updated")
    return {"success": True, "message": "Log settings saved"}
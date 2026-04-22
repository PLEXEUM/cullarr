import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_PATH = Path("/app/logs/cullarr.log")

def redact_api_keys(message: str) -> str:
    """Replace API keys in log messages with [REDACTED]."""
    # Redact anything that looks like an API key (32+ alphanumeric chars)
    message = re.sub(r'[a-zA-Z0-9]{32,}', '[REDACTED]', message)
    # Redact radarr API key patterns
    message = re.sub(r'(apikey=)[^&\s]+', r'\1[REDACTED]', message)
    message = re.sub(r'(X-Api-Key:\s*)\S+', r'\1[REDACTED]', message)
    message = re.sub(r'(X-Plex-Token=)[^&\s]+', r'\1[REDACTED]', message)
    return message

class RedactingFormatter(logging.Formatter):
    """Custom log formatter that redacts API keys."""
    def format(self, record):
        msg = super().format(record)
        return redact_api_keys(msg)

def setup_logger(
    log_level: str = "INFO",
    log_max_size_mb: int = 10,
    log_max_files: int = 5
) -> logging.Logger:
    """Set up and return the application logger with dated log files."""
    os.makedirs(LOG_PATH.parent, exist_ok=True)
    
    logger = logging.getLogger("cullarr")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Remove existing handlers to avoid duplicates on reload
    logger.handlers.clear()
    
    # Use TimedRotatingFileHandler for date-based rotation
    file_handler = TimedRotatingFileHandler(
        str(LOG_PATH),
        when="midnight",
        interval=1,
        backupCount=log_max_files,
        encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d"
    
    file_handler.setFormatter(RedactingFormatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    
    # Console handler for Docker logs
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(RedactingFormatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.info(f"Logging to {LOG_PATH} with {log_max_files} days retention")
    
    return logger

def get_logger() -> logging.Logger:
    """Get the application logger."""
    return logging.getLogger("cullarr")
from pydantic import BaseSettings, validator

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Logging
    log_level: str = "INFO"
    log_max_size_mb: int = 10
    max_log_files: int = 5
    
    # Radarr client
    radarr_retry_attempts: int = 3
    radarr_retry_delay_base: int = 2
    
    # Scheduler
    run_timeout_seconds: int = 7200
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
    
    @validator("log_level")
    def validate_log_level(cls, v: str) -> str:
        v = v.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR")
        return v
    
    @validator("log_max_size_mb")
    def validate_log_max_size(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError(f"LOG_MAX_SIZE_MB must be between 1 and 100")
        return v
    
    @validator("max_log_files")
    def validate_max_log_files(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError(f"MAX_LOG_FILES must be between 1 and 20")
        return v
    
    @validator("radarr_retry_attempts")
    def validate_retry_attempts(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError(f"RADARR_RETRY_ATTEMPTS must be between 1 and 10")
        return v
    
    @validator("radarr_retry_delay_base")
    def validate_retry_delay(cls, v: int) -> int:
        if v < 1 or v > 30:
            raise ValueError(f"RADARR_RETRY_DELAY_BASE must be between 1 and 30")
        return v
    
    @validator("run_timeout_seconds")
    def validate_run_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError(f"RUN_TIMEOUT_SECONDS must be at least 60 seconds")
        return v

# Singleton instance
settings = Settings()
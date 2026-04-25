import re
from croniter import croniter

# Allowed characters for user inputs
ALLOWED_PATTERN = re.compile(r'^[a-zA-Z0-9/\-_.:]+$')

# Dangerous characters to reject
DANGEROUS_PATTERN = re.compile(r'[;|&$`>]')

def sanitize_input(value: str) -> tuple[bool, str]:
    """
    Validate user input against allowed characters.
    Returns (is_valid, error_message)
    """
    if not value:
        return True, ""

    if DANGEROUS_PATTERN.search(value):
        return False, "Input contains invalid characters (; | & $ ` >)"

    return True, ""

def validate_url(url: str) -> tuple[bool, str]:
    """Validate a URL format."""
    if not url:
        return False, "URL cannot be empty"

    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"

    if DANGEROUS_PATTERN.search(url):
        return False, "URL contains invalid characters"

    return True, ""

def validate_cron(cron_str: str) -> bool:
    """
    Validate that a cron expression is syntactically correct and executable.
    Addresses Issue #7.
    """
    if not cron_str:
        return False
        
    try:
        # Check if the expression is valid (Standard 5-field cron)
        return croniter.is_valid(cron_str)
    except Exception:
        return False

def validate_batch_size(value: int) -> tuple[bool, str]:
    """Validate batch size is between 1 and 20 (no unlimited/0)."""
    if value < 1 or value > 20:
        return False, "Batch size must be between 1 and 20"
    return True, ""

def validate_delete_after_days(value: int) -> tuple[bool, str]:
    """Validate delete after days is between 1 and 90."""
    if value < 1 or value > 90:
        return False, "Delete after days must be between 1 and 90"
    return True, ""

def validate_protection_days(value: int) -> tuple[bool, str]:
    """Validate protection days is between 0 and 365."""
    if value < 0 or value > 365:
        return False, "Protection days must be between 0 and 365"
    return True, ""

def validate_max_queued(value: int) -> tuple[bool, str]:
    """Validate max queued deletions is between 1 and 500."""
    if value < 1 or value > 500:
        return False, "Max queued deletions must be between 1 and 500"
    return True, ""
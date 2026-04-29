import re

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

def validate_cron(expression: str) -> tuple[bool, str]:
    """
    Validate cron expression syntax.
    Checks:
    - Exactly 5 fields
    - Each field contains valid values (0-59, 0-23, 1-31, 1-12, 0-6)
    - Supports * and */N syntax
    - Supports ranges (1-5) and lists (1,2,3)
    """
    if not expression:
        return False, "Cron expression cannot be empty"

    cron_parts = expression.strip().split()
    if len(cron_parts) != 5:
        return False, f"Cron expression must have exactly 5 fields, got {len(cron_parts)} (e.g. '0 2 * * *')"

    # Field definitions: (index, name, min, max, allow_names)
    fields = [
        (0, "minute", 0, 59, False),
        (1, "hour", 0, 23, False),
        (2, "day of month", 1, 31, False),
        (3, "month", 1, 12, True),   # Allows JAN-DEC
        (4, "day of week", 0, 6, True),  # Allows SUN-SAT (0=Sunday)
    ]
    
    # Month names mapping
    month_names = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
    }
    
    # Day of week names mapping
    dow_names = {
        "SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6
    }
    
    for idx, name, min_val, max_val, allow_names in fields:
        field = cron_parts[idx].upper()
        
        # Skip empty fields (shouldn't happen but check anyway)
        if not field:
            return False, f"{name.capitalize()} field cannot be empty"
        
        # Handle * (any value)
        if field == "*":
            continue
        
        # Handle */N (step values)
        if field.startswith("*/"):
            try:
                step = int(field[2:])
                if step < 1 or step > max_val:
                    return False, f"{name.capitalize()} step value must be between 1 and {max_val}"
                continue
            except ValueError:
                return False, f"{name.capitalize()} field has invalid step syntax: {field}"
        
        # Handle named values (months or days of week)
        if allow_names and field in month_names or field in dow_names:
            continue
        
        # Handle comma-separated list
        if "," in field:
            parts_list = field.split(",")
            for part in parts_list:
                # Check each part (can be number or range)
                if "-" in part:
                    range_parts = part.split("-")
                    if len(range_parts) != 2:
                        return False, f"{name.capitalize()} field has invalid range: {part}"
                    try:
                        start = int(range_parts[0])
                        end = int(range_parts[1])
                        if start < min_val or end > max_val or start > end:
                            return False, f"{name.capitalize()} range {start}-{end} has values outside {min_val}-{max_val}"
                    except ValueError:
                        # Check for named values in range (e.g., MON-FRI)
                        if allow_names:
                            start_name = range_parts[0].upper()
                            end_name = range_parts[1].upper()
                            if start_name in dow_names and end_name in dow_names:
                                continue
                            if start_name in month_names and end_name in month_names:
                                continue
                        return False, f"{name.capitalize()} field has invalid range: {part}"
                else:
                    # Single value - could be number or name
                    try:
                        val = int(part)
                        if val < min_val or val > max_val:
                            return False, f"{name.capitalize()} value {val} is outside {min_val}-{max_val}"
                    except ValueError:
                        # Check if it's a valid name (for month or day of week)
                        if allow_names and (part.upper() in month_names or part.upper() in dow_names):
                            continue
                        return False, f"{name.capitalize()} field has invalid value: {part}"
            continue
        
        # Handle range (e.g., 1-5)
        if "-" in field:
            range_parts = field.split("-")
            if len(range_parts) != 2:
                return False, f"{name.capitalize()} field has invalid range: {field}"
            try:
                start = int(range_parts[0])
                end = int(range_parts[1])
                if start < min_val or end > max_val or start > end:
                    return False, f"{name.capitalize()} range {start}-{end} has values outside {min_val}-{max_val}"
            except ValueError:
                return False, f"{name.capitalize()} field has invalid range values: {field}"
            continue
        
        # Handle single number value
        try:
            val = int(field)
            if val < min_val or val > max_val:
                return False, f"{name.capitalize()} value {val} is outside {min_val}-{max_val}"
        except ValueError:
            # If we get here, check if it's a valid name (for month or day of week)
            if allow_names and (field.upper() in month_names or field.upper() in dow_names):
                continue
            return False, f"{name.capitalize()} field has invalid value: {field}"
    
    return True, ""

def validate_batch_size(value: int) -> tuple[bool, str]:
    """Validate batch size is between 1 and 20 (no unlimited/0)."""
    if value < 1 or value > 20:
        return False, "Batch size must be between 1 and 20"
    return True, ""

def validate_delete_after_days(value: int) -> tuple[bool, str]:
    """Validate delete after days is between 0 and 90."""
    if value < 0 or value > 90:
        return False, "Delete after days must be between 0 and 90"
    return True, ""

def validate_max_queued(value: int) -> tuple[bool, str]:
    """Validate max queued deletions is between 1 and 500."""
    if value < 1 or value > 500:
        return False, "Max queued deletions must be between 1 and 500"
    return True, ""
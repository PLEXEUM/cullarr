"""
Shared run state module to avoid circular imports between run.py and run_engine.py
"""

# Global state for tracking active runs
_active_run = {
    "is_running": False,
    "run_id": None,
    "run_type": None,
    "current": 0,
    "total": 0,
    "current_movie": "",
    "cancelled": False,
    "dry_run": False,
    "dry_run_results": None,
    "run_sequence": 0,
    "last_updated": None,
}
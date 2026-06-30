import asyncio
import os
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from app.utils.logger import get_logger
from app.db.database import get_connection

logger = get_logger()

def get_local_timezone():
    """Get the system's local timezone automatically."""
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz:
            return local_tz
    except Exception:
        pass
    
    tz_name = os.environ.get('TZ', 'UTC')
    try:
        return pytz.timezone(tz_name)
    except:
        return pytz.UTC

# Global scheduler instance with local timezone
local_tz = get_local_timezone()
scheduler = AsyncIOScheduler(timezone=local_tz)

def job_listener(event):
    """Listen for scheduler job events."""
    if event.exception:
        logger.error(f"Job '{event.job_id}' failed: {event.exception}")
    elif event.code == EVENT_JOB_MISSED:
        logger.warning(f"Job '{event.job_id}' was missed (misfired) - check if scheduler was running")
    elif event.code == EVENT_JOB_ERROR:
        logger.error(f"Job '{event.job_id}' error: {event.exception}")

scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

# Timeout for runs (configurable via environment variable, default 2 hours)
from app.config import settings

RUN_TIMEOUT_SECONDS = settings.run_timeout_seconds
logger.info(f"Run timeout set to {RUN_TIMEOUT_SECONDS} seconds ({RUN_TIMEOUT_SECONDS/3600:.1f} hours)")


async def execute_score_run():
    """Execute a score run (identify movies for deletion)."""
    from app.core.run_engine import run_score_cycle

    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        logger.info("Cullarr disabled, skipping scheduled score run")
        return

    logger.info(f"Starting scheduled score run (timeout: {RUN_TIMEOUT_SECONDS}s)")
    try:
        await asyncio.wait_for(run_score_cycle(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Scheduled score run completed")
    except asyncio.TimeoutError:
        logger.error(f"Score run timed out after {RUN_TIMEOUT_SECONDS} seconds")
    except Exception as e:
        logger.error(f"Scheduled score run failed: {e}")


async def execute_cull_run():
    """Execute a cull run (actually delete movies)."""
    from app.core.run_engine import run_cull_cycle

    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        logger.info("Cullarr disabled, skipping scheduled cull run")
        return

    logger.info(f"Starting scheduled cull run (timeout: {RUN_TIMEOUT_SECONDS}s)")
    try:
        await asyncio.wait_for(run_cull_cycle(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Scheduled cull run completed")
    except asyncio.TimeoutError:
        logger.error(f"Cull run timed out after {RUN_TIMEOUT_SECONDS} seconds")
    except Exception as e:
        logger.error(f"Scheduled cull run failed: {e}")


def update_score_schedule(cron_expression: str):
    """Update the score schedule."""
    try:
        if scheduler.get_job("score_run"):
            scheduler.remove_job("score_run")

        trigger = CronTrigger.from_crontab(cron_expression, timezone=local_tz)
        scheduler.add_job(
            execute_score_run,
            trigger=trigger,
            id="score_run",
            name="Score Run",
            misfire_grace_time=None
        )
        logger.info(f"Score schedule updated: {cron_expression}")
    except Exception as e:
        logger.error(f"Invalid cron expression '{cron_expression}': {e}")
        raise ValueError(f"Invalid cron expression: {e}")


def update_cull_schedule(cron_expression: str):
    """Update the cull schedule."""
    try:
        if scheduler.get_job("cull_run"):
            scheduler.remove_job("cull_run")

        trigger = CronTrigger.from_crontab(cron_expression, timezone=local_tz)
        scheduler.add_job(
            execute_cull_run,
            trigger=trigger,
            id="cull_run",
            name="Cull Run",
            misfire_grace_time=None
        )
        logger.info(f"Cull schedule updated: {cron_expression}")
    except Exception as e:
        logger.error(f"Invalid cron expression '{cron_expression}': {e}")
        raise ValueError(f"Invalid cron expression: {e}")


def get_next_score_run() -> str:
    """Get the next scheduled score run time in local time."""
    job = scheduler.get_job("score_run")
    if job and job.next_run_time:
        local_time = job.next_run_time.astimezone(local_tz)
        return local_time.strftime("%Y-%m-%d %H:%M:%S")
    return "Not scheduled"

def get_next_cull_run() -> str:
    """Get the next scheduled cull run time in local time."""
    job = scheduler.get_job("cull_run")
    if job and job.next_run_time:
        local_time = job.next_run_time.astimezone(local_tz)
        return local_time.strftime("%Y-%m-%d %H:%M:%S")
    return "Not scheduled"


def start_scheduler():
    """Start the scheduler and load schedules from database."""
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")

    conn = get_connection()
    settings = conn.execute("SELECT score_cron, cull_cron, enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if settings:
        update_score_schedule(settings["score_cron"])
        update_cull_schedule(settings["cull_cron"])
        if settings["enabled"]:
            logger.info(f"Schedules loaded: score={settings['score_cron']}, cull={settings['cull_cron']}")
        else:
            logger.info("Scheduler loaded but Cullarr is disabled")


def shutdown_scheduler():
    """Stop the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
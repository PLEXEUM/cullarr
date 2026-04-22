import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.utils.logger import get_logger
from app.db.database import get_connection

logger = get_logger()

# Global scheduler instance
scheduler = AsyncIOScheduler()

# Timeout for runs (2 hours)
RUN_TIMEOUT_SECONDS = 7200


async def execute_score_run():
    """Execute a score run (identify movies for deletion)."""
    from app.core.run_engine import run_score_cycle

    conn = get_connection()
    settings = conn.execute("SELECT enabled FROM settings WHERE id = 1").fetchone()
    conn.close()

    if not settings or not settings["enabled"]:
        logger.info("Cullarr disabled, skipping scheduled score run")
        return

    logger.info("Starting scheduled score run")
    try:
        await asyncio.wait_for(run_score_cycle(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Scheduled score run completed")
    except asyncio.TimeoutError:
        logger.error("Score run timed out after 2 hours")
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

    logger.info("Starting scheduled cull run")
    try:
        await asyncio.wait_for(run_cull_cycle(), timeout=RUN_TIMEOUT_SECONDS)
        logger.info("Scheduled cull run completed")
    except asyncio.TimeoutError:
        logger.error("Cull run timed out after 2 hours")
    except Exception as e:
        logger.error(f"Scheduled cull run failed: {e}")


def update_score_schedule(cron_expression: str):
    """Update the score schedule."""
    try:
        if scheduler.get_job("score_run"):
            scheduler.remove_job("score_run")

        trigger = CronTrigger.from_crontab(cron_expression)
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

        trigger = CronTrigger.from_crontab(cron_expression)
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
    """Get the next scheduled score run time."""
    job = scheduler.get_job("score_run")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return "Not scheduled"


def get_next_cull_run() -> str:
    """Get the next scheduled cull run time."""
    job = scheduler.get_job("cull_run")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
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
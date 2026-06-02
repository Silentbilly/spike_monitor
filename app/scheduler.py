"""
APScheduler setup for all periodic scan jobs.

Job types:
  - daily_spike_scan      — 1D OHLCV scan for new spike candidates
  - watchlist_update_4h   — 4h consolidation + breakdown scan
  - watchlist_update_1h   — 1h breakdown + update scan
  - health_ping           — periodic health check + Telegram ping
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from app.config import Config

logger = logging.getLogger(__name__)


def build_scheduler(config: "Config") -> AsyncIOScheduler:
    """Create a configured APScheduler instance (not yet started)."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    return scheduler


def register_jobs(
    scheduler: AsyncIOScheduler,
    daily_scan_fn,
    watchlist_4h_fn,
    watchlist_1h_fn,
    health_fn,
    config: "Config",
) -> None:
    """
    Register all cron jobs on the scheduler.

    Pass in async callable references from the application context.
    """
    scheduler.add_job(
        daily_scan_fn,
        trigger=CronTrigger.from_crontab(config.daily_scan_cron),
        id="daily_spike_scan",
        name="Daily Spike Scan (1D)",
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Registered daily_spike_scan: %s", config.daily_scan_cron)

    scheduler.add_job(
        watchlist_4h_fn,
        trigger=CronTrigger.from_crontab(config.scan_4h_cron),
        id="watchlist_4h",
        name="Watchlist Scan (4H)",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("Registered watchlist_4h: %s", config.scan_4h_cron)

    scheduler.add_job(
        watchlist_1h_fn,
        trigger=CronTrigger.from_crontab(config.scan_1h_cron),
        id="watchlist_1h",
        name="Watchlist Scan (1H)",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("Registered watchlist_1h: %s", config.scan_1h_cron)

    scheduler.add_job(
        health_fn,
        trigger=CronTrigger.from_crontab(config.health_ping_cron),
        id="health_ping",
        name="Health Ping",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("Registered health_ping: %s", config.health_ping_cron)

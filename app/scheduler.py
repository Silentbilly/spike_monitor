"""
APScheduler setup for all periodic scan jobs.

Job types:
  - daily_spike_scan      — 1D OHLCV scan for new spike candidates
  - watchlist_update_4h   — 4h consolidation + breakdown scan
  - watchlist_update_1h   — 1h breakdown + update scan
  - health_ping           — periodic health check + Telegram ping

ADDITIVE IBC jobs:
  - ibc_impulse_scan      — Phase 1 full-universe impulse scan (every 4h by default)
  - ibc_base_monitor      — Phase 2 base/level monitoring (every 30 min by default)
  - ibc_breakout_check    — Phase 3 breakout detection (every 15 min by default)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

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
    daily_scan_fn: Callable,
    watchlist_4h_fn: Callable,
    watchlist_1h_fn: Callable,
    health_fn: Callable,
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


def register_ibc_jobs(
    scheduler: AsyncIOScheduler,
    ibc_impulse_fn: Callable,
    ibc_monitor_fn: Callable,
    ibc_breakout_fn: Callable,
    config: "Config",
) -> None:
    """
    Register the three IBC-specific scheduler jobs.

    Kept separate from register_jobs() so existing callers are unaffected.
    Call this immediately after register_jobs() during Application.start().
    """
    scheduler.add_job(
        ibc_impulse_fn,
        trigger=CronTrigger.from_crontab(config.ibc_impulse_scan_cron),
        id="ibc_impulse_scan",
        name="IBC Phase 1 — Impulse Scan",
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Registered ibc_impulse_scan: %s", config.ibc_impulse_scan_cron)

    scheduler.add_job(
        ibc_monitor_fn,
        trigger=CronTrigger.from_crontab(config.ibc_monitor_cron),
        id="ibc_base_monitor",
        name="IBC Phase 2 — Base Monitor",
        replace_existing=True,
        misfire_grace_time=120,
    )
    logger.info("Registered ibc_base_monitor: %s", config.ibc_monitor_cron)

    scheduler.add_job(
        ibc_breakout_fn,
        trigger=CronTrigger.from_crontab(config.ibc_breakout_cron),
        id="ibc_breakout_check",
        name="IBC Phase 3 — Breakout Check",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("Registered ibc_breakout_check: %s", config.ibc_breakout_cron)

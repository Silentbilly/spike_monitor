"""Health check service — collects system status and sends periodic pings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.domain.models import HealthCheckLog
from app.services.notification_service import NotificationService
from app.storage.repositories import (
    HealthRepository,
    NotificationRepository,
    SpikeEventRepository,
    WatchlistRepository,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class HealthService:
    """Aggregates system health metrics and optionally notifies."""

    # Track last scan timestamps in memory (reset on restart)
    last_daily_scan: datetime | None = None
    last_4h_scan: datetime | None = None
    last_1h_scan: datetime | None = None

    def __init__(
        self,
        spike_repo: SpikeEventRepository,
        watchlist_repo: WatchlistRepository,
        notif_repo: NotificationRepository,
        health_repo: HealthRepository,
        notif_service: NotificationService,
    ) -> None:
        self._spikes = spike_repo
        self._watchlist = watchlist_repo
        self._notif = notif_repo
        self._health = health_repo
        self._notif_service = notif_service

    async def run_health_check(self, send_ping: bool = True) -> HealthCheckLog:
        now = utcnow()
        cutoff_24h = now - timedelta(hours=24)

        active_watchlist = await self._watchlist.get_active()
        recent_spikes = await self._spikes.get_recent(hours=24)
        error_count = await self._notif.count_errors_since(since=cutoff_24h)

        log = HealthCheckLog(
            checked_at=now,
            watchlist_count=len(active_watchlist),
            active_spikes_24h=len(recent_spikes),
            errors_24h=error_count,
            last_daily_scan=HealthService.last_daily_scan,
            last_4h_scan=HealthService.last_4h_scan,
            last_1h_scan=HealthService.last_1h_scan,
            details=f"Health check OK at {now.isoformat()}",
        )

        await self._health.save(log)

        if send_ping:
            await self._notif_service.send_health(log)

        return log

    @classmethod
    def mark_daily_scan(cls) -> None:
        cls.last_daily_scan = utcnow()

    @classmethod
    def mark_4h_scan(cls) -> None:
        cls.last_4h_scan = utcnow()

    @classmethod
    def mark_1h_scan(cls) -> None:
        cls.last_1h_scan = utcnow()

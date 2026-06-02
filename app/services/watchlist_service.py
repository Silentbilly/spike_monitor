"""
Watchlist Service.

Manages the lifecycle of spike setups:
  - Adding new strong spike candidates to the watchlist
  - Expiring stale setups
  - Checking cooldown to avoid duplicate additions
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.config import Config
from app.domain.enums import NotificationType, SetupStatus
from app.domain.models import SpikeEvent, WatchlistItem
from app.storage.repositories import NotificationRepository, WatchlistRepository
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class WatchlistService:
    """Manages the spike watchlist lifecycle."""

    def __init__(
        self,
        watchlist_repo: WatchlistRepository,
        notification_repo: NotificationRepository,
        config: Config,
    ) -> None:
        self._watchlist = watchlist_repo
        self._notif = notification_repo
        self._config = config

    async def add_if_eligible(self, event: SpikeEvent) -> tuple[bool, str]:
        """
        Add a SpikeEvent to the watchlist if it passes eligibility checks.

        Returns:
            (added: bool, reason: str)
        """
        # Must be a strong spike or meet watchlist score threshold
        if not event.is_strong and event.score < self._config.min_score_watchlist:
            return False, f"Score {event.score:.1f} < min_score_watchlist {self._config.min_score_watchlist}"

        # Cooldown check: don't add the same symbol twice within cooldown window
        existing = await self._watchlist.get_by_symbol(event.symbol)
        for item in existing:
            if item.status not in [SetupStatus.EXPIRED, SetupStatus.INVALIDATED, SetupStatus.BREAKDOWN_CONFIRMED]:
                return False, f"Already on watchlist with status={item.status}"

        # Cooldown vs last notification
        already = await self._notif.already_sent(
            notification_type=NotificationType.STRONG_SPIKE_WATCHLIST,
            reference_id=event.id,
            within_hours=self._config.signal_cooldown_hours,
        )
        if already:
            return False, "Recently notified — cooldown active"

        now = utcnow()
        expires_at = now + timedelta(hours=self._config.watchlist_ttl_hours)

        # Set invalidation level: above spike high (price would fully reclaim move)
        invalidation = event.spike_high * 1.005  # 0.5% above spike high

        item = WatchlistItem(
            spike_event_id=event.id,
            symbol=event.symbol,
            added_at=now,
            expires_at=expires_at,
            spike_high=event.spike_high,
            spike_open=event.spike_open,
            spike_pct=event.spike_pct,
            initial_score=event.score,
            invalidation_level=invalidation,
            current_score=event.score,
            retrace_pct=event.retrace_pct,
        )

        await self._watchlist.save(item)
        logger.info("Added %s to watchlist (score=%.1f, expires=%s)", event.symbol, event.score, expires_at.isoformat())
        return True, f"Added with score={event.score:.1f}, expires={expires_at.date()}"

    async def expire_stale_items(self) -> list[WatchlistItem]:
        """
        Mark expired and invalidated items and return them.

        An item expires if:
          - current time > expires_at
          - status is still WATCHING or CONSOLIDATING
        """
        now = utcnow()
        active = await self._watchlist.get_active()
        expired: list[WatchlistItem] = []

        for item in active:
            if now > item.expires_at:
                item.status = SetupStatus.EXPIRED
                await self._watchlist.save(item)
                expired.append(item)
                logger.info("Expired watchlist item: %s (added=%s)", item.symbol, item.added_at.date())

        return expired

    async def get_active_items(self) -> list[WatchlistItem]:
        return await self._watchlist.get_active()

    async def update_item(self, item: WatchlistItem) -> None:
        await self._watchlist.save(item)

    async def cleanup_terminal(self) -> int:
        """Delete terminal items older than TTL from DB."""
        cutoff = utcnow() - timedelta(hours=self._config.watchlist_ttl_hours * 2)
        deleted = await self._watchlist.delete_expired(before=cutoff)
        if deleted:
            logger.info("Cleaned up %d terminal watchlist items", deleted)
        return deleted

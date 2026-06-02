"""
Unit / integration tests for watchlist lifecycle:
  - expiry logic
  - deduplication of notifications
  - status transitions
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock


class TestWatchlistExpiry:
    """Tests for WatchlistService.expire_stale_items."""

    def _make_item(self, hours_old: float, status="watching") -> MagicMock:
        from app.domain.models import WatchlistItem
        item = MagicMock(spec=WatchlistItem)
        item.symbol = "TESTUSDT"
        item.spike_pct = 55.0
        item.retrace_pct = 75.0
        now = datetime.now(timezone.utc)
        item.added_at = now - timedelta(hours=hours_old)
        item.expires_at = now - timedelta(hours=max(0, hours_old - 168))
        item.status = status
        return item

    @pytest.mark.asyncio
    async def test_expired_item_detected(self):
        """Item past its expires_at should be marked EXPIRED."""
        from app.services.watchlist_service import WatchlistService
        from app.domain.enums import SetupStatus

        watchlist_repo = AsyncMock()
        notif_repo = AsyncMock()
        config = MagicMock()
        config.watchlist_ttl_hours = 168.0

        svc = WatchlistService(watchlist_repo, notif_repo, config)

        now = datetime.now(timezone.utc)
        expired_item = MagicMock()
        expired_item.symbol = "EXPIREDUSDT"
        expired_item.spike_pct = 60.0
        expired_item.retrace_pct = 80.0
        expired_item.added_at = now - timedelta(days=8)
        expired_item.expires_at = now - timedelta(hours=1)  # already expired
        expired_item.status = SetupStatus.WATCHING

        watchlist_repo.get_active = AsyncMock(return_value=[expired_item])
        watchlist_repo.save = AsyncMock()

        expired = await svc.expire_stale_items()

        assert len(expired) == 1
        assert expired[0].status == SetupStatus.EXPIRED
        watchlist_repo.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_item_not_expired(self):
        """Item with future expires_at should not be marked expired."""
        from app.services.watchlist_service import WatchlistService
        from app.domain.enums import SetupStatus

        watchlist_repo = AsyncMock()
        notif_repo = AsyncMock()
        config = MagicMock()
        config.watchlist_ttl_hours = 168.0

        svc = WatchlistService(watchlist_repo, notif_repo, config)

        now = datetime.now(timezone.utc)
        fresh_item = MagicMock()
        fresh_item.symbol = "FRESHUSDT"
        fresh_item.expires_at = now + timedelta(hours=100)
        fresh_item.status = SetupStatus.WATCHING

        watchlist_repo.get_active = AsyncMock(return_value=[fresh_item])
        watchlist_repo.save = AsyncMock()

        expired = await svc.expire_stale_items()

        assert len(expired) == 0
        watchlist_repo.save.assert_not_called()


class TestNotificationDeduplication:
    """Tests for NotificationRepository.already_sent idempotency."""

    @pytest.mark.asyncio
    async def test_already_sent_returns_true_when_recent(self):
        """already_sent returns True if a recent successful notification exists."""
        from app.storage.repositories import NotificationRepository
        from app.domain.enums import NotificationType

        session = AsyncMock()
        repo = NotificationRepository(session)

        # Mock scalar to return a truthy value (a row exists)
        mock_scalar = MagicMock()
        mock_scalar.scalar = MagicMock(return_value=MagicMock())  # not None
        session.execute = AsyncMock(return_value=mock_scalar)

        result = await repo.already_sent(
            notification_type=NotificationType.SPIKE_CANDIDATE,
            reference_id="some-id-123",
            within_hours=6.0,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_already_sent_returns_false_when_none(self):
        """already_sent returns False if no matching notification found."""
        from app.storage.repositories import NotificationRepository
        from app.domain.enums import NotificationType

        session = AsyncMock()
        repo = NotificationRepository(session)

        mock_scalar = MagicMock()
        mock_scalar.scalar = MagicMock(return_value=None)  # no record
        session.execute = AsyncMock(return_value=mock_scalar)

        result = await repo.already_sent(
            notification_type=NotificationType.SPIKE_CANDIDATE,
            reference_id="missing-id",
            within_hours=6.0,
        )
        assert result is False


class TestSpikeEventStatus:
    """Test SpikeEvent status enum usage."""

    def test_new_event_default_status(self):
        from app.domain.models import SpikeEvent
        from app.domain.enums import SetupStatus, Timeframe, SpikeStrength
        from app.utils.time import utcnow

        event = SpikeEvent(
            symbol="TESTUSDT",
            detected_at=utcnow(),
            spike_candle_ts=utcnow(),
            timeframe=Timeframe.D1,
            spike_open=100.0, spike_high=150.0, spike_close=105.0,
            spike_low=98.0, spike_volume=5_000_000, avg_volume_20d=1_000_000,
            spike_pct=50.0, close_pct_from_high=30.0,
            clv=-0.8, rv20=5.0, atr_14=3.0, atr_multiple=4.5,
            retrace_pct=80.0, current_price=110.0,
            strength=SpikeStrength.STRONG,
            score=72.5,
        )
        assert event.status == SetupStatus.NEW
        assert event.is_strong is False  # not yet set

    def test_strong_spike_flag(self):
        from app.domain.models import SpikeEvent
        from app.domain.enums import SetupStatus, Timeframe, SpikeStrength
        from app.utils.time import utcnow

        event = SpikeEvent(
            symbol="EXTREMEUSDT",
            detected_at=utcnow(),
            spike_candle_ts=utcnow(),
            timeframe=Timeframe.D1,
            spike_open=1.0, spike_high=2.1, spike_close=1.05,
            spike_low=0.98, spike_volume=10_000_000, avg_volume_20d=1_000_000,
            spike_pct=110.0, close_pct_from_high=50.0,
            clv=-0.9, rv20=10.0, atr_14=0.05, atr_multiple=22.0,
            retrace_pct=95.0, current_price=1.02,
            strength=SpikeStrength.EXTREME,
            score=88.0,
            is_strong=True,
        )
        assert event.is_strong is True
        assert event.spike_pct == pytest.approx(110.0)

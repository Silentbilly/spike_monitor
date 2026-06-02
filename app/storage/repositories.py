"""
Repository layer — thin CRUD wrappers mapping between Pydantic models and ORM rows.

All methods are async and accept/return domain models.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import NotificationType, SetupStatus
from app.domain.models import (
    BreakdownSignal,
    HealthCheckLog,
    NotificationLog,
    SpikeEvent,
    WatchlistItem,
)
from app.storage.schema import (
    BreakdownSignalRow,
    HealthCheckLogRow,
    NotificationLogRow,
    SpikeEventRow,
    WatchlistItemRow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapper helpers
# ---------------------------------------------------------------------------

def _row_to_spike(row: SpikeEventRow) -> SpikeEvent:
    from app.domain.enums import SpikeStrength, Timeframe
    return SpikeEvent(
        id=row.id,
        symbol=row.symbol,
        detected_at=_ensure_tz(row.detected_at),
        spike_candle_ts=_ensure_tz(row.spike_candle_ts),
        timeframe=Timeframe(row.timeframe),
        spike_open=row.spike_open,
        spike_high=row.spike_high,
        spike_close=row.spike_close,
        spike_low=row.spike_low,
        spike_volume=row.spike_volume,
        avg_volume_20d=row.avg_volume_20d,
        spike_pct=row.spike_pct,
        close_pct_from_high=row.close_pct_from_high,
        clv=row.clv,
        rv20=row.rv20,
        atr_14=row.atr_14,
        atr_multiple=row.atr_multiple,
        retrace_pct=row.retrace_pct,
        current_price=row.current_price,
        strength=SpikeStrength(row.strength),
        score=row.score,
        status=SetupStatus(row.status),
        is_strong=row.is_strong,
        chart_path=row.chart_path,
        explanation=row.explanation,
        funding_rate=row.funding_rate,
        open_interest=row.open_interest,
    )


def _spike_to_row(m: SpikeEvent) -> SpikeEventRow:
    return SpikeEventRow(
        id=m.id, symbol=m.symbol,
        detected_at=m.detected_at, spike_candle_ts=m.spike_candle_ts,
        timeframe=m.timeframe if isinstance(m.timeframe, str) else m.timeframe.value,
        spike_open=m.spike_open, spike_high=m.spike_high,
        spike_close=m.spike_close, spike_low=m.spike_low,
        spike_volume=m.spike_volume, avg_volume_20d=m.avg_volume_20d,
        spike_pct=m.spike_pct, close_pct_from_high=m.close_pct_from_high,
        clv=m.clv, rv20=m.rv20, atr_14=m.atr_14, atr_multiple=m.atr_multiple,
        retrace_pct=m.retrace_pct, current_price=m.current_price,
        strength=m.strength if isinstance(m.strength, str) else m.strength.value,
        score=m.score,
        status=m.status if isinstance(m.status, str) else m.status.value,
        is_strong=m.is_strong, chart_path=m.chart_path,
        explanation=m.explanation, funding_rate=m.funding_rate,
        open_interest=m.open_interest,
    )


def _row_to_watchlist(row: WatchlistItemRow) -> WatchlistItem:
    return WatchlistItem(
        id=row.id, spike_event_id=row.spike_event_id,
        symbol=row.symbol, added_at=_ensure_tz(row.added_at),
        expires_at=_ensure_tz(row.expires_at),
        spike_high=row.spike_high, spike_open=row.spike_open,
        spike_pct=row.spike_pct, initial_score=row.initial_score,
        consolidation_low=row.consolidation_low,
        consolidation_high=row.consolidation_high,
        post_spike_swing_low=row.post_spike_swing_low,
        breakdown_level=row.breakdown_level,
        invalidation_level=row.invalidation_level,
        status=SetupStatus(row.status),
        last_checked_1h=_ensure_tz(row.last_checked_1h) if row.last_checked_1h else None,
        last_checked_4h=_ensure_tz(row.last_checked_4h) if row.last_checked_4h else None,
        current_score=row.current_score, retrace_pct=row.retrace_pct,
        failed_bounce_detected=row.failed_bounce_detected,
        consolidation_detected=row.consolidation_detected,
    )


def _watchlist_to_row(m: WatchlistItem) -> WatchlistItemRow:
    return WatchlistItemRow(
        id=m.id, spike_event_id=m.spike_event_id,
        symbol=m.symbol, added_at=m.added_at, expires_at=m.expires_at,
        spike_high=m.spike_high, spike_open=m.spike_open,
        spike_pct=m.spike_pct, initial_score=m.initial_score,
        consolidation_low=m.consolidation_low, consolidation_high=m.consolidation_high,
        post_spike_swing_low=m.post_spike_swing_low,
        breakdown_level=m.breakdown_level, invalidation_level=m.invalidation_level,
        status=m.status if isinstance(m.status, str) else m.status.value,
        last_checked_1h=m.last_checked_1h, last_checked_4h=m.last_checked_4h,
        current_score=m.current_score, retrace_pct=m.retrace_pct,
        failed_bounce_detected=m.failed_bounce_detected,
        consolidation_detected=m.consolidation_detected,
    )


def _row_to_breakdown(row: BreakdownSignalRow) -> BreakdownSignal:
    from app.domain.enums import BreakdownQuality, Timeframe
    return BreakdownSignal(
        id=row.id, watchlist_item_id=row.watchlist_item_id,
        symbol=row.symbol, triggered_at=_ensure_tz(row.triggered_at),
        timeframe=Timeframe(row.timeframe),
        breakdown_price=row.breakdown_price, breakdown_level=row.breakdown_level,
        breakdown_volume=row.breakdown_volume, avg_volume=row.avg_volume,
        volume_confirmed=row.volume_confirmed, score=row.score,
        quality=BreakdownQuality(row.quality), spike_pct=row.spike_pct,
        retrace_pct=row.retrace_pct, consolidation_bars=row.consolidation_bars,
        chart_path=row.chart_path, explanation=row.explanation,
    )


def _ensure_tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# SpikeEvent repository
# ---------------------------------------------------------------------------

class SpikeEventRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, event: SpikeEvent) -> None:
        row = _spike_to_row(event)
        merged = await self._s.merge(row)
        await self._s.commit()

    async def get(self, event_id: str) -> Optional[SpikeEvent]:
        row = await self._s.get(SpikeEventRow, event_id)
        return _row_to_spike(row) if row else None

    async def get_by_symbol_since(self, symbol: str, since: datetime) -> list[SpikeEvent]:
        result = await self._s.execute(
            select(SpikeEventRow).where(
                SpikeEventRow.symbol == symbol,
                SpikeEventRow.detected_at >= since,
            ).order_by(SpikeEventRow.detected_at.desc())
        )
        return [_row_to_spike(r) for r in result.scalars()]

    async def get_recent(self, hours: int = 24) -> list[SpikeEvent]:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await self._s.execute(
            select(SpikeEventRow).where(SpikeEventRow.detected_at >= cutoff)
            .order_by(SpikeEventRow.detected_at.desc())
        )
        return [_row_to_spike(r) for r in result.scalars()]

    async def update_status(self, event_id: str, status: SetupStatus) -> None:
        await self._s.execute(
            update(SpikeEventRow)
            .where(SpikeEventRow.id == event_id)
            .values(status=status.value)
        )
        await self._s.commit()


# ---------------------------------------------------------------------------
# WatchlistItem repository
# ---------------------------------------------------------------------------

class WatchlistRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, item: WatchlistItem) -> None:
        row = _watchlist_to_row(item)
        await self._s.merge(row)
        await self._s.commit()

    async def get(self, item_id: str) -> Optional[WatchlistItem]:
        row = await self._s.get(WatchlistItemRow, item_id)
        return _row_to_watchlist(row) if row else None

    async def get_active(self) -> list[WatchlistItem]:
        """Return all non-terminal watchlist items."""
        terminal = [SetupStatus.BREAKDOWN_CONFIRMED.value, SetupStatus.EXPIRED.value,
                    SetupStatus.INVALIDATED.value]
        result = await self._s.execute(
            select(WatchlistItemRow).where(WatchlistItemRow.status.notin_(terminal))
            .order_by(WatchlistItemRow.added_at.desc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def get_by_symbol(self, symbol: str) -> list[WatchlistItem]:
        result = await self._s.execute(
            select(WatchlistItemRow).where(WatchlistItemRow.symbol == symbol)
            .order_by(WatchlistItemRow.added_at.desc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def get_all(self) -> list[WatchlistItem]:
        result = await self._s.execute(
            select(WatchlistItemRow).order_by(WatchlistItemRow.added_at.desc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def delete_expired(self, before: datetime) -> int:
        result = await self._s.execute(
            delete(WatchlistItemRow).where(
                WatchlistItemRow.expires_at < before,
                WatchlistItemRow.status.in_([SetupStatus.EXPIRED.value, SetupStatus.INVALIDATED.value]),
            )
        )
        await self._s.commit()
        return result.rowcount  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# BreakdownSignal repository
# ---------------------------------------------------------------------------

class BreakdownRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, signal: BreakdownSignal) -> None:
        from app.storage.schema import BreakdownSignalRow as Row
        row = BreakdownSignalRow(
            id=signal.id, watchlist_item_id=signal.watchlist_item_id,
            symbol=signal.symbol, triggered_at=signal.triggered_at,
            timeframe=signal.timeframe if isinstance(signal.timeframe, str) else signal.timeframe.value,
            breakdown_price=signal.breakdown_price,
            breakdown_level=signal.breakdown_level,
            breakdown_volume=signal.breakdown_volume,
            avg_volume=signal.avg_volume,
            volume_confirmed=signal.volume_confirmed,
            score=signal.score,
            quality=signal.quality if isinstance(signal.quality, str) else signal.quality.value,
            spike_pct=signal.spike_pct,
            retrace_pct=signal.retrace_pct,
            consolidation_bars=signal.consolidation_bars,
            chart_path=signal.chart_path,
            explanation=signal.explanation,
        )
        self._s.add(row)
        await self._s.commit()

    async def get_by_watchlist_item(self, item_id: str) -> list[BreakdownSignal]:
        result = await self._s.execute(
            select(BreakdownSignalRow).where(BreakdownSignalRow.watchlist_item_id == item_id)
        )
        return [_row_to_breakdown(r) for r in result.scalars()]


# ---------------------------------------------------------------------------
# NotificationLog repository
# ---------------------------------------------------------------------------

class NotificationRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, log: NotificationLog) -> None:
        from app.storage.schema import NotificationLogRow as Row
        row = NotificationLogRow(
            id=log.id, notification_type=log.notification_type if isinstance(log.notification_type, str) else log.notification_type.value,
            symbol=log.symbol, reference_id=log.reference_id,
            sent_at=log.sent_at,
            telegram_message_id=log.telegram_message_id,
            chat_id=log.chat_id, success=log.success,
            error_message=log.error_message,
        )
        self._s.add(row)
        await self._s.commit()

    async def already_sent(
        self,
        notification_type: NotificationType,
        reference_id: str,
        within_hours: float = 6.0,
    ) -> bool:
        """Check idempotency — has this notification been sent recently?"""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
        result = await self._s.execute(
            select(NotificationLogRow).where(
                NotificationLogRow.notification_type == notification_type.value,
                NotificationLogRow.reference_id == reference_id,
                NotificationLogRow.sent_at >= cutoff,
                NotificationLogRow.success == True,
            ).limit(1)
        )
        return result.scalar() is not None

    async def count_errors_since(self, since: datetime) -> int:
        from sqlalchemy import func
        result = await self._s.execute(
            select(func.count()).select_from(NotificationLogRow).where(
                NotificationLogRow.success == False,
                NotificationLogRow.sent_at >= since,
            )
        )
        return result.scalar() or 0


# ---------------------------------------------------------------------------
# HealthCheckLog repository
# ---------------------------------------------------------------------------

class HealthRepository:

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, log: HealthCheckLog) -> None:
        from app.storage.schema import HealthCheckLogRow as Row
        row = HealthCheckLogRow(
            id=log.id, checked_at=log.checked_at,
            watchlist_count=log.watchlist_count,
            active_spikes_24h=log.active_spikes_24h,
            errors_24h=log.errors_24h,
            last_daily_scan=log.last_daily_scan,
            last_4h_scan=log.last_4h_scan,
            last_1h_scan=log.last_1h_scan,
            details=log.details,
        )
        self._s.add(row)
        await self._s.commit()

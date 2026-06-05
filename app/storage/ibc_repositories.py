"""
IBC Repository layer.

Provides async CRUD wrappers for all three IBC tables.
Each repository is session-bound and stateless beyond the session.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ibc_models import (
    IBCBreakoutEvent,
    IBCStatus,
    IBCWatchlistEntry,
    ImpulseDirection,
    ImpulseEvent,
)
from app.storage.schema import IBCBreakoutEventRow, IBCWatchlistRow, ImpulseEventRow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timezone helper (re-used pattern from existing repositories.py)
# ---------------------------------------------------------------------------


def _ensure_tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Mappers: ORM ↔ Domain
# ---------------------------------------------------------------------------


def _row_to_impulse(row: ImpulseEventRow) -> ImpulseEvent:
    return ImpulseEvent(
        id=row.id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        direction=ImpulseDirection(row.direction),
        detected_at=_ensure_tz(row.detected_at),  # type: ignore[arg-type]
        start_price=row.start_price,
        end_price=row.end_price,
        move_pct=row.move_pct,
        bar_count=row.bar_count,
        avg_impulse_volume=row.avg_impulse_volume,
        avg_20_volume=row.avg_20_volume,
        rv_impulse=row.rv_impulse,
        atr14=row.atr14,
        atr_multiple=row.atr_multiple,
    )


def _impulse_to_row(m: ImpulseEvent) -> ImpulseEventRow:
    return ImpulseEventRow(
        id=m.id,
        symbol=m.symbol,
        timeframe=m.timeframe,
        direction=m.direction if isinstance(m.direction, str) else m.direction.value,
        detected_at=m.detected_at,
        start_price=m.start_price,
        end_price=m.end_price,
        move_pct=m.move_pct,
        bar_count=m.bar_count,
        avg_impulse_volume=m.avg_impulse_volume,
        avg_20_volume=m.avg_20_volume,
        rv_impulse=m.rv_impulse,
        atr14=m.atr14,
        atr_multiple=m.atr_multiple,
    )


def _row_to_watchlist(row: IBCWatchlistRow) -> IBCWatchlistEntry:
    return IBCWatchlistEntry(
        id=row.id,
        impulse_event_id=row.impulse_event_id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        direction=ImpulseDirection(row.direction),
        added_at=_ensure_tz(row.added_at),  # type: ignore[arg-type]
        expires_at=_ensure_tz(row.expires_at),  # type: ignore[arg-type]
        impulse_start_price=row.impulse_start_price,
        impulse_end_price=row.impulse_end_price,
        impulse_move_pct=row.impulse_move_pct,
        impulse_rv=row.impulse_rv,
        impulse_atr_multiple=row.impulse_atr_multiple,
        level_price=row.level_price,
        level_touches=row.level_touches,
        level_cluster_high=row.level_cluster_high,
        level_cluster_low=row.level_cluster_low,
        base_range_pct=row.base_range_pct,
        base_candle_count=row.base_candle_count,
        base_avg_volume=row.base_avg_volume,
        breakout_price=row.breakout_price,
        breakout_volume_confirmed=row.breakout_volume_confirmed,
        status=IBCStatus(row.status),
        base_alert_sent=row.base_alert_sent,
        last_checked_at=_ensure_tz(row.last_checked_at),
        last_breakout_alert_at=_ensure_tz(row.last_breakout_alert_at),
    )


def _watchlist_to_row(m: IBCWatchlistEntry) -> IBCWatchlistRow:
    return IBCWatchlistRow(
        id=m.id,
        impulse_event_id=m.impulse_event_id,
        symbol=m.symbol,
        timeframe=m.timeframe,
        direction=m.direction if isinstance(m.direction, str) else m.direction.value,
        added_at=m.added_at,
        expires_at=m.expires_at,
        impulse_start_price=m.impulse_start_price,
        impulse_end_price=m.impulse_end_price,
        impulse_move_pct=m.impulse_move_pct,
        impulse_rv=m.impulse_rv,
        impulse_atr_multiple=m.impulse_atr_multiple,
        level_price=m.level_price,
        level_touches=m.level_touches,
        level_cluster_high=m.level_cluster_high,
        level_cluster_low=m.level_cluster_low,
        base_range_pct=m.base_range_pct,
        base_candle_count=m.base_candle_count,
        base_avg_volume=m.base_avg_volume,
        breakout_price=m.breakout_price,
        breakout_volume_confirmed=m.breakout_volume_confirmed,
        status=m.status if isinstance(m.status, str) else m.status.value,
        base_alert_sent=m.base_alert_sent,
        last_checked_at=m.last_checked_at,
        last_breakout_alert_at=m.last_breakout_alert_at,
    )


def _row_to_breakout(row: IBCBreakoutEventRow) -> IBCBreakoutEvent:
    return IBCBreakoutEvent(
        id=row.id,
        watchlist_entry_id=row.watchlist_entry_id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        direction=ImpulseDirection(row.direction),
        triggered_at=_ensure_tz(row.triggered_at),  # type: ignore[arg-type]
        breakout_price=row.breakout_price,
        level_price=row.level_price,
        distance_pct=row.distance_pct,
        volume_confirmed=row.volume_confirmed,
        breakout_volume=row.breakout_volume,
        avg_volume=row.avg_volume,
        impulse_move_pct=row.impulse_move_pct,
        impulse_rv=row.impulse_rv,
        impulse_atr_multiple=row.impulse_atr_multiple,
        level_touches=row.level_touches,
        base_range_pct=row.base_range_pct,
        base_volume_decay=row.base_volume_decay,
        score=row.score,
        chart_path=row.chart_path,
        explanation=row.explanation,
    )


def _breakout_to_row(m: IBCBreakoutEvent) -> IBCBreakoutEventRow:
    return IBCBreakoutEventRow(
        id=m.id,
        watchlist_entry_id=m.watchlist_entry_id,
        symbol=m.symbol,
        timeframe=m.timeframe,
        direction=m.direction if isinstance(m.direction, str) else m.direction.value,
        triggered_at=m.triggered_at,
        breakout_price=m.breakout_price,
        level_price=m.level_price,
        distance_pct=m.distance_pct,
        volume_confirmed=m.volume_confirmed,
        breakout_volume=m.breakout_volume,
        avg_volume=m.avg_volume,
        impulse_move_pct=m.impulse_move_pct,
        impulse_rv=m.impulse_rv,
        impulse_atr_multiple=m.impulse_atr_multiple,
        level_touches=m.level_touches,
        base_range_pct=m.base_range_pct,
        base_volume_decay=m.base_volume_decay,
        score=m.score,
        chart_path=m.chart_path,
        explanation=m.explanation,
    )


# ---------------------------------------------------------------------------
# ImpulseEvent Repository
# ---------------------------------------------------------------------------


class ImpulseEventRepository:
    """CRUD for Phase 1 impulse events."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, event: ImpulseEvent) -> None:
        row = _impulse_to_row(event)
        await self._s.merge(row)
        await self._s.commit()

    async def get(self, event_id: str) -> Optional[ImpulseEvent]:
        row = await self._s.get(ImpulseEventRow, event_id)
        return _row_to_impulse(row) if row else None

    async def get_recent(
        self, hours: int = 24, symbol: Optional[str] = None
    ) -> list[ImpulseEvent]:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = select(ImpulseEventRow).where(
            ImpulseEventRow.detected_at >= cutoff
        )
        if symbol:
            stmt = stmt.where(ImpulseEventRow.symbol == symbol)
        stmt = stmt.order_by(ImpulseEventRow.detected_at.desc())
        result = await self._s.execute(stmt)
        return [_row_to_impulse(r) for r in result.scalars()]

    async def get_all(self, limit: int = 200) -> list[ImpulseEvent]:
        result = await self._s.execute(
            select(ImpulseEventRow)
            .order_by(ImpulseEventRow.detected_at.desc())
            .limit(limit)
        )
        return [_row_to_impulse(r) for r in result.scalars()]


# ---------------------------------------------------------------------------
# IBCWatchlist Repository
# ---------------------------------------------------------------------------


class IBCWatchlistRepository:
    """CRUD for IBC watchlist entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, entry: IBCWatchlistEntry) -> None:
        row = _watchlist_to_row(entry)
        await self._s.merge(row)
        await self._s.commit()

    async def get(self, entry_id: str) -> Optional[IBCWatchlistEntry]:
        row = await self._s.get(IBCWatchlistRow, entry_id)
        return _row_to_watchlist(row) if row else None

    async def get_active_entry(
        self,
        symbol: str,
        timeframe: str,
        direction: ImpulseDirection,
    ) -> Optional[IBCWatchlistEntry]:
        """Return an active (non-terminal) entry for a symbol+tf+direction combo."""
        terminal = [
            IBCStatus.BREAKOUT_CONFIRMED.value,
            IBCStatus.EXPIRED.value,
            IBCStatus.INVALIDATED.value,
        ]
        result = await self._s.execute(
            select(IBCWatchlistRow)
            .where(
                IBCWatchlistRow.symbol == symbol,
                IBCWatchlistRow.timeframe == timeframe,
                IBCWatchlistRow.direction == direction.value,
                IBCWatchlistRow.status.notin_(terminal),
            )
            .limit(1)
        )
        row = result.scalar()
        return _row_to_watchlist(row) if row else None

    async def get_pending_base_entries(self) -> list[IBCWatchlistEntry]:
        """Entries that still need base confirmation (Phases 1 and partially 2)."""
        pending_statuses = [
            IBCStatus.IMPULSE_DETECTED.value,
            IBCStatus.BASE_FORMING.value,
        ]
        result = await self._s.execute(
            select(IBCWatchlistRow)
            .where(IBCWatchlistRow.status.in_(pending_statuses))
            .order_by(IBCWatchlistRow.added_at.asc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def get_base_confirmed_entries(self) -> list[IBCWatchlistEntry]:
        """Entries with confirmed base waiting for breakout (Phase 3)."""
        result = await self._s.execute(
            select(IBCWatchlistRow)
            .where(IBCWatchlistRow.status == IBCStatus.BASE_CONFIRMED.value)
            .order_by(IBCWatchlistRow.added_at.asc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def get_all_active(self) -> list[IBCWatchlistEntry]:
        """All non-terminal entries."""
        terminal = [
            IBCStatus.BREAKOUT_CONFIRMED.value,
            IBCStatus.EXPIRED.value,
            IBCStatus.INVALIDATED.value,
        ]
        result = await self._s.execute(
            select(IBCWatchlistRow)
            .where(IBCWatchlistRow.status.notin_(terminal))
            .order_by(IBCWatchlistRow.added_at.desc())
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def get_all(self, limit: int = 200) -> list[IBCWatchlistEntry]:
        result = await self._s.execute(
            select(IBCWatchlistRow)
            .order_by(IBCWatchlistRow.added_at.desc())
            .limit(limit)
        )
        return [_row_to_watchlist(r) for r in result.scalars()]

    async def delete_terminal(self, before: datetime) -> int:
        """Remove terminal entries older than `before`."""
        terminal = [
            IBCStatus.BREAKOUT_CONFIRMED.value,
            IBCStatus.EXPIRED.value,
            IBCStatus.INVALIDATED.value,
        ]
        result = await self._s.execute(
            delete(IBCWatchlistRow).where(
                IBCWatchlistRow.expires_at < before,
                IBCWatchlistRow.status.in_(terminal),
            )
        )
        await self._s.commit()
        return result.rowcount  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# IBCBreakout Repository
# ---------------------------------------------------------------------------


class IBCBreakoutRepository:
    """CRUD for confirmed IBC breakout events."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, event: IBCBreakoutEvent) -> None:
        row = _breakout_to_row(event)
        self._s.add(row)
        await self._s.commit()

    async def get(self, event_id: str) -> Optional[IBCBreakoutEvent]:
        row = await self._s.get(IBCBreakoutEventRow, event_id)
        return _row_to_breakout(row) if row else None

    async def get_recent(self, hours: int = 48) -> list[IBCBreakoutEvent]:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await self._s.execute(
            select(IBCBreakoutEventRow)
            .where(IBCBreakoutEventRow.triggered_at >= cutoff)
            .order_by(IBCBreakoutEventRow.triggered_at.desc())
        )
        return [_row_to_breakout(r) for r in result.scalars()]

    async def get_all(self, limit: int = 200) -> list[IBCBreakoutEvent]:
        result = await self._s.execute(
            select(IBCBreakoutEventRow)
            .order_by(IBCBreakoutEventRow.triggered_at.desc())
            .limit(limit)
        )
        return [_row_to_breakout(r) for r in result.scalars()]

    async def get_by_symbol(self, symbol: str) -> list[IBCBreakoutEvent]:
        result = await self._s.execute(
            select(IBCBreakoutEventRow)
            .where(IBCBreakoutEventRow.symbol == symbol)
            .order_by(IBCBreakoutEventRow.triggered_at.desc())
        )
        return [_row_to_breakout(r) for r in result.scalars()]

"""
Phase 1 — Impulse Detector Service.

Scans the full liquid universe for impulse moves on 1H and 15M timeframes.
On detection, persists ImpulseEvent and adds to IBCWatchlistEntry.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from app.config import Config
from app.domain.enums import Timeframe
from app.domain.ibc_models import IBCStatus, IBCWatchlistEntry, ImpulseDirection, ImpulseEvent
from app.domain.ibc_rules import evaluate_impulse
from app.domain.models import InstrumentInfo
from app.exchanges.bybit import BybitAdapter
from app.storage.ibc_repositories import IBCWatchlistRepository, ImpulseEventRepository
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# Timeframes scanned for IBC impulses
IBC_SCAN_TIMEFRAMES: list[Timeframe] = [Timeframe.H1, Timeframe.M15]

# Bybit interval strings for these timeframes
_TF_INTERVAL: dict[Timeframe, str] = {
    Timeframe.H1: "60",
    Timeframe.M15: "15",
}

# Candle bars to fetch (must cover 20-bar baseline + impulse window + ATR14)
_CANDLE_LIMIT = 60


class ImpulseDetectorService:
    """
    Full-universe scanner for IBC impulse events (Phase 1).

    One instance can be shared across jobs; all state is held in DB.
    """

    def __init__(
        self,
        exchange: BybitAdapter,
        impulse_repo: ImpulseEventRepository,
        watchlist_repo: IBCWatchlistRepository,
        config: Config,
    ) -> None:
        self._exchange = exchange
        self._impulse_repo = impulse_repo
        self._watchlist_repo = watchlist_repo
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_universe(
        self, instruments: list[InstrumentInfo]
    ) -> list[ImpulseEvent]:
        """
        Scan all instruments on all IBC timeframes for both directions.

        Returns every new ImpulseEvent that was saved.  Concurrency is
        capped at SCAN_CONCURRENCY (from constants).
        """
        from app.constants import SCAN_CONCURRENCY

        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        tasks = [
            self._scan_symbol(instrument, tf, semaphore)
            for instrument in instruments
            for tf in IBC_SCAN_TIMEFRAMES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[ImpulseEvent] = []
        for r in results:
            if isinstance(r, Exception):
                logger.debug("IBC scan task error: %s", r)
            elif r is not None:
                events.append(r)

        logger.info(
            "IBC impulse scan complete: %d instruments × %d TFs → %d new events",
            len(instruments),
            len(IBC_SCAN_TIMEFRAMES),
            len(events),
        )
        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _scan_symbol(
        self,
        instrument: InstrumentInfo,
        tf: Timeframe,
        semaphore: asyncio.Semaphore,
    ) -> Optional[ImpulseEvent]:
        async with semaphore:
            try:
                return await self._process_symbol(instrument.symbol, tf)
            except Exception as exc:
                logger.debug(
                    "IBC scan failed for %s [%s]: %s",
                    instrument.symbol,
                    tf.value,
                    exc,
                )
                return None

    async def _process_symbol(
        self, symbol: str, tf: Timeframe
    ) -> Optional[ImpulseEvent]:
        interval = _TF_INTERVAL[tf]
        candles = await self._exchange.get_klines(
            symbol=symbol, interval=interval, limit=_CANDLE_LIMIT
        )
        if not candles:
            return None

        # Check both directions; pick the stronger impulse if both qualify
        best_event: Optional[ImpulseEvent] = None

        for direction in (ImpulseDirection.UP, ImpulseDirection.DOWN):
            # Skip if already on watchlist for this symbol+tf+direction
            already = await self._watchlist_repo.get_active_entry(symbol, tf.value, direction)
            if already is not None:
                continue

            result = evaluate_impulse(
                candles=candles,
                direction=direction,
                impulse_min_pct=self._cfg.ibc_impulse_min_pct,
                impulse_max_bars=self._cfg.ibc_impulse_max_bars,
                impulse_rv_min=self._cfg.ibc_impulse_rv_min,
                impulse_atr_min=self._cfg.ibc_impulse_atr_min,
            )

            if not result.detected:
                continue

            event = ImpulseEvent(
                symbol=symbol,
                timeframe=tf.value,
                direction=direction,
                detected_at=utcnow(),
                start_price=result.start_price,
                end_price=result.end_price,
                move_pct=result.move_pct,
                bar_count=result.bar_count,
                avg_impulse_volume=result.avg_impulse_volume,
                avg_20_volume=result.avg_20_volume,
                rv_impulse=result.rv_impulse,
                atr14=result.atr14,
                atr_multiple=result.atr_multiple,
            )

            if best_event is None or result.move_pct > best_event.move_pct:
                best_event = event

        if best_event is None:
            return None

        # Persist impulse event
        await self._impulse_repo.save(best_event)

        # Create watchlist entry
        ttl_hours = self._cfg.ibc_watchlist_ttl_hours
        watchlist_entry = IBCWatchlistEntry(
            impulse_event_id=best_event.id,
            symbol=symbol,
            timeframe=best_event.timeframe,
            direction=ImpulseDirection(best_event.direction),
            added_at=utcnow(),
            expires_at=utcnow() + timedelta(hours=ttl_hours),
            impulse_start_price=best_event.start_price,
            impulse_end_price=best_event.end_price,
            impulse_move_pct=best_event.move_pct,
            impulse_rv=best_event.rv_impulse,
            impulse_atr_multiple=best_event.atr_multiple,
            status=IBCStatus.IMPULSE_DETECTED,
        )
        await self._watchlist_repo.save(watchlist_entry)

        logger.info(
            "IBC Impulse detected: %s [%s] %s %.1f%% (rv=%.2fx, ATR×=%.1f)",
            symbol,
            tf.value,
            best_event.direction,
            best_event.move_pct,
            best_event.rv_impulse,
            best_event.atr_multiple,
        )
        return best_event

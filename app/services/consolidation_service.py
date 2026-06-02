"""
Consolidation Service.

Monitors watchlist items on 4h/1h timeframes to detect:
  - post-spike consolidation (range compression)
  - failed bounce attempts
  - retrace depth updates
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import Config
from app.domain.enums import SetupStatus, Timeframe
from app.domain.indicators import calc_avg_volume
from app.domain.models import WatchlistItem
from app.domain.rules import (
    evaluate_consolidation,
    evaluate_failed_bounce,
    evaluate_retracement,
)
from app.exchanges.base import ExchangeAdapter
from app.services.watchlist_service import WatchlistService

logger = logging.getLogger(__name__)


class ConsolidationService:
    """Analyses watchlist items for post-spike structure."""

    def __init__(
        self,
        exchange: ExchangeAdapter,
        watchlist_service: WatchlistService,
        config: Config,
    ) -> None:
        self._exchange = exchange
        self._watchlist = watchlist_service
        self._config = config

    async def scan_watchlist(self, timeframe: Timeframe) -> list[WatchlistItem]:
        """
        Scan active watchlist items on the given timeframe.

        Updates each item's consolidation/retrace state in DB.
        Returns items where consolidation was newly detected.
        """
        active = await self._watchlist.get_active_items()
        newly_consolidated: list[WatchlistItem] = []

        for item in active:
            updated = await self._analyse_item(item, timeframe)
            if updated and updated.consolidation_detected and not item.consolidation_detected:
                newly_consolidated.append(updated)

        return newly_consolidated

    async def _analyse_item(
        self,
        item: WatchlistItem,
        timeframe: Timeframe,
    ) -> Optional[WatchlistItem]:
        """Update a single watchlist item with latest structure analysis."""
        try:
            candles = await self._exchange.get_klines(
                symbol=item.symbol,
                interval=timeframe.value,
                limit=100,
            )
        except Exception as exc:
            logger.warning("Kline fetch error for %s/%s: %s", item.symbol, timeframe.value, exc)
            return None

        if len(candles) < 10:
            return None

        # Find candles after the spike
        post_spike = [c for c in candles if c.timestamp >= item.added_at]
        if len(post_spike) < self._config.consolidation_min_bars:
            post_spike = candles[-20:]  # fallback to most recent

        current_price = candles[-1].close

        # --- Retracement update ---
        retrace = evaluate_retracement(
            spike_high=item.spike_high,
            spike_open=item.spike_open,
            current_price=current_price,
            retrace_threshold_pct=self._config.retrace_threshold_pct,
        )
        item.retrace_pct = retrace.retrace_pct

        # --- Invalidation check: price re-climbed above spike high ---
        if current_price > item.spike_high:
            item.status = SetupStatus.INVALIDATED
            logger.info("%s invalidated: price %f > spike high %f", item.symbol, current_price, item.spike_high)
            await self._watchlist.update_item(item)
            return item

        # --- Consolidation detection ---
        cons = evaluate_consolidation(
            candles=post_spike,
            min_bars=self._config.consolidation_min_bars,
            max_bars=self._config.consolidation_max_bars,
            max_range_pct=self._config.consolidation_max_range_pct,
            contraction_threshold=self._config.consolidation_contraction_threshold,
        )

        was_consolidating = item.consolidation_detected
        item.consolidation_detected = cons.detected

        if cons.detected:
            item.consolidation_low = cons.range_low
            item.consolidation_high = cons.range_high
            item.post_spike_swing_low = min(
                cons.range_low,
                item.post_spike_swing_low or cons.range_low,
            )
            # Breakdown level = just below consolidation low
            item.breakdown_level = cons.range_low * 0.999
            if item.status == SetupStatus.WATCHING:
                item.status = SetupStatus.CONSOLIDATING
            logger.debug(
                "%s consolidation detected [%s]: range=%.2f%%, contraction=%.2f, quality=%.0f",
                item.symbol, timeframe.value, cons.range_pct, cons.range_contraction, cons.quality_score,
            )

        # --- Failed bounce detection ---
        if not item.failed_bounce_detected:
            fb_detected, fb_level, fb_reason = evaluate_failed_bounce(
                candles=post_spike,
                spike_high=item.spike_high,
                spike_open=item.spike_open,
                recovery_threshold_pct=50.0,
            )
            if fb_detected:
                item.failed_bounce_detected = True
                logger.info("%s failed bounce detected at ~%s", item.symbol, fb_level)

        # Update last-checked timestamps
        from app.utils.time import utcnow
        if timeframe == Timeframe.H1:
            item.last_checked_1h = utcnow()
        elif timeframe == Timeframe.H4:
            item.last_checked_4h = utcnow()

        await self._watchlist.update_item(item)
        return item

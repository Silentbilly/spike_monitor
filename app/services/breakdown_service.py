"""
Breakdown Service.

Monitors active watchlist items for breakdown triggers on 1h/4h.
Implements debounce logic to avoid false signals from wicks.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import Config
from app.domain.enums import BreakdownQuality, NotificationType, SetupStatus, Timeframe
from app.domain.indicators import calc_avg_volume
from app.domain.models import BreakdownSignal, WatchlistItem
from app.domain.rules import evaluate_breakdown
from app.domain.scoring import ScoringInput, compute_score
from app.exchanges.base import ExchangeAdapter
from app.services.watchlist_service import WatchlistService
from app.storage.repositories import BreakdownRepository, NotificationRepository
from app.utils.time import hours_since, utcnow

logger = logging.getLogger(__name__)


class BreakdownService:
    """Detects and records breakdown signals for watchlist items."""

    def __init__(
        self,
        exchange: ExchangeAdapter,
        watchlist_service: WatchlistService,
        breakdown_repo: BreakdownRepository,
        notification_repo: NotificationRepository,
        config: Config,
    ) -> None:
        self._exchange = exchange
        self._watchlist = watchlist_service
        self._breakdown_repo = breakdown_repo
        self._notif_repo = notification_repo
        self._config = config

    async def scan_for_breakdowns(
        self,
        timeframe: Timeframe,
    ) -> list[BreakdownSignal]:
        """
        Scan all active watchlist items for breakdown on the given timeframe.

        Only items with a known breakdown_level or consolidation_low are checked.
        """
        active = await self._watchlist.get_active_items()
        signals: list[BreakdownSignal] = []

        for item in active:
            if item.breakdown_level is None and item.consolidation_low is None:
                continue
            signal = await self._check_breakdown(item, timeframe)
            if signal is not None:
                signals.append(signal)

        return signals

    async def _check_breakdown(
        self,
        item: WatchlistItem,
        timeframe: Timeframe,
    ) -> Optional[BreakdownSignal]:
        """Evaluate a single watchlist item for breakdown."""
        # Idempotency: don't re-signal a confirmed breakdown
        if item.status == SetupStatus.BREAKDOWN_CONFIRMED:
            return None

        try:
            candles = await self._exchange.get_klines(
                symbol=item.symbol,
                interval=timeframe.value,
                limit=50,
            )
        except Exception as exc:
            logger.warning("Kline fetch error %s: %s", item.symbol, exc)
            return None

        if len(candles) < 5:
            return None

        # Determine breakdown support level
        support = item.breakdown_level or item.consolidation_low or item.post_spike_swing_low
        if support is None:
            return None

        avg_vol = calc_avg_volume(candles, period=20)
        latest = candles[-1]

        # --- Evaluate latest close ---
        bd_result = evaluate_breakdown(
            candle=latest,
            support_level=support,
            avg_volume=avg_vol,
            volume_multiplier=self._config.breakdown_volume_multiplier,
            confirmation_pct=self._config.breakdown_confirmation_pct,
        )

        if not bd_result.triggered:
            return None

        # --- Debounce: require 2 consecutive closes below support ---
        if len(candles) >= 2:
            prev = candles[-2]
            prev_below = prev.close < support * (1.0 - self._config.breakdown_confirmation_pct / 100.0)
            if not prev_below:
                logger.debug(
                    "%s: breakdown on latest bar but NOT previous — skipping (debounce)",
                    item.symbol,
                )
                return None

        # --- Deduplication check ---
        already_sent = await self._notif_repo.already_sent(
            notification_type=NotificationType.BREAKDOWN_CONFIRMED,
            reference_id=item.id,
            within_hours=self._config.signal_cooldown_hours,
        )
        if already_sent:
            return None

        # --- Score update ---
        age_h = hours_since(item.added_at)
        score_input = ScoringInput(
            spike_pct=item.spike_pct,
            clv=0.0,        # not recalculated here
            rv20=1.0,
            atr_multiple=1.0,
            retrace_pct=item.retrace_pct,
            consolidation_quality=80.0 if item.consolidation_detected else 20.0,
            failed_bounce=item.failed_bounce_detected,
            breakdown_quality=bd_result.quality,
            volume_confirmed=bd_result.volume_confirmed,
            v_shape_recovery=False,
            low_liquidity=False,
            age_hours=age_h,
            thin_history=False,
        )
        score_result = compute_score(score_input)

        explanation = self._build_breakdown_explanation(item, bd_result, timeframe)

        signal = BreakdownSignal(
            watchlist_item_id=item.id,
            symbol=item.symbol,
            triggered_at=utcnow(),
            timeframe=timeframe,
            breakdown_price=bd_result.breakdown_price,
            breakdown_level=bd_result.breakdown_level,
            breakdown_volume=latest.volume,
            avg_volume=avg_vol,
            volume_confirmed=bd_result.volume_confirmed,
            score=score_result.total,
            quality=bd_result.quality,
            spike_pct=item.spike_pct,
            retrace_pct=item.retrace_pct,
            consolidation_bars=0,
            explanation=explanation,
        )

        await self._breakdown_repo.save(signal)

        # Update watchlist item status
        item.status = SetupStatus.BREAKDOWN_CONFIRMED
        await self._watchlist.update_item(item)

        logger.info(
            "BREAKDOWN confirmed: %s at %.4f (quality=%s, score=%.1f)",
            item.symbol, bd_result.breakdown_price, bd_result.quality.value, score_result.total,
        )
        return signal

    @staticmethod
    def _build_breakdown_explanation(
        item: WatchlistItem,
        bd: "BreakdownRuleResult",  # type: ignore[name-defined]
        tf: Timeframe,
    ) -> str:
        parts = [
            f"{item.symbol} broke below {bd.breakdown_level:.4f} on {tf.value}.",
        ]
        if bd.volume_confirmed:
            parts.append("Breakdown accompanied by above-average volume (confirmed).")
        if item.consolidation_detected:
            parts.append("Prior consolidation phase makes this a high-quality breakdown setup.")
        if item.failed_bounce_detected:
            parts.append("Failed bounce previously observed — adds to short thesis.")
        parts.append(f"Spike was {item.spike_pct:.1f}%, retrace {item.retrace_pct:.0f}% of impulse.")
        return " ".join(parts)

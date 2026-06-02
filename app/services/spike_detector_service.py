"""
Spike Detector Service.

Scans the universe for daily spike candles and builds SpikeEvent objects
with scores and explanations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from typing import Optional

from app.config import Config
from app.constants import MIN_CANDLES_STATS, SCAN_CONCURRENCY
from app.domain.enums import SetupStatus, SpikeStrength, Timeframe
from app.domain.indicators import (
    calc_atr,
    calc_atr_multiple,
    calc_avg_volume,
    calc_clv,
    calc_retrace_pct,
    calc_rv20,
    calc_spike_pct,
)
from app.domain.models import InstrumentInfo, SpikeEvent
from app.domain.rules import evaluate_retracement, evaluate_spike
from app.domain.scoring import ScoringInput, compute_score
from app.exchanges.base import ExchangeAdapter
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class SpikeDetectorService:
    """Finds and scores daily spike events across the universe."""

    def __init__(self, exchange: ExchangeAdapter, config: Config) -> None:
        self._exchange = exchange
        self._config = config

    async def scan_universe(
        self,
        instruments: list[InstrumentInfo],
    ) -> list[SpikeEvent]:
        """
        Scan all instruments in the universe for recent daily spikes.

        Returns events sorted by score descending.
        """
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        events: list[SpikeEvent] = []

        async def process(inst: InstrumentInfo) -> Optional[SpikeEvent]:
            async with semaphore:
                return await self._check_symbol(inst.symbol)

        tasks = [process(i) for i in instruments]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                events.append(result)

        events.sort(key=lambda e: e.score, reverse=True)
        logger.info("Daily scan complete: %d spike events found (of %d instruments)", len(events), len(instruments))
        return events

    async def _check_symbol(self, symbol: str) -> Optional[SpikeEvent]:
        """Evaluate the most recent daily candle for spike criteria."""
        try:
            candles = await self._exchange.get_klines(
                symbol=symbol,
                interval=Timeframe.D1.value,
                limit=60,
            )
        except Exception as exc:
            logger.debug("Kline fetch failed for %s: %s", symbol, exc)
            return None

        if len(candles) < MIN_CANDLES_STATS:
            return None

        # Use the previous completed day (not the partial live candle)
        spike_candle = candles[-2]  # yesterday's closed candle
        history = candles[:-1]      # exclude today for avg calculations

        avg_vol = calc_avg_volume(history, period=20)
        if avg_vol < self._config.min_avg_quote_volume_usdt:
            return None

        atr = calc_atr(history, period=14)
        if atr <= 0:
            return None

        # --- Spike rule evaluation ---
        spike_result = evaluate_spike(
            candle=spike_candle,
            avg_volume_20d=avg_vol,
            spike_threshold_pct=self._config.spike_threshold_pct,
            wick_ratio_min=self._config.wick_ratio_min,
            rv20_min=self._config.rv20_min,
        )

        if not spike_result.is_spike:
            return None

        # --- Compute metrics ---
        spike_pct = spike_result.spike_pct
        clv = spike_result.clv
        rv20 = calc_rv20(spike_candle.volume, history)
        atr_mult = calc_atr_multiple(spike_pct, atr, spike_candle.open)
        current_price = candles[-1].close  # today's live close
        retrace_pct = calc_retrace_pct(spike_candle.high, spike_candle.open, current_price)
        close_pct_from_high = (spike_candle.high - spike_candle.close) / spike_candle.high * 100 if spike_candle.high > 0 else 0

        # V-shape check: did price already recover to above 50% of spike?
        retrace_result = evaluate_retracement(
            spike_high=spike_candle.high,
            spike_open=spike_candle.open,
            current_price=current_price,
            retrace_threshold_pct=self._config.retrace_threshold_pct,
        )
        v_shape = not retrace_result.qualifies and retrace_pct < 50.0

        is_strong = spike_pct >= self._config.strong_spike_threshold_pct

        # --- Scoring ---
        score_input = ScoringInput(
            spike_pct=spike_pct,
            clv=clv,
            rv20=rv20,
            atr_multiple=atr_mult,
            retrace_pct=retrace_pct,
            consolidation_quality=0.0,   # not yet evaluated
            failed_bounce=False,
            breakdown_quality=None,
            volume_confirmed=False,
            v_shape_recovery=v_shape,
            low_liquidity=avg_vol < self._config.min_avg_quote_volume_usdt * 2,
            age_hours=0.0,
            thin_history=len(candles) < 40,
        )
        score_result = compute_score(score_input)

        if score_result.total < self._config.min_score_alert:
            logger.debug("Score too low for %s: %.1f", symbol, score_result.total)
            return None

        # --- Optional enrichments ---
        funding_rate: Optional[float] = None
        open_interest: Optional[float] = None
        if self._config.enable_funding_enrichment:
            try:
                funding_rate = await self._exchange.get_funding_rate(symbol)
            except Exception:
                pass
        if self._config.enable_oi_enrichment:
            try:
                open_interest = await self._exchange.get_open_interest(symbol)
            except Exception:
                pass

        explanation = self._build_explanation(
            symbol=symbol,
            spike_pct=spike_pct,
            clv=clv,
            rv20=rv20,
            retrace_pct=retrace_pct,
            atr_mult=atr_mult,
            score=score_result.total,
            is_strong=is_strong,
            funding_rate=funding_rate,
        )

        event = SpikeEvent(
            symbol=symbol,
            detected_at=utcnow(),
            spike_candle_ts=spike_candle.timestamp.replace(tzinfo=timezone.utc) if spike_candle.timestamp.tzinfo is None else spike_candle.timestamp,
            timeframe=Timeframe.D1,
            spike_open=spike_candle.open,
            spike_high=spike_candle.high,
            spike_close=spike_candle.close,
            spike_low=spike_candle.low,
            spike_volume=spike_candle.volume,
            avg_volume_20d=avg_vol,
            spike_pct=spike_pct,
            close_pct_from_high=close_pct_from_high,
            clv=clv,
            rv20=rv20,
            atr_14=atr,
            atr_multiple=atr_mult,
            retrace_pct=retrace_pct,
            current_price=current_price,
            strength=score_result.strength,
            score=score_result.total,
            status=SetupStatus.NEW,
            is_strong=is_strong,
            explanation=explanation,
            funding_rate=funding_rate,
            open_interest=open_interest,
        )
        return event

    @staticmethod
    def _build_explanation(
        symbol: str,
        spike_pct: float,
        clv: float,
        rv20: float,
        retrace_pct: float,
        atr_mult: float,
        score: float,
        is_strong: bool,
        funding_rate: Optional[float],
    ) -> str:
        parts = [
            f"{symbol} spiked {spike_pct:.1f}% intraday but failed to hold highs.",
        ]
        if clv < -0.3:
            parts.append(f"CLV={clv:.2f} — closed near lows (bearish wick rejection).")
        if rv20 >= 2.0:
            parts.append(f"Volume was {rv20:.1f}× the 20d average (anomalous expansion).")
        if retrace_pct >= 70:
            parts.append(f"Already retraced {retrace_pct:.0f}% of the spike impulse.")
        if atr_mult >= 3:
            parts.append(f"Spike = {atr_mult:.1f}× ATR (highly abnormal move).")
        if is_strong:
            parts.append("Classified as STRONG spike — high probability of continuation lower.")
        if funding_rate is not None and funding_rate > 0.001:
            parts.append(f"Elevated funding rate ({funding_rate*100:.3f}%) may indicate crowded longs.")
        parts.append(f"Setup score: {score:.0f}/100.")
        return " ".join(parts)

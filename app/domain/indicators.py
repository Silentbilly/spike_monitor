"""
Technical indicator calculations.

All functions operate on plain lists/floats for easy unit-testing,
independent of any exchange or storage layer.
"""

from __future__ import annotations

import statistics
from typing import Optional

from app.domain.models import OHLCV


def calc_atr(candles: list[OHLCV], period: int = 14) -> float:
    """
    Average True Range (Wilder smoothing).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)

    Args:
        candles: List of OHLCV sorted oldest-first. Must have >= period+1 bars.
        period:  ATR period (default 14).

    Returns:
        ATR value, or 0.0 if insufficient data.
    """
    if len(candles) < period + 1:
        return 0.0

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        c = candles[i]
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        true_ranges.append(tr)

    # Initial ATR: simple mean of first `period` TRs
    if len(true_ranges) < period:
        return 0.0

    atr = sum(true_ranges[:period]) / period

    # Wilder's smoothing for the rest
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def calc_clv(high: float, low: float, close: float) -> float:
    """
    Close Location Value — where the close sits within the candle range.

    Formula: (close - low - (high - close)) / (high - low)
           = (2*close - high - low) / (high - low)

    Returns value in [-1, 1]:
        +1  = close at the high (bullish)
         0  = close at midpoint
        -1  = close at the low (bearish, desired for short setups)

    Returns 0.0 if high == low (doji / no-range candle).
    """
    rng = high - low
    if rng <= 0:
        return 0.0
    return (2.0 * close - high - low) / rng


def calc_rv20(spike_volume: float, candles: list[OHLCV]) -> float:
    """
    Relative Volume vs 20-day average (excluding the spike candle itself).

    Args:
        spike_volume: Volume of the spike candle.
        candles:      Historical candles (oldest-first); last element is the spike.

    Returns:
        ratio, e.g. 3.5 means 3.5× average. 0.0 if insufficient data.
    """
    history = candles[:-1]  # exclude spike candle
    if len(history) < 5:
        return 0.0
    lookback = history[-20:] if len(history) >= 20 else history
    avg = sum(c.volume for c in lookback) / len(lookback)
    if avg <= 0:
        return 0.0
    return spike_volume / avg


def calc_avg_volume(candles: list[OHLCV], period: int = 20) -> float:
    """Average quote_volume over last `period` candles."""
    lookback = candles[-period:] if len(candles) >= period else candles
    if not lookback:
        return 0.0
    return sum(c.quote_volume for c in lookback) / len(lookback)


def calc_spike_pct(spike_high: float, spike_open: float) -> float:
    """
    Spike magnitude as percentage move from open to high.

    Formula: (high - open) / open * 100
    """
    if spike_open <= 0:
        return 0.0
    return (spike_high - spike_open) / spike_open * 100.0


def calc_retrace_pct(
    spike_high: float,
    spike_open: float,
    current_price: float,
) -> float:
    """
    How much of the spike impulse has been retraced.

    Formula: (spike_high - current_price) / (spike_high - spike_open) * 100

    Returns 0–100+ (can exceed 100 if price drops below spike open).
    """
    impulse = spike_high - spike_open
    if impulse <= 0:
        return 0.0
    return (spike_high - current_price) / impulse * 100.0


def calc_atr_multiple(spike_pct: float, atr: float, spike_open: float) -> float:
    """
    How many ATRs the spike represents.

    normalises the percentage move by ATR as % of price, so the result is
    dimensionless and comparable across instruments.

    Returns 0.0 if ATR or spike_open is zero.
    """
    if atr <= 0 or spike_open <= 0:
        return 0.0
    atr_pct = (atr / spike_open) * 100.0
    if atr_pct <= 0:
        return 0.0
    return spike_pct / atr_pct


def calc_range_contraction(candles: list[OHLCV], window: int = 5) -> float:
    """
    Rolling range contraction ratio.

    Compares average candle body range of the last `window` bars vs the
    preceding `window` bars. A ratio < 1.0 indicates compression.

    Returns ratio (current / prior). 1.0 means no change, 0.5 = 50% contraction.
    """
    if len(candles) < window * 2:
        return 1.0

    recent = candles[-window:]
    prior = candles[-(window * 2):-window]

    def avg_range(bars: list[OHLCV]) -> float:
        return sum(c.high - c.low for c in bars) / len(bars)

    recent_range = avg_range(recent)
    prior_range = avg_range(prior)

    if prior_range <= 0:
        return 1.0
    return recent_range / prior_range


def has_lower_highs(candles: list[OHLCV], min_bars: int = 3) -> bool:
    """
    Returns True if there is a sequence of at least `min_bars` consecutive
    lower highs in the provided candles (oldest-first).
    """
    if len(candles) < min_bars:
        return False

    streak = 1
    for i in range(1, len(candles)):
        if candles[i].high < candles[i - 1].high:
            streak += 1
            if streak >= min_bars:
                return True
        else:
            streak = 1
    return False


def calc_realized_volatility(candles: list[OHLCV], period: int = 10) -> float:
    """
    Simple realised volatility: stdev of log returns over last `period` bars.

    Returns 0.0 if insufficient data.
    """
    lookback = candles[-period:] if len(candles) >= period else candles
    if len(lookback) < 2:
        return 0.0
    import math
    log_returns = [
        math.log(lookback[i].close / lookback[i - 1].close)
        for i in range(1, len(lookback))
        if lookback[i - 1].close > 0 and lookback[i].close > 0
    ]
    if len(log_returns) < 2:
        return 0.0
    return statistics.stdev(log_returns)


def detect_failed_bounce(
    candles: list[OHLCV],
    spike_high: float,
    spike_open: float,
    recovery_threshold_pct: float = 50.0,
) -> tuple[bool, Optional[float]]:
    """
    Detect a failed bounce attempt.

    After a spike and deep retracement the price may attempt a bounce.
    This function looks for a bar that closes above `recovery_threshold_pct`
    of the spike impulse but then fails to continue (next close is lower).

    Args:
        candles: Post-spike candles (oldest-first).
        spike_high: High of the spike candle.
        spike_open: Open of the spike candle.
        recovery_threshold_pct: % of impulse that counts as "bounce attempt".

    Returns:
        (detected: bool, bounce_level: Optional[float])
    """
    impulse = spike_high - spike_open
    if impulse <= 0 or len(candles) < 3:
        return False, None

    mid_level = spike_open + impulse * (recovery_threshold_pct / 100.0)

    for i in range(1, len(candles) - 1):
        c = candles[i]
        next_c = candles[i + 1]
        # Bounce attempt: high touched mid level but closed below it
        if c.high >= mid_level and c.close < mid_level and next_c.close < c.close:
            return True, c.high

    return False, None

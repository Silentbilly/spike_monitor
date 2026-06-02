"""
Strategy rule evaluation — formalised decision logic.

Each function returns a bool or structured result and is independently
unit-testable. No I/O or DB access here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.domain.enums import BreakdownQuality
from app.domain.indicators import (
    calc_clv,
    calc_range_contraction,
    detect_failed_bounce,
    has_lower_highs,
)
from app.domain.models import OHLCV


# ---------------------------------------------------------------------------
# A. Spike detection
# ---------------------------------------------------------------------------

@dataclass
class SpikeRuleResult:
    is_spike: bool
    spike_pct: float
    clv: float
    wick_ratio: float          # (high - close) / (high - low)
    volume_expansion: bool
    rv20: float
    reason: str                # human-readable verdict


def evaluate_spike(
    candle: OHLCV,
    avg_volume_20d: float,
    spike_threshold_pct: float = 30.0,
    wick_ratio_min: float = 0.40,
    rv20_min: float = 1.5,
) -> SpikeRuleResult:
    """
    Determine if a daily candle qualifies as a spike.

    Rules:
        1. spike_pct = (high - open) / open * 100 >= spike_threshold_pct
        2. close is significantly below high (wick_ratio >= wick_ratio_min)
           — indicates inability to hold highs
        3. volume expansion preferred (rv20 >= rv20_min), but not hard required

    Args:
        candle:              The candidate OHLCV bar.
        avg_volume_20d:      20-day average volume (excluding this candle).
        spike_threshold_pct: Minimum spike magnitude to qualify (default 30%).
        wick_ratio_min:      Minimum (high-close)/(high-low) wick ratio.
        rv20_min:            Minimum relative volume for "volume expansion" flag.
    """
    spike_pct = (candle.high - candle.open) / candle.open * 100.0 if candle.open > 0 else 0.0
    clv = calc_clv(candle.high, candle.low, candle.close)
    rng = candle.high - candle.low
    wick_ratio = (candle.high - candle.close) / rng if rng > 0 else 0.0
    rv20 = candle.volume / avg_volume_20d if avg_volume_20d > 0 else 0.0
    volume_expansion = rv20 >= rv20_min

    reasons: list[str] = []
    if spike_pct < spike_threshold_pct:
        reasons.append(f"spike_pct={spike_pct:.1f}% below threshold={spike_threshold_pct}%")
    if wick_ratio < wick_ratio_min:
        reasons.append(f"wick_ratio={wick_ratio:.2f} below min={wick_ratio_min}")

    is_spike = spike_pct >= spike_threshold_pct and wick_ratio >= wick_ratio_min

    if is_spike:
        reason = (
            f"Spike {spike_pct:.1f}%, wick_ratio={wick_ratio:.2f}, "
            f"CLV={clv:.2f}, rv20={rv20:.1f}x"
        )
    else:
        reason = "Not a spike: " + "; ".join(reasons)

    return SpikeRuleResult(
        is_spike=is_spike,
        spike_pct=spike_pct,
        clv=clv,
        wick_ratio=wick_ratio,
        volume_expansion=volume_expansion,
        rv20=rv20,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# B. Retracement quality
# ---------------------------------------------------------------------------

@dataclass
class RetraceRuleResult:
    qualifies: bool
    retrace_pct: float
    is_strong: bool     # >= strong threshold
    is_deep: bool       # >= deep threshold
    reason: str


def evaluate_retracement(
    spike_high: float,
    spike_open: float,
    current_price: float,
    retrace_threshold_pct: float = 70.0,
    strong_retrace_pct: float = 80.0,
) -> RetraceRuleResult:
    """
    Evaluate how much of the spike impulse has been retraced.

    A 70%+ retracement of the impulse (high-open) indicates the move
    was likely unsustainable. 80%+ is considered strong evidence.
    """
    impulse = spike_high - spike_open
    if impulse <= 0:
        return RetraceRuleResult(False, 0.0, False, False, "Zero impulse")

    retrace_pct = (spike_high - current_price) / impulse * 100.0
    qualifies = retrace_pct >= retrace_threshold_pct
    is_strong = retrace_pct >= strong_retrace_pct
    is_deep = retrace_pct >= 90.0

    reason = (
        f"Retrace={retrace_pct:.1f}% of impulse"
        + (" [STRONG]" if is_strong else "")
        + (" [DEEP]" if is_deep else "")
    )
    return RetraceRuleResult(
        qualifies=qualifies,
        retrace_pct=retrace_pct,
        is_strong=is_strong,
        is_deep=is_deep,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# G. Post-spike consolidation detection
# ---------------------------------------------------------------------------

@dataclass
class ConsolidationRuleResult:
    detected: bool
    range_high: float
    range_low: float
    range_pct: float
    candle_count: int
    range_contraction: float
    lower_highs: bool
    quality_score: float   # 0-100
    reason: str


def evaluate_consolidation(
    candles: list[OHLCV],
    min_bars: int = 3,
    max_bars: int = 20,
    max_range_pct: float = 8.0,
    contraction_threshold: float = 0.85,
) -> ConsolidationRuleResult:
    """
    Detect a consolidation phase in recent candles.

    A valid consolidation requires:
      - At least `min_bars` candles
      - Rolling range <= max_range_pct% of the range low
      - Range contraction ratio <= contraction_threshold vs prior bars
      - Absence of a V-shape recovery (no close above spike mid)

    Args:
        candles:               Post-spike bars (1h or 4h), oldest-first.
        min_bars:              Minimum bars inside the range.
        max_bars:              Maximum bars to look back for range.
        max_range_pct:         Max (high-low)/low * 100 to call it consolidation.
        contraction_threshold: range_contraction ratio to confirm compression.
    """
    if len(candles) < min_bars:
        return ConsolidationRuleResult(
            False, 0, 0, 0, 0, 1.0, False, 0.0,
            f"Insufficient bars: {len(candles)} < {min_bars}",
        )

    lookback = candles[-max_bars:] if len(candles) > max_bars else candles
    range_high = max(c.high for c in lookback)
    range_low = min(c.low for c in lookback)
    range_pct = (range_high - range_low) / range_low * 100.0 if range_low > 0 else 0.0

    contraction = calc_range_contraction(lookback)
    lower_h = has_lower_highs(lookback, min_bars=3)
    candle_count = len(lookback)

    # Quality scoring
    quality = 0.0
    reasons: list[str] = []

    if range_pct <= max_range_pct:
        quality += 40.0
        reasons.append(f"tight range {range_pct:.1f}%")
    else:
        reasons.append(f"wide range {range_pct:.1f}% > {max_range_pct}%")

    if contraction <= contraction_threshold:
        quality += 30.0
        reasons.append(f"contraction={contraction:.2f}")

    if lower_h:
        quality += 20.0
        reasons.append("lower highs")

    if candle_count >= 5:
        quality += 10.0

    detected = range_pct <= max_range_pct and contraction <= contraction_threshold and candle_count >= min_bars

    return ConsolidationRuleResult(
        detected=detected,
        range_high=range_high,
        range_low=range_low,
        range_pct=range_pct,
        candle_count=candle_count,
        range_contraction=contraction,
        lower_highs=lower_h,
        quality_score=min(quality, 100.0),
        reason=", ".join(reasons) if reasons else "no consolidation",
    )


# ---------------------------------------------------------------------------
# H. Failed bounce
# ---------------------------------------------------------------------------

def evaluate_failed_bounce(
    candles: list[OHLCV],
    spike_high: float,
    spike_open: float,
    recovery_threshold_pct: float = 50.0,
) -> tuple[bool, Optional[float], str]:
    """
    Wrapper around detect_failed_bounce with a human-readable reason string.

    Returns:
        (detected, bounce_level, reason)
    """
    detected, level = detect_failed_bounce(
        candles, spike_high, spike_open, recovery_threshold_pct
    )
    if detected:
        reason = f"Failed bounce at ~{level:.4f} (< {recovery_threshold_pct}% recovery held)"
    else:
        reason = "No failed bounce detected"
    return detected, level, reason


# ---------------------------------------------------------------------------
# I. Breakdown trigger
# ---------------------------------------------------------------------------

@dataclass
class BreakdownRuleResult:
    triggered: bool
    breakdown_price: float
    breakdown_level: float
    volume_confirmed: bool
    quality: BreakdownQuality
    reason: str


def evaluate_breakdown(
    candle: OHLCV,
    support_level: float,
    avg_volume: float,
    volume_multiplier: float = 1.3,
    confirmation_pct: float = 0.3,
) -> BreakdownRuleResult:
    """
    Check if a candle closes below a support level with conviction.

    Rules:
        1. candle.close < support_level * (1 - confirmation_pct/100)
           — close must be a minimum % below support (avoids wicks)
        2. Volume confirmation: volume >= avg_volume * volume_multiplier
        3. Bearish candle: close < open preferred

    Quality:
        HIGH   = close below + volume confirmed + bearish candle
        MEDIUM = close below + either volume OR bearish
        LOW    = close below only
    """
    if support_level <= 0:
        return BreakdownRuleResult(False, candle.close, support_level, False, BreakdownQuality.LOW, "Invalid support level")

    # Require close at least confirmation_pct% below support
    close_threshold = support_level * (1.0 - confirmation_pct / 100.0)
    close_below = candle.close < close_threshold

    if not close_below:
        return BreakdownRuleResult(
            False, candle.close, support_level, False, BreakdownQuality.LOW,
            f"Close {candle.close:.4f} not sufficiently below support {support_level:.4f}"
        )

    volume_confirmed = avg_volume > 0 and candle.volume >= avg_volume * volume_multiplier
    bearish_candle = candle.close < candle.open

    if volume_confirmed and bearish_candle:
        quality = BreakdownQuality.HIGH
    elif volume_confirmed or bearish_candle:
        quality = BreakdownQuality.MEDIUM
    else:
        quality = BreakdownQuality.LOW

    reason = (
        f"Breakdown: close={candle.close:.4f} below support={support_level:.4f} "
        f"({'vol confirmed' if volume_confirmed else 'no vol confirm'}, "
        f"{'bearish' if bearish_candle else 'bullish candle'})"
    )

    return BreakdownRuleResult(
        triggered=True,
        breakdown_price=candle.close,
        breakdown_level=support_level,
        volume_confirmed=volume_confirmed,
        quality=quality,
        reason=reason,
    )

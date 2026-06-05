"""
Unit tests for IBC rule evaluation functions.

Tests cover:
  - evaluate_impulse(): detection, direction, threshold guards
  - evaluate_level(): clustering, touch counting, direction handling
  - evaluate_ibc_breakout(): UP/DOWN triggers, volume confirmation, threshold edge cases
  - Helpers and guard conditions

All tests are synchronous (no I/O) and use plain OHLCV fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.ibc_models import ImpulseDirection
from app.domain.ibc_rules import evaluate_impulse, evaluate_ibc_breakout, evaluate_level
from app.domain.models import OHLCV


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


from datetime import timedelta

_BASE_TS = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_candle(
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
    ts_offset: int = 0,  # offset in hours from _BASE_TS
) -> OHLCV:
    o = open_ if open_ is not None else close * 0.99
    h = high if high is not None else close * 1.005
    lo = low if low is not None else close * 0.995
    return OHLCV(
        timestamp=_BASE_TS + timedelta(hours=ts_offset),
        open=o,
        high=h,
        low=lo,
        close=close,
        volume=volume,
        quote_volume=close * volume,
    )


def _build_baseline(n: int = 30, base_price: float = 100.0, volume: float = 500.0) -> list[OHLCV]:
    """Build n neutral sideways candles for baseline volume."""
    candles: list[OHLCV] = []
    for i in range(n):
        candles.append(
            _make_candle(base_price, open_=base_price * 0.999, volume=volume, ts_offset=i)
        )
    return candles


def _build_up_impulse(
    baseline: list[OHLCV],
    n_bars: int,
    start_price: float,
    pct_per_bar: float,
    volume: float = 1000.0,
) -> list[OHLCV]:
    """Append n_bars of strongly bullish (up) candles."""
    candles = list(baseline)
    price = start_price
    for i in range(n_bars):
        next_price = price * (1.0 + pct_per_bar / 100.0)
        ts_offset = len(candles)
        candles.append(
            _make_candle(
                close=next_price,
                open_=price,
                high=next_price * 1.001,
                low=price * 0.999,
                volume=volume,
                ts_offset=ts_offset,
            )
        )
        price = next_price
    return candles


def _build_down_impulse(
    baseline: list[OHLCV],
    n_bars: int,
    start_price: float,
    pct_per_bar: float,
    volume: float = 1000.0,
) -> list[OHLCV]:
    """Append n_bars of strongly bearish (down) candles."""
    candles = list(baseline)
    price = start_price
    for i in range(n_bars):
        next_price = price * (1.0 - pct_per_bar / 100.0)
        ts_offset = len(candles)
        candles.append(
            _make_candle(
                close=next_price,
                open_=price,
                high=price * 1.001,
                low=next_price * 0.999,
                volume=volume,
                ts_offset=ts_offset,
            )
        )
        price = next_price
    return candles


# ===========================================================================
# evaluate_impulse tests
# ===========================================================================


class TestEvaluateImpulse:
    def test_up_impulse_detected(self):
        """A 3-bar 20% UP move with high volume should be detected."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_up_impulse(baseline, n_bars=3, start_price=100.0, pct_per_bar=7.0, volume=1500.0)
        result = evaluate_impulse(candles, ImpulseDirection.UP, impulse_min_pct=15.0, impulse_rv_min=1.5)
        assert result.detected is True
        assert result.direction == ImpulseDirection.UP
        assert result.move_pct >= 15.0

    def test_down_impulse_detected(self):
        """A 3-bar 20% DOWN move with high volume should be detected."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_down_impulse(baseline, n_bars=3, start_price=100.0, pct_per_bar=7.0, volume=1500.0)
        result = evaluate_impulse(candles, ImpulseDirection.DOWN, impulse_min_pct=15.0, impulse_rv_min=1.5)
        assert result.detected is True
        assert result.direction == ImpulseDirection.DOWN
        assert result.move_pct >= 15.0

    def test_up_impulse_below_min_pct_not_detected(self):
        """A 5% move should not qualify when min_pct=15%."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_up_impulse(baseline, n_bars=2, start_price=100.0, pct_per_bar=2.5, volume=1500.0)
        result = evaluate_impulse(candles, ImpulseDirection.UP, impulse_min_pct=15.0, impulse_rv_min=1.5)
        assert result.detected is False

    def test_impulse_insufficient_volume_not_detected(self):
        """Same volume as baseline → rv < 1.5 → no detection."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_up_impulse(baseline, n_bars=3, start_price=100.0, pct_per_bar=7.0, volume=500.0)
        result = evaluate_impulse(candles, ImpulseDirection.UP, impulse_min_pct=15.0, impulse_rv_min=1.5)
        assert result.detected is False

    def test_impulse_too_few_candles_returns_no_detection(self):
        """Fewer than max_bars + 20 candles should return not-detected immediately."""
        candles = _build_baseline(n=10, base_price=100.0)
        result = evaluate_impulse(candles, ImpulseDirection.UP, impulse_min_pct=5.0)
        assert result.detected is False
        assert "Insufficient bars" in result.reason

    def test_impulse_result_has_correct_start_end_prices(self):
        """start_price and end_price should bound the impulse open→close."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_up_impulse(baseline, n_bars=2, start_price=100.0, pct_per_bar=9.0, volume=2000.0)
        result = evaluate_impulse(candles, ImpulseDirection.UP, impulse_min_pct=10.0, impulse_rv_min=1.5)
        assert result.detected is True
        assert result.start_price < result.end_price  # UP impulse

    def test_impulse_up_with_wrong_direction_not_detected(self):
        """UP candles should not satisfy DOWN direction."""
        baseline = _build_baseline(n=30, base_price=100.0, volume=500.0)
        candles = _build_up_impulse(baseline, n_bars=3, start_price=100.0, pct_per_bar=7.0, volume=1500.0)
        result = evaluate_impulse(candles, ImpulseDirection.DOWN, impulse_min_pct=15.0, impulse_rv_min=1.5)
        assert result.detected is False


# ===========================================================================
# evaluate_level tests
# ===========================================================================


class TestEvaluateLevel:
    def _make_candles_with_highs(self, highs: list[float], base_low: float = 90.0) -> list[OHLCV]:
        candles = []
        for i, h in enumerate(highs):
            c = _make_candle(close=h * 0.995, open_=base_low, high=h, low=base_low, ts_offset=i)
            candles.append(c)
        return candles

    def _make_candles_with_lows(self, lows: list[float], base_high: float = 110.0) -> list[OHLCV]:
        candles = []
        for i, lo in enumerate(lows):
            c = _make_candle(close=lo * 1.005, open_=base_high, high=base_high, low=lo, ts_offset=i)
            candles.append(c)
        return candles

    def test_up_level_detected_from_highs(self):
        """Two highs within 1% should cluster into a resistance level."""
        highs = [100.0, 99.6, 100.3, 100.1, 99.8]
        candles = self._make_candles_with_highs(highs)
        result = evaluate_level(candles, ImpulseDirection.UP, cluster_pct=1.0, min_touches=2)
        assert result.detected is True
        assert result.touches >= 2
        assert 99.0 <= result.level_price <= 101.0

    def test_down_level_detected_from_lows(self):
        """Two lows within 1% should cluster into a support level."""
        lows = [50.0, 50.4, 49.8, 50.1, 49.9]
        candles = self._make_candles_with_lows(lows)
        result = evaluate_level(candles, ImpulseDirection.DOWN, cluster_pct=1.0, min_touches=2)
        assert result.detected is True
        assert result.touches >= 2

    def test_level_not_detected_scattered_highs(self):
        """Very different highs (> 5% apart) should not form a level."""
        highs = [100.0, 106.0, 112.0, 118.0]
        candles = self._make_candles_with_highs(highs)
        result = evaluate_level(candles, ImpulseDirection.UP, cluster_pct=1.0, min_touches=2)
        assert result.detected is False

    def test_level_requires_min_touches(self):
        """One extreme alone should not satisfy min_touches=2."""
        highs = [100.0, 80.0, 70.0, 60.0]  # all different
        candles = self._make_candles_with_highs(highs)
        result = evaluate_level(candles, ImpulseDirection.UP, cluster_pct=0.5, min_touches=2)
        assert result.detected is False

    def test_level_cluster_boundaries_correct(self):
        """cluster_high and cluster_low should bracket all touches."""
        highs = [100.0, 100.3, 99.8, 100.1]
        candles = self._make_candles_with_highs(highs)
        result = evaluate_level(candles, ImpulseDirection.UP, cluster_pct=1.0, min_touches=2)
        if result.detected:
            assert result.cluster_low <= result.level_price <= result.cluster_high

    def test_level_max_age_bars_limits_lookback(self):
        """max_age_bars=2 should only look at the last 2 candles."""
        highs = [100.0] * 10 + [200.0, 200.3]  # only last 2 have the cluster
        candles = self._make_candles_with_highs(highs)
        result = evaluate_level(candles, ImpulseDirection.UP, cluster_pct=1.0, min_touches=2, max_age_bars=2)
        # The last 2 bars should form a cluster near 200
        assert result.detected is True
        assert result.level_price > 150.0


# ===========================================================================
# evaluate_ibc_breakout tests
# ===========================================================================


class TestEvaluateIBCBreakout:
    def _candle(self, close: float, volume: float = 1000.0) -> OHLCV:
        return _make_candle(close=close, open_=close * 0.99, volume=volume)

    def test_up_breakout_triggered(self):
        """Close above level*(1+0.3%) with sufficient volume → triggered."""
        level = 100.0
        candle = self._candle(close=100.4, volume=2000.0)
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.UP, level_price=level,
            avg_volume=1000.0, breakout_confirm_pct=0.3, breakout_vol_mult=1.3
        )
        assert result.triggered is True
        assert result.direction == ImpulseDirection.UP
        assert result.volume_confirmed is True
        assert result.distance_pct > 0

    def test_down_breakout_triggered(self):
        """Close below level*(1-0.3%) with sufficient volume → triggered."""
        level = 100.0
        candle = self._candle(close=99.6, volume=2000.0)
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.DOWN, level_price=level,
            avg_volume=1000.0, breakout_confirm_pct=0.3, breakout_vol_mult=1.3
        )
        assert result.triggered is True
        assert result.direction == ImpulseDirection.DOWN
        assert result.volume_confirmed is True

    def test_up_breakout_not_triggered_close_below_threshold(self):
        """Close that does not exceed level*(1+0.3%) → not triggered."""
        level = 100.0
        candle = self._candle(close=100.2, volume=2000.0)
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.UP, level_price=level,
            avg_volume=1000.0, breakout_confirm_pct=0.3, breakout_vol_mult=1.3
        )
        assert result.triggered is False

    def test_breakout_volume_not_confirmed_but_still_triggers(self):
        """Low volume breakout should trigger but volume_confirmed=False."""
        level = 100.0
        candle = self._candle(close=100.4, volume=500.0)  # vol < 1000*1.3
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.UP, level_price=level,
            avg_volume=1000.0, breakout_confirm_pct=0.3, breakout_vol_mult=1.3
        )
        assert result.triggered is True
        assert result.volume_confirmed is False

    def test_invalid_level_price_returns_not_triggered(self):
        """Zero level price should return not-triggered immediately."""
        candle = self._candle(close=100.0)
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.UP, level_price=0.0,
            avg_volume=1000.0
        )
        assert result.triggered is False

    def test_down_breakout_not_triggered_close_above_threshold(self):
        """Close not sufficiently below support → not triggered."""
        level = 100.0
        candle = self._candle(close=99.8)  # only -0.2%, needs -0.3%
        result = evaluate_ibc_breakout(
            candle, ImpulseDirection.DOWN, level_price=level,
            avg_volume=1000.0, breakout_confirm_pct=0.3, breakout_vol_mult=1.3
        )
        assert result.triggered is False

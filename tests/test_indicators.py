"""Unit tests for domain indicator calculations."""

import pytest
from tests.conftest import make_candle, make_candles_series

from app.domain.indicators import (
    calc_atr,
    calc_avg_volume,
    calc_clv,
    calc_range_contraction,
    calc_retrace_pct,
    calc_rv20,
    calc_spike_pct,
    detect_failed_bounce,
    has_lower_highs,
)


class TestCalcCLV:
    def test_close_at_high(self):
        # close == high → CLV = +1 (bullish)
        assert calc_clv(high=110, low=90, close=110) == pytest.approx(1.0)

    def test_close_at_low(self):
        # close == low → CLV = -1 (bearish)
        assert calc_clv(high=110, low=90, close=90) == pytest.approx(-1.0)

    def test_close_at_midpoint(self):
        # close == midpoint → CLV = 0
        assert calc_clv(high=110, low=90, close=100) == pytest.approx(0.0)

    def test_close_near_low(self):
        # close at 25% from low (below midpoint)
        # (2*95 - 110 - 90) / (110-90) = (190-200)/20 = -0.5
        assert calc_clv(high=110, low=90, close=95) == pytest.approx(-0.5)

    def test_zero_range_returns_zero(self):
        # Doji candle — avoid division by zero
        assert calc_clv(high=100, low=100, close=100) == 0.0


class TestCalcSpikePct:
    def test_normal_spike(self):
        # open=100, high=130 → 30%
        assert calc_spike_pct(spike_high=130, spike_open=100) == pytest.approx(30.0)

    def test_zero_open(self):
        assert calc_spike_pct(spike_high=100, spike_open=0) == 0.0

    def test_spike_below_open(self):
        # high < open — unusual but shouldn't crash
        result = calc_spike_pct(spike_high=90, spike_open=100)
        assert result < 0


class TestCalcRetracePct:
    def test_full_retrace(self):
        # current_price == spike_open → 100% retraced
        assert calc_retrace_pct(spike_high=130, spike_open=100, current_price=100) == pytest.approx(100.0)

    def test_no_retrace(self):
        # current_price == spike_high → 0% retraced
        assert calc_retrace_pct(spike_high=130, spike_open=100, current_price=130) == pytest.approx(0.0)

    def test_70pct_retrace(self):
        # impulse = 30, retraced = 21 → 70%
        current = 130 - 21  # = 109
        assert calc_retrace_pct(spike_high=130, spike_open=100, current_price=109) == pytest.approx(70.0)

    def test_over_100pct_retrace(self):
        # price below spike open → retrace > 100%
        result = calc_retrace_pct(spike_high=130, spike_open=100, current_price=90)
        assert result > 100.0

    def test_zero_impulse(self):
        assert calc_retrace_pct(spike_high=100, spike_open=100, current_price=95) == 0.0


class TestCalcRV20:
    def test_two_times_average(self):
        from tests.conftest import make_candle
        history = [make_candle(100, 110, 90, 105, volume=1_000_000) for _ in range(25)]
        spike_volume = 2_000_000
        result = calc_rv20(spike_volume, history + [make_candle(100, 200, 95, 110, volume=spike_volume)])
        assert result == pytest.approx(2.0, rel=0.05)

    def test_insufficient_history(self):
        history = [make_candle(100, 110, 90, 105, volume=1_000_000) for _ in range(3)]
        result = calc_rv20(2_000_000, history)
        assert result == 0.0


class TestCalcATR:
    def test_stable_market(self):
        # Candles with constant range of 2 should give ATR ≈ 2
        candles = [make_candle(100, 101, 99, 100) for _ in range(20)]
        atr = calc_atr(candles, period=14)
        assert 1.9 <= atr <= 2.1

    def test_insufficient_bars(self):
        candles = [make_candle(100, 101, 99, 100) for _ in range(5)]
        assert calc_atr(candles, period=14) == 0.0

    def test_expanding_range(self):
        # Range gradually increasing — ATR should increase
        candles = []
        for i in range(20):
            r = 1 + i * 0.5
            candles.append(make_candle(100, 100 + r, 100 - r, 100))
        atr = calc_atr(candles, period=14)
        assert atr > 3  # should reflect expanding range


class TestRangeContraction:
    def test_contraction_detected(self):
        """Candles with shrinking range should show contraction < 1."""
        big_candles = [make_candle(100, 110, 90, 100) for _ in range(5)]   # range=20
        small_candles = [make_candle(100, 103, 97, 100) for _ in range(5)]  # range=6
        result = calc_range_contraction(big_candles + small_candles, window=5)
        assert result < 0.5

    def test_no_contraction(self):
        """Uniform candles → ratio ≈ 1."""
        candles = [make_candle(100, 105, 95, 100) for _ in range(10)]
        result = calc_range_contraction(candles, window=5)
        assert 0.9 <= result <= 1.1


class TestHasLowerHighs:
    def test_three_lower_highs(self):
        from datetime import timedelta, timezone
        from datetime import datetime
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles = [
            make_candle(100, 110, 95, 100, ts=ts),
            make_candle(100, 108, 95, 100, ts=ts + timedelta(hours=1)),
            make_candle(100, 105, 95, 100, ts=ts + timedelta(hours=2)),
        ]
        assert has_lower_highs(candles, min_bars=3) is True

    def test_not_lower_highs(self):
        candles = [
            make_candle(100, 105, 95, 100),
            make_candle(100, 110, 95, 100),
            make_candle(100, 108, 95, 100),
        ]
        assert has_lower_highs(candles, min_bars=3) is False


class TestDetectFailedBounce:
    def test_failed_bounce_detected(self):
        """Price attempts to recover to 50% of impulse but fails."""
        from datetime import datetime, timedelta, timezone
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Spike: open=100, high=150 → impulse=50
        # Mid level = 100 + 50*0.5 = 125
        # Candle 0: below mid (post-spike trough)
        # Candle 1: high >= 125 but close < 125 (failed bounce)
        # Candle 2: close < candle 1 close (confirmation)
        candles = [
            make_candle(105, 115, 100, 108, ts=ts),
            make_candle(110, 128, 108, 118, ts=ts + timedelta(hours=1)),  # touched 125+ but closed below
            make_candle(117, 120, 110, 113, ts=ts + timedelta(hours=2)),  # lower close
        ]
        detected, level = detect_failed_bounce(candles, spike_high=150, spike_open=100, recovery_threshold_pct=50.0)
        assert detected is True
        assert level is not None

    def test_no_failed_bounce(self):
        """Candles all below mid level — no bounce attempt."""
        candles = [
            make_candle(100, 110, 95, 105),
            make_candle(105, 112, 100, 108),
            make_candle(107, 115, 102, 110),
        ]
        detected, _ = detect_failed_bounce(candles, spike_high=150, spike_open=100, recovery_threshold_pct=50.0)
        assert detected is False

"""Unit tests for spike detection rules."""

import pytest
from datetime import datetime, timezone

from tests.conftest import make_candle

from app.domain.rules import evaluate_spike, evaluate_retracement


class TestEvaluateSpike:
    def test_valid_spike(self):
        """30%+ spike with high wick and volume expansion qualifies."""
        candle = make_candle(open_=100, high=135, low=95, close=102, volume=3_000_000)
        result = evaluate_spike(
            candle=candle,
            avg_volume_20d=1_000_000,
            spike_threshold_pct=30.0,
            wick_ratio_min=0.40,
            rv20_min=1.5,
        )
        assert result.is_spike is True
        assert result.spike_pct == pytest.approx(35.0)
        assert result.volume_expansion is True

    def test_spike_below_threshold(self):
        """20% move does not qualify as 30% spike."""
        candle = make_candle(open_=100, high=120, low=98, close=101, volume=2_000_000)
        result = evaluate_spike(candle=candle, avg_volume_20d=1_000_000, spike_threshold_pct=30.0)
        assert result.is_spike is False

    def test_low_wick_ratio_fails(self):
        """Spike where price closed near the high fails wick filter."""
        # High=140, close=138 → wick=(140-138)/(140-90)=0.04 (too low)
        candle = make_candle(open_=100, high=140, low=90, close=138, volume=3_000_000)
        result = evaluate_spike(
            candle=candle,
            avg_volume_20d=1_000_000,
            spike_threshold_pct=30.0,
            wick_ratio_min=0.40,
        )
        assert result.is_spike is False

    def test_clv_near_low_on_spike(self):
        """Valid spike should have CLV near -1 (closed near low)."""
        # open=100, high=150, low=95, close=97
        # wick_ratio = (150-97)/(150-95) = 53/55 ≈ 0.96 ✓
        # spike_pct = (150-100)/100 = 50% ✓
        candle = make_candle(open_=100, high=150, low=95, close=97, volume=4_000_000)
        result = evaluate_spike(candle=candle, avg_volume_20d=1_000_000, spike_threshold_pct=30.0)
        assert result.is_spike is True
        assert result.clv < -0.5  # closed near lows

    def test_no_volume_expansion_still_spikes(self):
        """Volume expansion is not required for spike detection."""
        candle = make_candle(open_=100, high=140, low=95, close=103, volume=500_000)
        result = evaluate_spike(
            candle=candle,
            avg_volume_20d=1_000_000,
            spike_threshold_pct=30.0,
            wick_ratio_min=0.40,
        )
        # is_spike determined by magnitude + wick, not volume
        assert result.volume_expansion is False
        # May or may not be spike depending on wick_ratio
        wick = (140 - 103) / (140 - 95)
        if wick >= 0.40:
            assert result.is_spike is True


class TestEvaluateRetracement:
    def test_70pct_qualifies(self):
        result = evaluate_retracement(
            spike_high=130, spike_open=100, current_price=109,
            retrace_threshold_pct=70.0,
        )
        assert result.qualifies is True
        assert result.retrace_pct == pytest.approx(70.0)

    def test_80pct_is_strong(self):
        # 80% retrace of 30pt impulse: current = 130 - 24 = 106
        result = evaluate_retracement(
            spike_high=130, spike_open=100, current_price=106,
            retrace_threshold_pct=70.0, strong_retrace_pct=80.0,
        )
        assert result.is_strong is True

    def test_below_threshold_not_qualifies(self):
        # 50% retrace only
        result = evaluate_retracement(
            spike_high=130, spike_open=100, current_price=115,
            retrace_threshold_pct=70.0,
        )
        assert result.qualifies is False

    def test_deep_retrace_below_open(self):
        # Price dropped below spike open → retrace > 100%
        result = evaluate_retracement(
            spike_high=130, spike_open=100, current_price=90,
            retrace_threshold_pct=70.0,
        )
        assert result.qualifies is True
        assert result.retrace_pct > 100.0

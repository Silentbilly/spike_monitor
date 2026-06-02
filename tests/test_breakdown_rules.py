"""Unit tests for breakdown and consolidation rules."""

import pytest
from datetime import datetime, timedelta, timezone

from tests.conftest import make_candle

from app.domain.enums import BreakdownQuality
from app.domain.rules import evaluate_breakdown, evaluate_consolidation


class TestEvaluateBreakdown:
    def _candle_below(self, support: float = 100.0) -> "OHLCV":  # noqa: F821
        """Bearish candle that closes 0.5% below support."""
        close = support * 0.995
        return make_candle(open_=support * 1.01, high=support * 1.02, low=close * 0.998, close=close, volume=2_000_000)

    def test_high_quality_breakdown(self):
        """Bearish candle + volume → HIGH quality."""
        candle = self._candle_below(100.0)
        result = evaluate_breakdown(
            candle=candle,
            support_level=100.0,
            avg_volume=1_000_000,
            volume_multiplier=1.3,
            confirmation_pct=0.3,
        )
        assert result.triggered is True
        assert result.quality == BreakdownQuality.HIGH
        assert result.volume_confirmed is True

    def test_no_breakdown_if_close_above_support(self):
        """Close only marginally (0.5%) below support still triggers — test close ABOVE."""
        # Close at 100.2 — above support 100.0 → should NOT trigger
        candle = make_candle(open_=100.5, high=101.5, low=100.1, close=100.2, volume=2_000_000)
        result = evaluate_breakdown(
            candle=candle, support_level=100.0, avg_volume=1_000_000, confirmation_pct=0.3
        )
        assert result.triggered is False

    def test_low_quality_without_volume(self):
        """Close below support but low volume → LOW or MEDIUM quality."""
        candle = self._candle_below(100.0)
        # Override volume to be below average
        from tests.conftest import make_candle as mc
        low_vol_candle = mc(open_=101, high=101.5, low=99.3, close=99.6, volume=500_000)
        result = evaluate_breakdown(
            candle=low_vol_candle,
            support_level=100.0,
            avg_volume=1_000_000,
            volume_multiplier=1.3,
            confirmation_pct=0.3,
        )
        assert result.triggered is True
        assert result.quality in (BreakdownQuality.LOW, BreakdownQuality.MEDIUM)
        assert result.volume_confirmed is False

    def test_zero_support_no_crash(self):
        """Invalid support level → not triggered."""
        candle = make_candle(open_=100, high=105, low=95, close=98, volume=1_000_000)
        result = evaluate_breakdown(candle=candle, support_level=0.0, avg_volume=1_000_000)
        assert result.triggered is False

    def test_confirmation_pct_prevents_wick_false_signal(self):
        """
        Candle whose close is only marginally below support (< confirmation_pct)
        should not trigger.
        """
        # Support = 100, close = 99.9 → only 0.1% below, confirmation needs 0.3%
        candle = make_candle(open_=100.5, high=101, low=99.8, close=99.9, volume=2_000_000)
        result = evaluate_breakdown(
            candle=candle, support_level=100.0, avg_volume=1_000_000, confirmation_pct=0.3
        )
        assert result.triggered is False


class TestEvaluateConsolidation:
    def _tight_candles(self, n: int = 8, base: float = 100.0) -> list:
        from datetime import datetime, timedelta, timezone
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles = []
        for i in range(n):
            candles.append(make_candle(
                open_=base * 0.995, high=base * 1.02, low=base * 0.98,
                close=base * 1.001, ts=ts + timedelta(hours=i),
            ))
        return candles

    def test_tight_range_qualifies(self):
        """8 candles in a 4% range should qualify as consolidation."""
        candles = self._tight_candles(n=8, base=100.0)
        # Use contraction_threshold=1.05 to pass uniform candles (range_contraction≈1.0 ≤ 1.05)
        result = evaluate_consolidation(
            candles=candles,
            min_bars=3,
            max_range_pct=8.0,
            contraction_threshold=1.05,
        )
        assert result.detected is True
        assert result.quality_score > 30

    def test_wide_range_no_consolidation(self):
        """Candles with 20% range should NOT be consolidation."""
        from datetime import datetime, timedelta, timezone
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles = [
            make_candle(100, 120, 90, 105, ts=ts + timedelta(hours=i))
            for i in range(6)
        ]
        result = evaluate_consolidation(candles=candles, min_bars=3, max_range_pct=8.0)
        assert result.detected is False

    def test_insufficient_bars(self):
        """Too few bars → consolidation not detected."""
        candles = self._tight_candles(n=2)
        result = evaluate_consolidation(candles=candles, min_bars=3)
        assert result.detected is False

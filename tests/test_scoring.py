"""Unit tests for the 0-100 scoring model."""

import pytest

from app.domain.enums import BreakdownQuality, SpikeStrength
from app.domain.scoring import ScoringInput, ScoringResult, compute_score


def base_input(**overrides) -> ScoringInput:
    """Return a baseline scoring input with reasonable defaults."""
    defaults = dict(
        spike_pct=40.0,
        clv=-0.5,
        rv20=2.5,
        atr_multiple=3.5,
        retrace_pct=75.0,
        consolidation_quality=50.0,
        failed_bounce=False,
        breakdown_quality=None,
        volume_confirmed=False,
        v_shape_recovery=False,
        low_liquidity=False,
        age_hours=0.0,
        thin_history=False,
    )
    defaults.update(overrides)
    return ScoringInput(**defaults)


class TestScoreTotal:
    def test_score_in_range(self):
        result = compute_score(base_input())
        assert 0.0 <= result.total <= 100.0

    def test_perfect_setup_high_score(self):
        """Ideal setup with all positive factors should score high."""
        inp = base_input(
            spike_pct=80.0,
            clv=-0.9,
            rv20=4.0,
            atr_multiple=6.0,
            retrace_pct=90.0,
            consolidation_quality=90.0,
            failed_bounce=True,
            breakdown_quality=BreakdownQuality.HIGH,
            volume_confirmed=True,
        )
        result = compute_score(inp)
        assert result.total >= 70.0, f"Expected high score, got {result.total}"

    def test_weak_setup_low_score(self):
        """Weak setup: minimal spike, no retrace, no volume."""
        inp = base_input(
            spike_pct=16.0,
            clv=0.3,
            rv20=0.8,
            atr_multiple=1.0,
            retrace_pct=20.0,
            consolidation_quality=0.0,
        )
        result = compute_score(inp)
        assert result.total < 25.0, f"Expected low score, got {result.total}"


class TestPenalties:
    def test_v_shape_penalty(self):
        """V-shape recovery should reduce score by 10."""
        no_v = compute_score(base_input(v_shape_recovery=False))
        v_shape = compute_score(base_input(v_shape_recovery=True))
        assert no_v.total - v_shape.total == pytest.approx(10.0, abs=0.5)

    def test_liquidity_penalty(self):
        """Low liquidity should reduce score by 10."""
        liquid = compute_score(base_input(low_liquidity=False))
        illiquid = compute_score(base_input(low_liquidity=True))
        assert liquid.total - illiquid.total == pytest.approx(10.0, abs=0.5)

    def test_time_decay_no_penalty_within_72h(self):
        """No penalty for setups younger than 72 hours."""
        fresh = compute_score(base_input(age_hours=0.0))
        still_fresh = compute_score(base_input(age_hours=72.0))
        assert fresh.total == pytest.approx(still_fresh.total, abs=0.1)

    def test_time_decay_max_at_168h(self):
        """Maximum time decay (20 pts) at 168 hours."""
        fresh = compute_score(base_input(age_hours=0.0))
        old = compute_score(base_input(age_hours=168.0))
        assert fresh.total - old.total == pytest.approx(20.0, abs=1.0)

    def test_time_decay_intermediate(self):
        """Partial time decay between 72h and 168h."""
        fresh = compute_score(base_input(age_hours=0.0))
        stale = compute_score(base_input(age_hours=120.0))
        assert 5.0 < fresh.total - stale.total < 20.0

    def test_thin_history_penalty(self):
        """Thin history should reduce score by 5."""
        full = compute_score(base_input(thin_history=False))
        thin = compute_score(base_input(thin_history=True))
        assert full.total - thin.total == pytest.approx(5.0, abs=0.5)


class TestStrengthClassification:
    def test_extreme_spike(self):
        result = compute_score(base_input(spike_pct=120.0))
        assert result.strength == SpikeStrength.EXTREME

    def test_strong_spike(self):
        result = compute_score(base_input(spike_pct=65.0))
        assert result.strength == SpikeStrength.STRONG

    def test_moderate_spike(self):
        result = compute_score(base_input(spike_pct=40.0))
        assert result.strength == SpikeStrength.MODERATE

    def test_weak_spike(self):
        result = compute_score(base_input(spike_pct=20.0))
        assert result.strength == SpikeStrength.WEAK


class TestBreakdownQuality:
    def test_high_quality_breakdown_boosts_score(self):
        no_breakdown = compute_score(base_input())
        with_high_bd = compute_score(base_input(
            breakdown_quality=BreakdownQuality.HIGH,
            volume_confirmed=True,
        ))
        assert with_high_bd.total > no_breakdown.total

    def test_breakdown_quality_ordering(self):
        """HIGH > MEDIUM > LOW quality."""
        low = compute_score(base_input(breakdown_quality=BreakdownQuality.LOW))
        med = compute_score(base_input(breakdown_quality=BreakdownQuality.MEDIUM))
        high = compute_score(base_input(breakdown_quality=BreakdownQuality.HIGH))
        assert high.total > med.total > low.total


class TestRV20Scoring:
    def test_rv20_above_3_max_pts(self):
        result = compute_score(base_input(rv20=3.5))
        assert result.rv20_pts == pytest.approx(10.0)

    def test_rv20_zero(self):
        result = compute_score(base_input(rv20=0.0))
        assert result.rv20_pts == pytest.approx(0.0)

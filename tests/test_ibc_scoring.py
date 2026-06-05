"""
Unit tests for the IBC scoring model (score_ibc / IBCScoringInput).

Tests cover:
  - Maximum achievable score
  - Minimum score (all zeros)
  - Individual component scoring
  - Penalty application
  - Boundary edge cases
"""

from __future__ import annotations

import pytest

from app.domain.ibc_rules import IBCScoringInput, IBCScoringResult, score_ibc


def _perfect_input() -> IBCScoringInput:
    """All inputs maximised for highest possible score."""
    return IBCScoringInput(
        impulse_move_pct=60.0,        # → 20 pts
        impulse_rv=4.0,               # → 10 pts
        impulse_atr_multiple=6.0,     # → 10 pts
        level_touches=5,              # → 15 pts
        consolidation_tightness=1.0,  # → 15 pts
        volume_decay=0.3,             # → 10 pts
        breakout_volume_confirmed=True,  # → 10 pts
        breakout_candle_conviction=2.0,
        breakout_distance_pct=2.0,    # → 5 pts
        weak_volume=False,
        wide_base=False,
        stale_level=False,
    )


def _zero_input() -> IBCScoringInput:
    """All inputs at zero / worst-case values."""
    return IBCScoringInput(
        impulse_move_pct=0.0,
        impulse_rv=0.0,
        impulse_atr_multiple=0.0,
        level_touches=0,
        consolidation_tightness=15.0,
        volume_decay=1.5,
        breakout_volume_confirmed=False,
        breakout_candle_conviction=0.0,
        breakout_distance_pct=0.0,
        weak_volume=True,
        wide_base=True,
        stale_level=True,
    )


class TestIBCScoringModel:
    def test_perfect_score_is_100(self):
        result = score_ibc(_perfect_input())
        # 20 + 10 + 10 + 15 + 15 + 10 + 15 + 5 = 100
        assert result.total == 100.0

    def test_zero_input_score_is_zero(self):
        result = score_ibc(_zero_input())
        assert result.total == 0.0

    def test_score_is_bounded_0_to_100(self):
        for _ in range(5):
            inp = _perfect_input()
            inp.impulse_move_pct = 200.0  # try to overflow
            result = score_ibc(inp)
            assert 0.0 <= result.total <= 100.0

    # --- Component: impulse magnitude ---

    def test_impulse_magnitude_max_at_50pct(self):
        inp = _perfect_input()
        inp.impulse_move_pct = 50.0
        result = score_ibc(inp)
        assert result.impulse_magnitude_pts == 20.0

    def test_impulse_magnitude_mid_range(self):
        """25% move should give exactly 10 pts."""
        inp = _zero_input()
        inp.impulse_move_pct = 25.0
        inp.weak_volume = False
        inp.wide_base = False
        inp.stale_level = False
        result = score_ibc(inp)
        assert result.impulse_magnitude_pts == pytest.approx(10.0, abs=0.01)

    def test_impulse_magnitude_zero_for_low_pct(self):
        inp = _perfect_input()
        inp.impulse_move_pct = 0.0
        result = score_ibc(inp)
        assert result.impulse_magnitude_pts == 0.0

    # --- Component: level touches ---

    def test_two_touches_gives_5pts(self):
        inp = _perfect_input()
        inp.level_touches = 2
        result = score_ibc(inp)
        assert result.level_touches_pts == 5.0

    def test_three_touches_gives_10pts(self):
        inp = _perfect_input()
        inp.level_touches = 3
        result = score_ibc(inp)
        assert result.level_touches_pts == 10.0

    def test_four_touches_gives_15pts(self):
        inp = _perfect_input()
        inp.level_touches = 4
        result = score_ibc(inp)
        assert result.level_touches_pts == 15.0

    def test_one_touch_gives_0pts(self):
        inp = _perfect_input()
        inp.level_touches = 1
        result = score_ibc(inp)
        assert result.level_touches_pts == 0.0

    # --- Penalties ---

    def test_weak_volume_penalty_applied(self):
        no_pen = score_ibc(_perfect_input())
        with_pen = score_ibc(IBCScoringInput(**{**_perfect_input().__dict__, "weak_volume": True}))
        assert no_pen.total - with_pen.total == pytest.approx(10.0, abs=0.1)

    def test_wide_base_penalty_applied(self):
        no_pen = score_ibc(_perfect_input())
        with_pen = score_ibc(IBCScoringInput(**{**_perfect_input().__dict__, "wide_base": True}))
        assert no_pen.total - with_pen.total == pytest.approx(10.0, abs=0.1)

    def test_stale_level_penalty_applied(self):
        no_pen = score_ibc(_perfect_input())
        with_pen = score_ibc(IBCScoringInput(**{**_perfect_input().__dict__, "stale_level": True}))
        assert no_pen.total - with_pen.total == pytest.approx(5.0, abs=0.1)

    def test_all_penalties_together(self):
        inp = _perfect_input()
        inp.weak_volume = True
        inp.wide_base = True
        inp.stale_level = True
        result = score_ibc(inp)
        # 100 - 10 - 10 - 5 = 75
        assert result.total == pytest.approx(75.0, abs=1.0)

    # --- Volume decay component ---

    def test_volume_decay_max_pts_at_low_decay(self):
        inp = _perfect_input()
        inp.volume_decay = 0.3  # well below 0.4 threshold
        result = score_ibc(inp)
        assert result.volume_decay_pts == 10.0

    def test_volume_decay_zero_pts_at_high_decay(self):
        inp = _perfect_input()
        inp.volume_decay = 1.5  # > 1.0 — full decay
        result = score_ibc(inp)
        assert result.volume_decay_pts == 0.0

    # --- Explanation string ---

    def test_explanation_contains_total(self):
        result = score_ibc(_perfect_input())
        assert "TOTAL=" in result.explanation
        assert str(result.total) in result.explanation or f"{result.total:.1f}" in result.explanation

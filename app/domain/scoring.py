"""
Score calculation model (0–100) for spike setups and breakdown signals.

Scoring table
=============
Component                       Max pts  Notes
----------------------------------------------------------------------
Spike magnitude (spike_pct)       15     30%=5, 50%=10, 80%=15
Strong spike bonus (≥50%)          5     flat bonus
Retracement depth (retrace_pct)   15     70%=8, 80%=12, 90%=15
CLV weakness                       10     CLV=-1→10, CLV=0→0, linear
Relative volume (rv20)             10     rv≥2→5, rv≥3→10
ATR-normalised expansion           10     multiple≥3→5, ≥5→10
Post-spike consolidation            10     quality_score/10
Failed bounce detected              10     +10 flat
Breakdown quality                  10     LOW=3, MEDIUM=6, HIGH=10
Volume confirmed on breakdown       5     +5 flat
----------------------------------------------------------------------
                                  100

Penalties (applied after summing):
  -10   V-shape recovery (retrace_pct < 50 within 2 bars)
  -10   Low liquidity (avg_quote_vol < threshold)
  -10   Time decay: linear reduction if setup age > 3d, max -20 at 7d
  -5    Very thin candle count (< min_history)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.domain.enums import BreakdownQuality, SpikeStrength


@dataclass
class ScoringInput:
    """All inputs required to compute a setup score."""
    # ---- Spike metrics ----
    spike_pct: float
    clv: float                      # [-1, 1], negative = bearish
    rv20: float                     # relative volume
    atr_multiple: float             # spike / ATR
    retrace_pct: float              # % of impulse retraced

    # ---- Structure ----
    consolidation_quality: float    # 0-100
    failed_bounce: bool

    # ---- Breakdown ----
    breakdown_quality: Optional[BreakdownQuality] = None
    volume_confirmed: bool = False

    # ---- Penalties ----
    v_shape_recovery: bool = False  # retraced then quickly recovered
    low_liquidity: bool = False
    age_hours: float = 0.0          # hours since spike
    thin_history: bool = False


@dataclass
class ScoringResult:
    """Score breakdown for transparency."""
    total: float                    # 0-100
    spike_magnitude_pts: float
    strong_spike_bonus_pts: float
    retrace_pts: float
    clv_pts: float
    rv20_pts: float
    atr_pts: float
    consolidation_pts: float
    failed_bounce_pts: float
    breakdown_pts: float
    volume_confirm_pts: float
    # Penalties
    v_shape_penalty: float
    liquidity_penalty: float
    time_decay_penalty: float
    thin_history_penalty: float

    strength: SpikeStrength


def compute_score(inp: ScoringInput) -> ScoringResult:
    """
    Compute a 0-100 score for a spike/short setup.

    Higher score = stronger short setup evidence.
    """
    pts = 0.0

    # --- Spike magnitude (max 15) ---
    if inp.spike_pct >= 80:
        spike_mag = 15.0
    elif inp.spike_pct >= 50:
        spike_mag = 10.0 + (inp.spike_pct - 50.0) / 30.0 * 5.0
    elif inp.spike_pct >= 30:
        spike_mag = 5.0 + (inp.spike_pct - 30.0) / 20.0 * 5.0
    elif inp.spike_pct >= 15:
        spike_mag = (inp.spike_pct - 15.0) / 15.0 * 5.0
    else:
        spike_mag = 0.0
    spike_mag = min(spike_mag, 15.0)

    # --- Strong spike bonus (max 5) ---
    strong_bonus = 5.0 if inp.spike_pct >= 50.0 else 0.0

    # --- Retracement (max 15) ---
    if inp.retrace_pct >= 90:
        retrace_pts = 15.0
    elif inp.retrace_pct >= 80:
        retrace_pts = 12.0 + (inp.retrace_pct - 80.0) / 10.0 * 3.0
    elif inp.retrace_pct >= 70:
        retrace_pts = 8.0 + (inp.retrace_pct - 70.0) / 10.0 * 4.0
    elif inp.retrace_pct >= 50:
        retrace_pts = (inp.retrace_pct - 50.0) / 20.0 * 8.0
    else:
        retrace_pts = 0.0
    retrace_pts = min(retrace_pts, 15.0)

    # --- CLV weakness (max 10) ---
    # CLV in [-1, 1]: -1 = close at low (best), +1 = close at high (worst)
    clv_pts = max(0.0, (-inp.clv + 1.0) / 2.0 * 10.0)  # maps [-1,1] → [10,0]
    clv_pts = min(clv_pts, 10.0)

    # --- Relative volume (max 10) ---
    if inp.rv20 >= 3.0:
        rv20_pts = 10.0
    elif inp.rv20 >= 2.0:
        rv20_pts = 5.0 + (inp.rv20 - 2.0) * 5.0
    elif inp.rv20 >= 1.5:
        rv20_pts = 2.0 + (inp.rv20 - 1.5) * 6.0
    else:
        rv20_pts = max(0.0, inp.rv20 * 1.33)
    rv20_pts = min(rv20_pts, 10.0)

    # --- ATR multiple (max 10) ---
    if inp.atr_multiple >= 5.0:
        atr_pts = 10.0
    elif inp.atr_multiple >= 3.0:
        atr_pts = 5.0 + (inp.atr_multiple - 3.0) / 2.0 * 5.0
    elif inp.atr_multiple >= 1.5:
        atr_pts = (inp.atr_multiple - 1.5) / 1.5 * 5.0
    else:
        atr_pts = 0.0
    atr_pts = min(atr_pts, 10.0)

    # --- Consolidation quality (max 10) ---
    cons_pts = inp.consolidation_quality / 100.0 * 10.0
    cons_pts = min(cons_pts, 10.0)

    # --- Failed bounce (max 10) ---
    failed_bounce_pts = 10.0 if inp.failed_bounce else 0.0

    # --- Breakdown quality (max 10) ---
    bd_pts = 0.0
    if inp.breakdown_quality is not None:
        if inp.breakdown_quality == BreakdownQuality.HIGH:
            bd_pts = 10.0
        elif inp.breakdown_quality == BreakdownQuality.MEDIUM:
            bd_pts = 6.0
        elif inp.breakdown_quality == BreakdownQuality.LOW:
            bd_pts = 3.0

    # --- Volume confirmation (max 5) ---
    vol_confirm_pts = 5.0 if inp.volume_confirmed else 0.0

    # --- Raw total ---
    raw = (
        spike_mag + strong_bonus + retrace_pts + clv_pts
        + rv20_pts + atr_pts + cons_pts + failed_bounce_pts
        + bd_pts + vol_confirm_pts
    )

    # --- Penalties ---
    v_shape_pen = 10.0 if inp.v_shape_recovery else 0.0
    liq_pen = 10.0 if inp.low_liquidity else 0.0
    thin_hist_pen = 5.0 if inp.thin_history else 0.0

    # Time decay: 0 penalty up to 72h, then linear to 20 pts at 168h (7d)
    if inp.age_hours <= 72:
        time_decay_pen = 0.0
    elif inp.age_hours <= 168:
        time_decay_pen = (inp.age_hours - 72.0) / (168.0 - 72.0) * 20.0
    else:
        time_decay_pen = 20.0

    total = raw - v_shape_pen - liq_pen - thin_hist_pen - time_decay_pen
    total = max(0.0, min(100.0, total))

    # --- Spike strength classification ---
    if inp.spike_pct >= 100:
        strength = SpikeStrength.EXTREME
    elif inp.spike_pct >= 50:
        strength = SpikeStrength.STRONG
    elif inp.spike_pct >= 30:
        strength = SpikeStrength.MODERATE
    else:
        strength = SpikeStrength.WEAK

    return ScoringResult(
        total=round(total, 1),
        spike_magnitude_pts=round(spike_mag, 2),
        strong_spike_bonus_pts=strong_bonus,
        retrace_pts=round(retrace_pts, 2),
        clv_pts=round(clv_pts, 2),
        rv20_pts=round(rv20_pts, 2),
        atr_pts=round(atr_pts, 2),
        consolidation_pts=round(cons_pts, 2),
        failed_bounce_pts=failed_bounce_pts,
        breakdown_pts=round(bd_pts, 2),
        volume_confirm_pts=vol_confirm_pts,
        v_shape_penalty=v_shape_pen,
        liquidity_penalty=liq_pen,
        time_decay_penalty=round(time_decay_pen, 2),
        thin_history_penalty=thin_hist_pen,
        strength=strength,
    )

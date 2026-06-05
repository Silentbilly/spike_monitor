"""
IBC rule evaluation functions.

All functions are pure (no I/O, no DB) and fully type-annotated.
Each function returns a structured result dataclass for transparency.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.domain.ibc_models import (
    IBCBreakoutResult,
    ImpulseDirection,
    ImpulseResult,
    LevelResult,
)
from app.domain.indicators import calc_atr
from app.domain.models import OHLCV
from app.domain.rules import evaluate_consolidation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1 — Impulse detection
# ---------------------------------------------------------------------------


def evaluate_impulse(
    candles: list[OHLCV],
    direction: ImpulseDirection,
    impulse_min_pct: float = 15.0,
    impulse_max_bars: int = 5,
    impulse_rv_min: float = 1.5,
    impulse_atr_min: float = 3.0,
) -> ImpulseResult:
    """
    Detect a directional impulse in the most recent window of candles.

    Rules:
      1. Scan the last `impulse_max_bars` bars for a run of consecutive
         directional candles (each close in the impulse direction).
      2. Total move (open of first bar → close of last bar) ≥ impulse_min_pct%.
      3. Average volume of impulse bars ≥ impulse_rv_min × 20-bar prior avg.
      4. Total price move in $ ≥ impulse_atr_min × ATR14.

    The function tries every possible window of 1..impulse_max_bars at the
    tail of the provided candles and returns the *best* (largest move) valid
    result.  Candles must be provided oldest-first with ≥ 20+impulse_max_bars
    bars for meaningful ATR/volume baselines.

    Args:
        candles:           OHLCV list, oldest-first.
        direction:         ImpulseDirection.UP or ImpulseDirection.DOWN.
        impulse_min_pct:   Minimum total move (%) to qualify.
        impulse_max_bars:  Maximum consecutive bars forming the impulse.
        impulse_rv_min:    Minimum impulse avg volume / 20-bar avg.
        impulse_atr_min:   Minimum move / ATR14.

    Returns:
        ImpulseResult with detected=True on success.
    """
    _no = ImpulseResult(
        detected=False,
        direction=direction,
        start_index=0,
        end_index=0,
        move_pct=0.0,
        bar_count=0,
        avg_impulse_volume=0.0,
        avg_20_volume=0.0,
        rv_impulse=0.0,
        atr14=0.0,
        atr_multiple=0.0,
        start_price=0.0,
        end_price=0.0,
        reason="",
    )

    if len(candles) < impulse_max_bars + 20:
        r = _no
        r.reason = f"Insufficient bars: {len(candles)} < {impulse_max_bars + 20}"
        return r

    atr14 = calc_atr(candles[:-impulse_max_bars], period=14)

    # 20-bar average volume (prior to the scan window)
    prior_window = candles[-(impulse_max_bars + 20) : -impulse_max_bars]
    avg_20_vol = (
        sum(c.volume for c in prior_window) / len(prior_window)
        if prior_window
        else 0.0
    )

    best: Optional[ImpulseResult] = None

    # Try every window length from 1 to impulse_max_bars at the tail
    for window in range(1, impulse_max_bars + 1):
        tail = candles[-window:]

        # Check each bar is directional
        directional = True
        for c in tail:
            if direction == ImpulseDirection.UP and c.close <= c.open:
                directional = False
                break
            if direction == ImpulseDirection.DOWN and c.close >= c.open:
                directional = False
                break
        if not directional:
            continue

        start_price = tail[0].open
        end_price = tail[-1].close

        if start_price <= 0:
            continue

        if direction == ImpulseDirection.UP:
            move_pct = (end_price - start_price) / start_price * 100.0
        else:
            move_pct = (start_price - end_price) / start_price * 100.0

        if move_pct < impulse_min_pct:
            continue

        avg_imp_vol = sum(c.volume for c in tail) / len(tail)
        rv_impulse = avg_imp_vol / avg_20_vol if avg_20_vol > 0 else 0.0

        if rv_impulse < impulse_rv_min:
            continue

        price_move_abs = abs(end_price - start_price)
        atr_multiple = price_move_abs / atr14 if atr14 > 0 else 0.0

        if atr_multiple < impulse_atr_min:
            continue

        start_idx = len(candles) - window
        end_idx = len(candles) - 1

        candidate = ImpulseResult(
            detected=True,
            direction=direction,
            start_index=start_idx,
            end_index=end_idx,
            move_pct=move_pct,
            bar_count=window,
            avg_impulse_volume=avg_imp_vol,
            avg_20_volume=avg_20_vol,
            rv_impulse=rv_impulse,
            atr14=atr14,
            atr_multiple=atr_multiple,
            start_price=start_price,
            end_price=end_price,
            reason=(
                f"Impulse {direction.value.upper()} {move_pct:.1f}% in {window} bars, "
                f"rv={rv_impulse:.2f}x, ATR×={atr_multiple:.1f}"
            ),
        )

        if best is None or candidate.move_pct > best.move_pct:
            best = candidate

    if best is not None:
        return best

    _no.reason = (
        f"No qualifying impulse window found "
        f"(min_pct={impulse_min_pct}%, rv_min={impulse_rv_min}x, atr_min={impulse_atr_min}x)"
    )
    return _no


# ---------------------------------------------------------------------------
# Phase 2 — Level detection
# ---------------------------------------------------------------------------


def evaluate_level(
    candles: list[OHLCV],
    direction: ImpulseDirection,
    cluster_pct: float = 1.0,
    min_touches: int = 2,
    max_age_bars: int = 30,
) -> LevelResult:
    """
    Detect a horizontal price level from post-impulse candle extremes.

    For an UP impulse, collect highs of post-impulse bars and look for
    clustering at a resistance level.  For a DOWN impulse, collect lows
    and look for clustering at a support level.

    Clustering algorithm:
      1. Sort extremes.
      2. Walk through sorted extremes; any extreme within ±cluster_pct% of
         the current cluster centre merges into that cluster.
      3. Return the cluster with the most touches that satisfies min_touches.

    Args:
        candles:      Post-impulse OHLCV bars (oldest-first).
        direction:    Whether the impulse was UP or DOWN.
        cluster_pct:  Corridor width as ±% around cluster centre.
        min_touches:  Minimum extremes required to validate the level.
        max_age_bars: Look back at most this many bars.

    Returns:
        LevelResult with detected=True if a valid level found.
    """
    _no = LevelResult(
        detected=False,
        level_price=0.0,
        touches=0,
        cluster_high=0.0,
        cluster_low=0.0,
        age_bars=0,
        reason="",
    )

    lookback = candles[-max_age_bars:] if len(candles) > max_age_bars else candles
    if len(lookback) < min_touches:
        _no.reason = f"Insufficient bars: {len(lookback)} < {min_touches}"
        return _no

    # Collect extremes
    extremes: list[float] = []
    if direction == ImpulseDirection.UP:
        extremes = [c.high for c in lookback]
    else:
        extremes = [c.low for c in lookback]

    extremes_sorted = sorted(extremes)

    # Greedy clustering
    clusters: list[list[float]] = []
    for price in extremes_sorted:
        placed = False
        for cluster in clusters:
            centre = statistics.mean(cluster)
            if abs(price - centre) / centre * 100.0 <= cluster_pct:
                cluster.append(price)
                placed = True
                break
        if not placed:
            clusters.append([price])

    # Find best cluster
    best_cluster: Optional[list[float]] = None
    for cluster in clusters:
        if len(cluster) >= min_touches:
            if best_cluster is None or len(cluster) > len(best_cluster):
                best_cluster = cluster

    if best_cluster is None:
        _no.reason = (
            f"No cluster with ≥{min_touches} touches found "
            f"(cluster_pct={cluster_pct}%)"
        )
        return _no

    level_price = statistics.mean(best_cluster)
    cluster_low = min(best_cluster)
    cluster_high = max(best_cluster)
    touches = len(best_cluster)
    age_bars = len(lookback)

    return LevelResult(
        detected=True,
        level_price=level_price,
        touches=touches,
        cluster_high=cluster_high,
        cluster_low=cluster_low,
        age_bars=age_bars,
        reason=(
            f"Level at {level_price:.4f} ({touches} touches, "
            f"corridor {cluster_low:.4f}–{cluster_high:.4f})"
        ),
    )


# ---------------------------------------------------------------------------
# Phase 2b — Ceiling-based base detection
# ---------------------------------------------------------------------------


@dataclass
class CeilingBaseResult:
    """
    Output of evaluate_ceiling_base().

    Replaces the range-based consolidation check with a flat-ceiling
    clustering check: the base is valid when a statistically flat
    resistance (UP) or support (DOWN) line is formed by repeated high/low
    touches, regardless of the total height of the zone.
    """

    detected: bool
    ceiling_price: float        # representative resistance / support level
    touches: int                # bars whose high/low reached the ceiling
    flat_ratio: float           # touches / total_bars  (0–1)
    flatness_pct: float         # std(touches) / ceiling × 100  — quality metric
    vol_decay: float            # base avg vol / impulse avg vol
    total_bars: int
    reason: str


def evaluate_ceiling_base(
    bars: list[OHLCV],
    direction: ImpulseDirection,
    impulse_avg_volume: float,
    cluster_tol_pct: float = 2.0,
    min_touches: int = 8,
    min_flat_ratio: float = 0.25,
    vol_decay_thresh: float = 0.60,
) -> CeilingBaseResult:
    """
    Detect a flat ceiling (resistance for UP, support for DOWN) via high/low
    clustering.  This is the replacement for the range-width gate in Phase 2.

    Unlike the legacy `evaluate_level` + `evaluate_consolidation` combination
    that fails when the *overall* zone height is wide (e.g. due to a single
    spike wick), this function only asks: "are the *tops* (or *bottoms*)
    of the base bars converging on the same level?"

    Algorithm
    ---------
    1. Collect highs (UP) or lows (DOWN) of every bar.
    2. Greedy-cluster them with ``cluster_tol_pct`` tolerance.
    3. Pick the dominant cluster (most members).
    4. Validate:
       - touches   >= ``min_touches``
       - flat_ratio  = touches / len(bars)  >= ``min_flat_ratio``
       - vol_decay = base avg vol / impulse avg vol  < ``vol_decay_thresh``

    Args:
        bars:               Post-impulse OHLCV bars (oldest-first).
        direction:          ImpulseDirection.UP or DOWN.
        impulse_avg_volume: Average volume of the impulse bars (from Phase 1).
        cluster_tol_pct:    Highs/lows within ±this% of cluster centre are
                            grouped together.  Env: IBC_CEILING_CLUSTER_TOL_PCT.
        min_touches:        Minimum bars touching the ceiling cluster.
                            Env: IBC_CEILING_MIN_TOUCHES.
        min_flat_ratio:     Minimum fraction (0–1) of base bars that must touch
                            the ceiling.  Env: IBC_CEILING_MIN_FLAT_RATIO.
        vol_decay_thresh:   Maximum allowed ratio of base avg vol / impulse avg
                            vol.  Env: IBC_BASE_VOLUME_DECAY (shared key).

    Returns:
        CeilingBaseResult with detected=True when all conditions pass.
    """
    _no = CeilingBaseResult(
        detected=False,
        ceiling_price=0.0,
        touches=0,
        flat_ratio=0.0,
        flatness_pct=0.0,
        vol_decay=0.0,
        total_bars=len(bars),
        reason="",
    )

    if len(bars) < min_touches:
        _no.reason = f"Insufficient bars: {len(bars)} < {min_touches}"
        return _no

    # ── 1. Collect extremes ────────────────────────────────────────────────
    extremes: list[float]
    if direction == ImpulseDirection.UP:
        extremes = [b.high for b in bars]
    else:
        extremes = [b.low for b in bars]

    # ── 2. Greedy clustering ───────────────────────────────────────────────
    clusters: dict[float, list[float]] = {}   # centre → members
    for price in extremes:
        placed = False
        for centre in list(clusters.keys()):
            if abs(price - centre) / centre * 100.0 <= cluster_tol_pct:
                clusters[centre].append(price)
                placed = True
                break
        if not placed:
            clusters[price] = [price]

    if not clusters:
        _no.reason = "No price clusters found"
        return _no

    # ── 3. Dominant cluster ────────────────────────────────────────────────
    best_centre, best_members = max(clusters.items(), key=lambda kv: len(kv[1]))
    touches = len(best_members)

    # ── 4. Minimum touches ────────────────────────────────────────────────
    if touches < min_touches:
        _no.reason = (
            f"Dominant cluster has {touches} touches "
            f"(required {min_touches})"
        )
        return _no

    # ── 5. Flat ratio ─────────────────────────────────────────────────────
    flat_ratio = touches / len(bars)
    if flat_ratio < min_flat_ratio:
        _no.reason = (
            f"Flat ratio {flat_ratio:.2f} < {min_flat_ratio} "
            f"({touches}/{len(bars)} bars touch ceiling)"
        )
        return _no

    # ── 6. Flatness quality ────────────────────────────────────────────────
    arr = np.array(best_members, dtype=float)
    ceiling_price = float(arr.mean())
    flatness_pct = float(arr.std() / ceiling_price * 100.0) if ceiling_price > 0 else 0.0

    # ── 7. Volume decay ────────────────────────────────────────────────────
    base_avg_vol = sum(b.volume for b in bars) / len(bars) if bars else 0.0
    vol_decay = (
        base_avg_vol / impulse_avg_volume if impulse_avg_volume > 0 else 1.0
    )
    if vol_decay > vol_decay_thresh:
        _no.reason = (
            f"Volume decay {vol_decay:.3f} > threshold {vol_decay_thresh} "
            f"(base_avg={base_avg_vol:.0f}, impulse_avg={impulse_avg_volume:.0f})"
        )
        return _no

    return CeilingBaseResult(
        detected=True,
        ceiling_price=ceiling_price,
        touches=touches,
        flat_ratio=flat_ratio,
        flatness_pct=flatness_pct,
        vol_decay=vol_decay,
        total_bars=len(bars),
        reason=(
            f"Ceiling at {ceiling_price:.4f} | "
            f"{touches} touches ({flat_ratio*100:.1f}% of bars) | "
            f"flatness={flatness_pct:.2f}% | "
            f"vol_decay={vol_decay:.3f}x"
        ),
    )


# ---------------------------------------------------------------------------
# Phase 3 — Breakout detection
# ---------------------------------------------------------------------------


def evaluate_ibc_breakout(
    candle: OHLCV,
    direction: ImpulseDirection,
    level_price: float,
    avg_volume: float,
    breakout_confirm_pct: float = 0.3,
    breakout_vol_mult: float = 1.3,
) -> IBCBreakoutResult:
    """
    Check if a single candle constitutes a breakout from the base level.

    UP impulse  → breakout above resistance:
        close > level × (1 + breakout_confirm_pct/100)
    DOWN impulse → breakout below support:
        close < level × (1 - breakout_confirm_pct/100)

    Volume confirmation: candle.volume >= avg_volume * breakout_vol_mult.

    Args:
        candle:               Candidate breakout candle.
        direction:            ImpulseDirection of the originating impulse.
        level_price:          Resistance (UP) or support (DOWN) level.
        avg_volume:           Average volume in the base zone.
        breakout_confirm_pct: Minimum % beyond the level for confirmation.
        breakout_vol_mult:    Minimum volume multiple for confirmation.

    Returns:
        IBCBreakoutResult with triggered=True on confirmation.
    """
    if level_price <= 0:
        return IBCBreakoutResult(
            triggered=False,
            direction=direction,
            breakout_price=candle.close,
            level_price=level_price,
            volume_confirmed=False,
            candle_volume=candle.volume,
            avg_volume=avg_volume,
            distance_pct=0.0,
            reason="Invalid level price",
        )

    volume_confirmed = avg_volume > 0 and candle.volume >= avg_volume * breakout_vol_mult

    if direction == ImpulseDirection.UP:
        threshold = level_price * (1.0 + breakout_confirm_pct / 100.0)
        triggered = candle.close > threshold
        distance_pct = (candle.close - level_price) / level_price * 100.0 if triggered else 0.0
    else:
        threshold = level_price * (1.0 - breakout_confirm_pct / 100.0)
        triggered = candle.close < threshold
        distance_pct = (level_price - candle.close) / level_price * 100.0 if triggered else 0.0

    if not triggered:
        return IBCBreakoutResult(
            triggered=False,
            direction=direction,
            breakout_price=candle.close,
            level_price=level_price,
            volume_confirmed=volume_confirmed,
            candle_volume=candle.volume,
            avg_volume=avg_volume,
            distance_pct=0.0,
            reason=(
                f"Close {candle.close:.4f} did not break level {level_price:.4f} "
                f"(threshold {threshold:.4f})"
            ),
        )

    vol_str = "vol ✓" if volume_confirmed else "vol ✗"
    return IBCBreakoutResult(
        triggered=True,
        direction=direction,
        breakout_price=candle.close,
        level_price=level_price,
        volume_confirmed=volume_confirmed,
        candle_volume=candle.volume,
        avg_volume=avg_volume,
        distance_pct=distance_pct,
        reason=(
            f"Breakout {direction.value.upper()}: close={candle.close:.4f} "
            f"vs level={level_price:.4f}, dist={distance_pct:.2f}%, {vol_str}"
        ),
    )


# ---------------------------------------------------------------------------
# Scoring model (0–100)
# ---------------------------------------------------------------------------


@dataclass
class IBCScoringInput:
    """All inputs needed for IBC setup scoring."""

    # Impulse
    impulse_move_pct: float         # max 20 pts
    impulse_rv: float               # max 10 pts
    impulse_atr_multiple: float     # max 10 pts

    # Level
    level_touches: int              # max 15 pts

    # Consolidation
    consolidation_tightness: float  # range % — lower = better; max 15 pts
    volume_decay: float             # base vol / impulse vol; lower = better; max 10 pts

    # Breakout
    breakout_volume_confirmed: bool  # max 10 pts (from 15)
    breakout_candle_conviction: float  # distance_pct beyond level; max 5 pts
    breakout_distance_pct: float    # max 5 pts

    # Penalty inputs
    weak_volume: bool = False       # -10
    wide_base: bool = False         # -10
    stale_level: bool = False       # -5


@dataclass
class IBCScoringResult:
    """Detailed IBC score breakdown."""

    total: float                    # 0–100

    # Component points
    impulse_magnitude_pts: float    # max 20
    impulse_volume_pts: float       # max 10
    impulse_atr_pts: float          # max 10
    level_touches_pts: float        # max 15
    consolidation_tightness_pts: float  # max 15
    volume_decay_pts: float         # max 10
    breakout_conviction_pts: float  # max 15
    breakout_distance_pts: float    # max 5

    # Penalties
    weak_volume_penalty: float      # -10
    wide_base_penalty: float        # -10
    stale_level_penalty: float      # -5

    explanation: str


def score_ibc(inp: IBCScoringInput) -> IBCScoringResult:
    """
    Compute a 0–100 score for an IBC breakout setup.

    Scoring table
    =============
    Component                   Max pts  Notes
    ─────────────────────────────────────────────────────────
    Impulse magnitude            20      15%=5, 25%=10, 50%=20
    Impulse volume expansion     10      rv≥2→5, rv≥3→10
    Impulse ATR multiple         10      ≥3x=5, ≥5x=10
    Level touches count          15      2→5, 3→10, 4+→15
    Consolidation tightness      15      ≤3%=15, ≤6%=8, ≤10%=3
    Volume decay in base         10      ≤0.4→10, ≤0.6→7, ≤0.8→4
    Breakout conviction (vol+c)  15      vol=10, candle conviction=5
    Breakout distance from level  5      ≥1%=5, ≥0.5%=3
    ─────────────────────────────────────────────────────────
    Total raw                   100

    Penalties (applied after summing):
      weak_volume     -10   (volume not confirmed on breakout)
      wide_base       -10   (consolidation range > BASE_MAX_RANGE_PCT)
      stale_level      -5   (level age > LEVEL_MAX_AGE_BARS * 0.8)
    """
    # Impulse magnitude (max 20)
    pct = inp.impulse_move_pct
    if pct >= 50.0:
        imp_mag = 20.0
    elif pct >= 25.0:
        imp_mag = 10.0 + (pct - 25.0) / 25.0 * 10.0
    elif pct >= 15.0:
        imp_mag = 5.0 + (pct - 15.0) / 10.0 * 5.0
    else:
        imp_mag = 0.0
    imp_mag = min(imp_mag, 20.0)

    # Impulse volume expansion (max 10)
    rv = inp.impulse_rv
    if rv >= 3.0:
        imp_vol = 10.0
    elif rv >= 2.0:
        imp_vol = 5.0 + (rv - 2.0) * 5.0
    elif rv >= 1.5:
        imp_vol = 2.0 + (rv - 1.5) * 6.0
    else:
        imp_vol = max(0.0, rv * 1.33)
    imp_vol = min(imp_vol, 10.0)

    # Impulse ATR multiple (max 10)
    atm = inp.impulse_atr_multiple
    if atm >= 5.0:
        imp_atr = 10.0
    elif atm >= 3.0:
        imp_atr = 5.0 + (atm - 3.0) / 2.0 * 5.0
    elif atm >= 1.5:
        imp_atr = (atm - 1.5) / 1.5 * 5.0
    else:
        imp_atr = 0.0
    imp_atr = min(imp_atr, 10.0)

    # Level touches (max 15)
    t = inp.level_touches
    if t >= 4:
        level_pts = 15.0
    elif t == 3:
        level_pts = 10.0
    elif t == 2:
        level_pts = 5.0
    else:
        level_pts = 0.0

    # Consolidation tightness (max 15) — lower range_pct = better
    cons_pct = inp.consolidation_tightness
    if cons_pct <= 3.0:
        cons_pts = 15.0
    elif cons_pct <= 6.0:
        cons_pts = 8.0 + (6.0 - cons_pct) / 3.0 * 7.0
    elif cons_pct <= 10.0:
        cons_pts = 3.0 + (10.0 - cons_pct) / 4.0 * 5.0
    else:
        cons_pts = 0.0
    cons_pts = min(cons_pts, 15.0)

    # Volume decay (max 10) — lower ratio = more decay = better
    vd = inp.volume_decay
    if vd <= 0.4:
        vdecay_pts = 10.0
    elif vd <= 0.6:
        vdecay_pts = 7.0 + (0.6 - vd) / 0.2 * 3.0
    elif vd <= 0.8:
        vdecay_pts = 4.0 + (0.8 - vd) / 0.2 * 3.0
    else:
        vdecay_pts = max(0.0, (1.0 - vd) * 10.0)
    vdecay_pts = min(vdecay_pts, 10.0)

    # Breakout conviction: volume (max 10) + candle strength (max 5) — capped at 15
    vol_pts = 10.0 if inp.breakout_volume_confirmed else 0.0
    # Candle conviction: close-to-open distance as fraction of close (proxy for strong candle)
    candle_conv = inp.breakout_candle_conviction  # passed in from caller
    if candle_conv >= 1.0:
        candle_pts = 5.0
    elif candle_conv >= 0.5:
        candle_pts = 3.0 + (candle_conv - 0.5) / 0.5 * 2.0
    elif candle_conv > 0.0:
        candle_pts = candle_conv / 0.5 * 3.0
    else:
        candle_pts = 0.0
    breakout_pts = min(vol_pts + candle_pts, 15.0)

    # Breakout distance from level (max 5 pts — separate from conviction)
    dist = inp.breakout_distance_pct
    if dist >= 1.0:
        dist_pts = 5.0
    elif dist >= 0.5:
        dist_pts = 3.0 + (dist - 0.5) / 0.5 * 2.0
    elif dist > 0.0:
        dist_pts = dist / 0.5 * 3.0
    else:
        dist_pts = 0.0

    # Raw total
    raw = imp_mag + imp_vol + imp_atr + level_pts + cons_pts + vdecay_pts + breakout_pts + dist_pts

    # Penalties
    weak_vol_pen = 10.0 if inp.weak_volume else 0.0
    wide_base_pen = 10.0 if inp.wide_base else 0.0
    stale_pen = 5.0 if inp.stale_level else 0.0

    total = max(0.0, min(100.0, raw - weak_vol_pen - wide_base_pen - stale_pen))

    explanation = (
        f"ImpMag={imp_mag:.1f} ImpVol={imp_vol:.1f} ImpATR={imp_atr:.1f} "
        f"LvlTouches={level_pts:.1f} ConsTight={cons_pts:.1f} "
        f"VolDecay={vdecay_pts:.1f} BrkConv={breakout_pts:.1f} "
        f"| Penalties: vol={-weak_vol_pen:.0f} base={-wide_base_pen:.0f} stale={-stale_pen:.0f}"
        f" | TOTAL={total:.1f}"
    )

    return IBCScoringResult(
        total=round(total, 1),
        impulse_magnitude_pts=round(imp_mag, 2),
        impulse_volume_pts=round(imp_vol, 2),
        impulse_atr_pts=round(imp_atr, 2),
        level_touches_pts=level_pts,
        consolidation_tightness_pts=round(cons_pts, 2),
        volume_decay_pts=round(vdecay_pts, 2),
        breakout_conviction_pts=round(breakout_pts, 2),
        breakout_distance_pts=round(dist_pts, 2),
        weak_volume_penalty=weak_vol_pen,
        wide_base_penalty=wide_base_pen,
        stale_level_penalty=stale_pen,
        explanation=explanation,
    )

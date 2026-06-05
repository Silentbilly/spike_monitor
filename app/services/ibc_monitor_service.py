"""
Phase 2 — IBC Monitor Service (Base / Consolidation).

Polls the IBC watchlist for symbols with a confirmed impulse and checks
whether a valid base (horizontal level + consolidation) has formed.

On confirmation it:
  1. Marks IBCWatchlistEntry.base_alert_sent = True (deduplication).
  2. Sends a Telegram "🟡 Base Formed" alert with annotated chart PNG.
  3. Updates watchlist entry status → BASE_CONFIRMED.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402 — must be set before other imports

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.config import Config
from app.domain.enums import Timeframe
from app.domain.ibc_models import IBCStatus, IBCWatchlistEntry, ImpulseDirection
from app.domain.ibc_rules import evaluate_level
from app.domain.models import OHLCV
from app.domain.rules import evaluate_consolidation
from app.exchanges.bybit import BybitAdapter
from app.services.telegram_service import TelegramService
from app.storage.ibc_repositories import IBCWatchlistRepository
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

_TF_INTERVAL: dict[str, str] = {"60": "60", "15": "15"}
_CANDLE_LIMIT = 80


class IBCMonitorService:
    """
    Phase 2 monitor: checks each watchlist entry for base formation.

    Called periodically (every IBC_MONITOR_INTERVAL_MIN minutes).
    """

    def __init__(
        self,
        exchange: BybitAdapter,
        watchlist_repo: IBCWatchlistRepository,
        telegram: TelegramService,
        config: Config,
    ) -> None:
        self._exchange = exchange
        self._watchlist_repo = watchlist_repo
        self._tg = telegram
        self._cfg = config
        Path(config.chart_output_dir).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_monitor_cycle(self) -> list[IBCWatchlistEntry]:
        """
        Poll all active (non-base-confirmed, non-expired) watchlist entries.

        Returns entries for which a base was newly confirmed.
        """
        entries = await self._watchlist_repo.get_pending_base_entries()

        # Expire stale entries
        now = utcnow()
        confirmed: list[IBCWatchlistEntry] = []

        for entry in entries:
            if entry.expires_at < now:
                entry.status = IBCStatus.EXPIRED
                await self._watchlist_repo.save(entry)
                logger.debug("IBC watchlist entry expired: %s [%s]", entry.symbol, entry.timeframe)
                continue

            try:
                result = await self._check_base(entry)
                if result:
                    confirmed.append(result)
            except Exception as exc:
                logger.error(
                    "IBC monitor error for %s [%s]: %s",
                    entry.symbol,
                    entry.timeframe,
                    exc,
                    exc_info=True,
                )

        logger.info(
            "IBC base monitor cycle: %d entries checked, %d base(s) confirmed",
            len(entries),
            len(confirmed),
        )
        return confirmed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_base(
        self, entry: IBCWatchlistEntry
    ) -> Optional[IBCWatchlistEntry]:
        """
        Check if a base has formed for the given watchlist entry.

        Returns the updated entry if base confirmed, else None.
        """
        if entry.base_alert_sent:
            return None  # already alerted; wait for breakout phase

        interval = _TF_INTERVAL.get(entry.timeframe, entry.timeframe)
        candles = await self._exchange.get_klines(
            symbol=entry.symbol, interval=interval, limit=_CANDLE_LIMIT
        )
        if not candles:
            return None

        direction = ImpulseDirection(entry.direction)

        # Post-impulse candles: everything after the impulse end price formation
        # We take all candles but the last N (conservatively use all for level detection)
        post_impulse = candles  # level detection works on the full window

        # ── 1. Level detection ─────────────────────────────────────────
        level_result = evaluate_level(
            candles=post_impulse,
            direction=direction,
            cluster_pct=self._cfg.ibc_level_cluster_pct,
            min_touches=self._cfg.ibc_level_min_touches,
            max_age_bars=self._cfg.ibc_level_max_age_bars,
        )

        if not level_result.detected:
            logger.debug(
                "IBC [%s %s %s] level not found: %s",
                entry.symbol, entry.timeframe, direction.value,
                level_result.reason,
            )
            return None

        # ── 2. Consolidation detection ──────────────────────────────────
        lookback_bars = min(len(candles), self._cfg.ibc_level_max_age_bars)
        cons_candles = candles[-lookback_bars:]

        cons_result = evaluate_consolidation(
            candles=cons_candles,
            min_bars=self._cfg.consolidation_min_bars,
            max_bars=min(lookback_bars, self._cfg.consolidation_max_bars),
            max_range_pct=self._cfg.ibc_base_max_range_pct,
            contraction_threshold=self._cfg.consolidation_contraction_threshold,
        )

        if not cons_result.detected:
            logger.debug(
                "IBC [%s %s %s] consolidation not found: %s",
                entry.symbol, entry.timeframe, direction.value,
                cons_result.reason,
            )
            return None

        # ── 3. Volume decay check ────────────────────────────────────────
        base_avg_vol = (
            sum(c.volume for c in cons_candles) / len(cons_candles)
            if cons_candles
            else 0.0
        )
        impulse_avg_vol = entry.impulse_rv * (
            entry.impulse_end_price  # proxy; actual impulse avg stored in DB
        )
        # Use the stored impulse avg volume directly
        impulse_ref_vol = entry.impulse_rv  # rv = base_avg / 20_avg, we'll compute decay vs impulse
        # Volume decay: base avg vol / impulse avg vol (we stored rv = imp_avg/20_avg)
        # To get decay we compare base_avg_vol vs the avg_20_volume baseline
        # We use: decay = base_avg_vol / (entry.impulse_rv * avg_20_baseline)
        # But avg_20_baseline is not stored; estimate as base_avg_vol / entry.impulse_rv
        # Simplest correct approach: use candle volumes directly
        # impulse bars volume is not easily available after the fact, so use rv as proxy:
        # decay = base_avg_vol / (entry.impulse_rv * estimated_20_avg)
        # estimated_20_avg = base_avg_vol / entry.impulse_rv (circular) → use absolute check
        # Instead: volume_decay = base_avg_vol / (base_avg_vol + 1) is meaningless
        # Best approach: store impulse avg_volume in the watchlist entry
        # IBCWatchlistEntry doesn't have it directly — but ImpulseEvent does.
        # For the phase-2 check we compute volume_decay as a rough heuristic using
        # the impulse rv field: expected_impulse_vol ≈ base_avg_vol / max(entry.impulse_rv, 0.1)
        # Rehydrate from the impulse event if possible; otherwise use threshold ratio.
        # For correctness, we stored impulse_rv = avg_impulse_vol / avg_20_vol,
        # and we can estimate: avg_20_vol ≈ base_avg_vol / entry.impulse_rv  (if base decayed to ~1×)
        # This is an approximation. For a clean signal check we compare base_avg_vol to
        # the 20-bar average of the candles currently loaded.
        recent_20_avg = (
            sum(c.volume for c in candles[-20:]) / 20 if len(candles) >= 20 else base_avg_vol
        )
        volume_decay = base_avg_vol / recent_20_avg if recent_20_avg > 0 else 1.0

        if volume_decay > self._cfg.ibc_base_volume_decay:
            logger.debug(
                "IBC [%s %s] volume decay insufficient: %.2f > %.2f",
                entry.symbol, entry.timeframe, volume_decay, self._cfg.ibc_base_volume_decay,
            )
            return None

        # ── 4. All conditions met — update entry ────────────────────────
        entry.level_price = level_result.level_price
        entry.level_touches = level_result.touches
        entry.level_cluster_high = level_result.cluster_high
        entry.level_cluster_low = level_result.cluster_low
        entry.base_range_pct = cons_result.range_pct
        entry.base_candle_count = cons_result.candle_count
        entry.base_avg_volume = base_avg_vol
        entry.status = IBCStatus.BASE_CONFIRMED
        entry.base_alert_sent = True
        entry.last_checked_at = utcnow()
        await self._watchlist_repo.save(entry)

        # ── 5. Generate chart and send Telegram alert ────────────────────
        chart_path = self._render_base_chart(entry, candles, level_result.level_price, cons_result)
        await self._send_base_alert(entry, chart_path)

        logger.info(
            "IBC Base confirmed: %s [%s] %s | level=%.4f (%d touches) | range=%.1f%%",
            entry.symbol,
            entry.timeframe,
            entry.direction,
            entry.level_price,
            entry.level_touches,
            entry.base_range_pct,
        )
        return entry

    # ------------------------------------------------------------------
    # Chart rendering
    # ------------------------------------------------------------------

    def _render_base_chart(
        self,
        entry: IBCWatchlistEntry,
        candles: list[OHLCV],
        level_price: float,
        cons_result,
    ) -> Optional[str]:
        """Render a base-formation chart annotated with level and base zone."""
        try:
            display = candles[-60:]
            data = {
                "Open": [c.open for c in display],
                "High": [c.high for c in display],
                "Low": [c.low for c in display],
                "Close": [c.close for c in display],
                "Volume": [c.volume for c in display],
            }
            index = pd.DatetimeIndex([c.timestamp for c in display], name="Date")
            df = pd.DataFrame(data, index=index)

            mc = mpf.make_marketcolors(
                up="#26a69a", down="#ef5350",
                edge="inherit", wick="inherit",
                volume={"up": "#26a69a55", "down": "#ef535055"},
            )
            style = mpf.make_mpf_style(
                marketcolors=mc,
                gridstyle=":",
                gridcolor="#2a2a3e",
                facecolor="#1a1a2e",
                edgecolor="#2a2a3e",
                figcolor="#1a1a2e",
                y_on_right=False,
                rc={
                    "axes.labelcolor": "#cccccc",
                    "xtick.color": "#cccccc",
                    "ytick.color": "#cccccc",
                    "text.color": "#cccccc",
                },
            )

            direction_str = entry.direction if isinstance(entry.direction, str) else entry.direction.value

            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                volume=True,
                figsize=(14, 8),
                title=(
                    f"\n{entry.symbol}  |  IBC Base Formed [{entry.timeframe}]  "
                    f"|  {direction_str.upper()}"
                ),
                hlines=dict(
                    hlines=[level_price],
                    colors=["#f39c12"],
                    linestyle=["--"],
                    linewidths=[1.5],
                ),
                returnfig=True,
            )
            ax = axes[0]

            # Shade consolidation (base) zone
            ax.axhspan(
                cons_result.range_low,
                cons_result.range_high,
                alpha=0.15,
                color="#3498db",
                label="Base zone",
            )

            # Highlight impulse candles (approximate: first N bars from bottom)
            # Colour the latest `impulse_bar_count` bars
            imp_count = 0  # stored in watchlist entry via impulse event — approximate with 1-5
            # We don't store bar_count in watchlist entry, but ImpulseEvent has it.
            # For visual clarity, just shade the whole chart and mark with text.

            ax.annotate(
                f"Level: {level_price:.4f}  ({entry.level_touches} touches)",
                xy=(df.index[-1], level_price),
                xytext=(5, 8),
                textcoords="offset points",
                color="#f39c12",
                fontsize=8,
                fontweight="bold",
            )

            stats = (
                f"Impulse: {entry.impulse_move_pct:+.1f}%  [{direction_str.upper()}]\n"
                f"Level touches: {entry.level_touches}\n"
                f"Base range: {entry.base_range_pct:.1f}%"
            )
            ax.text(
                0.02, 0.97, stats,
                transform=ax.transAxes,
                fontsize=8,
                verticalalignment="top",
                bbox=dict(
                    boxstyle="round,pad=0.4",
                    facecolor="#0d0d1a",
                    alpha=0.85,
                    edgecolor="#444",
                ),
                color="#e0e0e0",
            )

            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            fname = f"{entry.symbol}_ibc_base_{ts}.png"
            output = os.path.join(self._cfg.chart_output_dir, fname)
            fig.savefig(output, dpi=self._cfg.chart_dpi, bbox_inches="tight", facecolor="#1a1a2e")
            plt.close(fig)
            return output

        except Exception as exc:
            logger.error(
                "IBC base chart render failed for %s: %s", entry.symbol, exc, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Telegram alert
    # ------------------------------------------------------------------

    async def _send_base_alert(
        self, entry: IBCWatchlistEntry, chart_path: Optional[str]
    ) -> None:
        direction_str = (
            entry.direction if isinstance(entry.direction, str) else entry.direction.value
        )
        dir_emoji = "🟢" if direction_str == "up" else "🔴"
        text = (
            f"🟡 <b>IBC BASE FORMED: {entry.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{dir_emoji} Direction: <b>{direction_str.upper()}</b>  |  TF: <b>{entry.timeframe}</b>\n"
            f"⚡ Impulse: <b>{entry.impulse_move_pct:+.1f}%</b>  "
            f"(rv={entry.impulse_rv:.2f}x, ATR×={entry.impulse_atr_multiple:.1f})\n"
            f"📐 Level: <b>{entry.level_price:.4f}</b>  ({entry.level_touches} touches)\n"
            f"📦 Base range: <b>{entry.base_range_pct:.1f}%</b>  "
            f"({entry.base_candle_count} bars)\n"
            f"🕐 {format_utc(utcnow())}"
        )
        try:
            if chart_path and Path(chart_path).exists():
                await self._tg.send_photo(chart_path, caption=text)
            else:
                await self._tg.send_message(text)
        except Exception as exc:
            logger.error("IBC Base alert send failed for %s: %s", entry.symbol, exc)

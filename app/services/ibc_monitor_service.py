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
from app.domain.ibc_rules import CeilingBaseResult, evaluate_ceiling_base
from app.domain.models import OHLCV
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
        Check if a ceiling base has formed for the given watchlist entry.

        Uses evaluate_ceiling_base() which detects a flat resistance/support
        level by clustering bar highs (UP) or lows (DOWN), rather than gating
        on the total height of the consolidation zone.  This correctly handles
        wide-range bases that still have a clearly defined ceiling.

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

        # Post-impulse bars: look back up to ibc_ceiling_max_age_bars
        lookback = min(len(candles), self._cfg.ibc_ceiling_max_age_bars)
        post_impulse = candles[-lookback:]

        # Reconstruct impulse avg volume from stored rv and oldest available baseline.
        # rv = avg_impulse_vol / avg_20_vol  →  avg_impulse_vol = rv × avg_20_vol.
        baseline_slice = candles[: max(1, len(candles) - lookback)]
        avg_20_vol = (
            sum(c.volume for c in baseline_slice[-20:]) / min(20, len(baseline_slice))
            if baseline_slice
            else 1.0
        )
        impulse_avg_vol = entry.impulse_rv * avg_20_vol

        # ── Ceiling base detection ──────────────────────────────────────────
        ceiling: CeilingBaseResult = evaluate_ceiling_base(
            bars=post_impulse,
            direction=direction,
            impulse_avg_volume=impulse_avg_vol,
            cluster_tol_pct=self._cfg.ibc_ceiling_cluster_tol_pct,
            min_touches=self._cfg.ibc_ceiling_min_touches,
            min_flat_ratio=self._cfg.ibc_ceiling_min_flat_ratio,
            vol_decay_thresh=self._cfg.ibc_base_volume_decay,
        )

        if not ceiling.detected:
            logger.debug(
                "IBC [%s %s %s] ceiling base not found: %s",
                entry.symbol, entry.timeframe, direction.value,
                ceiling.reason,
            )
            return None

        # ── All conditions met — update entry ───────────────────────────────
        entry.level_price   = ceiling.ceiling_price
        entry.level_touches = ceiling.touches
        # Ceiling corridor bounds
        tol = self._cfg.ibc_ceiling_cluster_tol_pct / 100.0
        entry.level_cluster_high = ceiling.ceiling_price * (1.0 + tol)
        entry.level_cluster_low  = ceiling.ceiling_price * (1.0 - tol)
        # base_range_pct repurposed as flatness quality %
        entry.base_range_pct    = round(ceiling.flatness_pct, 2)
        entry.base_candle_count = ceiling.total_bars
        entry.base_avg_volume   = (
            sum(c.volume for c in post_impulse) / len(post_impulse)
            if post_impulse else 0.0
        )
        entry.ceiling_flat_ratio = ceiling.flat_ratio
        entry.ceiling_vol_decay  = ceiling.vol_decay
        entry.status          = IBCStatus.BASE_CONFIRMED
        entry.base_alert_sent = True
        entry.last_checked_at = utcnow()
        await self._watchlist_repo.save(entry)

        # ── Generate chart and send Telegram alert ──────────────────────────
        chart_path = self._render_base_chart(entry, candles, ceiling)
        await self._send_base_alert(entry, chart_path)

        logger.info(
            "IBC Ceiling base confirmed: %s [%s] %s | "
            "ceiling=%.4f (%d touches, %.1f%% of bars) | flatness=%.2f%%",
            entry.symbol, entry.timeframe, entry.direction,
            entry.level_price, entry.level_touches,
            ceiling.flat_ratio * 100.0, ceiling.flatness_pct,
        )
        return entry

    # ------------------------------------------------------------------

    def _render_base_chart(
        self,
        entry: IBCWatchlistEntry,
        candles: list[OHLCV],
        ceiling: CeilingBaseResult,
    ) -> Optional[str]:
        """Render chart annotated with the detected flat ceiling level."""
        try:
            display = candles[-60:]
            data = {
                "Open":   [c.open   for c in display],
                "High":   [c.high   for c in display],
                "Low":    [c.low    for c in display],
                "Close":  [c.close  for c in display],
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

            direction_str = (
                entry.direction if isinstance(entry.direction, str) else entry.direction.value
            )
            tol = self._cfg.ibc_ceiling_cluster_tol_pct / 100.0

            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                volume=True,
                figsize=(14, 8),
                title=(
                    f"\n{entry.symbol}  |  IBC Ceiling Base [{entry.timeframe}]  "
                    f"|  {direction_str.upper()}"
                ),
                hlines=dict(
                    hlines=[ceiling.ceiling_price],
                    colors=["#f39c12"],
                    linestyle=["--"],
                    linewidths=[1.8],
                ),
                returnfig=True,
            )
            ax = axes[0]

            # Shade ceiling cluster band
            ax.axhspan(
                ceiling.ceiling_price * (1.0 - tol),
                ceiling.ceiling_price * (1.0 + tol),
                alpha=0.18,
                color="#f39c12",
                label="Ceiling cluster band",
            )

            ax.annotate(
                f"Ceiling: {ceiling.ceiling_price:.4f}  "
                f"({ceiling.touches} touches, {ceiling.flat_ratio * 100:.1f}% of bars)",
                xy=(df.index[-1], ceiling.ceiling_price),
                xytext=(5, 8),
                textcoords="offset points",
                color="#f39c12",
                fontsize=8,
                fontweight="bold",
            )

            stats = (
                f"Impulse: {entry.impulse_move_pct:+.1f}%  [{direction_str.upper()}]\n"
                f"Ceiling: {ceiling.ceiling_price:.4f}  ({ceiling.touches} touches)\n"
                f"Flat ratio: {ceiling.flat_ratio * 100:.1f}%  "
                f"flatness={ceiling.flatness_pct:.2f}%\n"
                f"Vol decay: {ceiling.vol_decay:.3f}x"
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
            fname = f"{entry.symbol}_ibc_ceiling_{ts}.png"
            output = os.path.join(self._cfg.chart_output_dir, fname)
            fig.savefig(
                output, dpi=self._cfg.chart_dpi, bbox_inches="tight", facecolor="#1a1a2e"
            )
            plt.close(fig)
            return output

        except Exception as exc:
            logger.error(
                "IBC ceiling chart render failed for %s: %s", entry.symbol, exc, exc_info=True
            )
            return None


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

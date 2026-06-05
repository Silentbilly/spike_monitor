"""
Phase 3 — IBC Breakout Service.

Polls base-confirmed watchlist entries for a breakout from the level.
On confirmation it:
  1. Scores the setup (0-100).
  2. Sends a Telegram "🔴🟢 Breakout" alert with annotated PNG chart.
  3. Persists IBCBreakoutEvent and marks watchlist entry BREAKOUT_CONFIRMED.
  4. Deduplicates: no re-alert within IBC_COOLDOWN_H hours.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.config import Config
from app.domain.ibc_models import (
    IBCBreakoutEvent,
    IBCStatus,
    IBCWatchlistEntry,
    ImpulseDirection,
)
from app.domain.ibc_rules import (
    IBCScoringInput,
    evaluate_ibc_breakout,
    score_ibc,
)
from app.domain.models import OHLCV
from app.exchanges.bybit import BybitAdapter
from app.services.telegram_service import TelegramService
from app.storage.ibc_repositories import IBCBreakoutRepository, IBCWatchlistRepository
from app.utils.time import format_utc, hours_since, utcnow

logger = logging.getLogger(__name__)

_TF_INTERVAL: dict[str, str] = {"60": "60", "15": "15"}
_CANDLE_LIMIT = 80


class IBCBreakoutService:
    """
    Phase 3: Breakout detection for IBC setups.

    Called every IBC_BREAKOUT_INTERVAL_MIN minutes.
    """

    def __init__(
        self,
        exchange: BybitAdapter,
        watchlist_repo: IBCWatchlistRepository,
        breakout_repo: IBCBreakoutRepository,
        telegram: TelegramService,
        config: Config,
    ) -> None:
        self._exchange = exchange
        self._watchlist_repo = watchlist_repo
        self._breakout_repo = breakout_repo
        self._tg = telegram
        self._cfg = config
        Path(config.chart_output_dir).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_breakout_cycle(self) -> list[IBCBreakoutEvent]:
        """
        Check all BASE_CONFIRMED watchlist entries for a breakout.

        Returns list of IBCBreakoutEvent objects for setups that triggered.
        """
        entries = await self._watchlist_repo.get_base_confirmed_entries()
        now = utcnow()
        triggered: list[IBCBreakoutEvent] = []

        for entry in entries:
            if entry.expires_at < now:
                entry.status = IBCStatus.EXPIRED
                await self._watchlist_repo.save(entry)
                continue

            # Deduplication: skip if a breakout alert was sent recently
            if entry.last_breakout_alert_at is not None:
                elapsed = hours_since(entry.last_breakout_alert_at)
                if elapsed < self._cfg.ibc_cooldown_h:
                    continue

            try:
                event = await self._check_breakout(entry)
                if event is not None:
                    triggered.append(event)
            except Exception as exc:
                logger.error(
                    "IBC breakout check failed for %s [%s]: %s",
                    entry.symbol, entry.timeframe, exc, exc_info=True
                )

        logger.info(
            "IBC breakout cycle: %d base-confirmed entries → %d breakout(s)",
            len(entries), len(triggered)
        )
        return triggered

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_breakout(
        self, entry: IBCWatchlistEntry
    ) -> Optional[IBCBreakoutEvent]:
        if entry.level_price is None:
            return None

        interval = _TF_INTERVAL.get(entry.timeframe, entry.timeframe)
        candles = await self._exchange.get_klines(
            symbol=entry.symbol, interval=interval, limit=_CANDLE_LIMIT
        )
        if not candles:
            return None

        latest = candles[-1]
        direction = ImpulseDirection(entry.direction)

        # Average volume: last 20 bars excluding the latest
        vol_window = candles[-21:-1] if len(candles) >= 21 else candles[:-1]
        avg_vol = (
            sum(c.volume for c in vol_window) / len(vol_window)
            if vol_window
            else 0.0
        )

        breakout_result = evaluate_ibc_breakout(
            candle=latest,
            direction=direction,
            level_price=entry.level_price,
            avg_volume=avg_vol,
            breakout_confirm_pct=self._cfg.ibc_breakout_confirm_pct,
            breakout_vol_mult=self._cfg.ibc_breakout_vol_mult,
        )

        if not breakout_result.triggered:
            return None

        # ── Score the setup ────────────────────────────────────────────
        base_range = entry.base_range_pct or 10.0
        base_avg_vol = entry.base_avg_volume or avg_vol
        impulse_avg_vol = entry.impulse_rv  # rv = imp_avg / 20_avg
        # volume_decay = base_avg_vol / estimated_impulse_avg_vol
        # We don't store raw impulse avg vol, but we can estimate:
        # impulse_avg ≈ entry.impulse_rv × (base_avg_vol / current_rv_approx)
        # Simplest: use base_avg_vol / entry.impulse_rv as 20-bar baseline proxy
        estimated_20_avg = base_avg_vol / max(entry.impulse_rv, 0.1)
        volume_decay = base_avg_vol / max(estimated_20_avg, 0.001)

        score_inp = IBCScoringInput(
            impulse_move_pct=entry.impulse_move_pct,
            impulse_rv=entry.impulse_rv,
            impulse_atr_multiple=entry.impulse_atr_multiple,
            level_touches=entry.level_touches or 0,
            consolidation_tightness=base_range,
            volume_decay=volume_decay,
            breakout_volume_confirmed=breakout_result.volume_confirmed,
            breakout_candle_conviction=breakout_result.distance_pct,
            breakout_distance_pct=breakout_result.distance_pct,
            weak_volume=not breakout_result.volume_confirmed,
            wide_base=base_range > self._cfg.ibc_base_max_range_pct,
            stale_level=(
                (entry.level_touches or 0) > 0
                and hours_since(entry.added_at) > self._cfg.ibc_watchlist_ttl_hours * 0.8
            ),
        )
        score_result = score_ibc(score_inp)

        # ── Persist breakout event ─────────────────────────────────────
        breakout_event = IBCBreakoutEvent(
            watchlist_entry_id=entry.id,
            symbol=entry.symbol,
            timeframe=entry.timeframe,
            direction=direction,
            triggered_at=utcnow(),
            breakout_price=breakout_result.breakout_price,
            level_price=entry.level_price,
            distance_pct=breakout_result.distance_pct,
            volume_confirmed=breakout_result.volume_confirmed,
            breakout_volume=breakout_result.candle_volume,
            avg_volume=avg_vol,
            impulse_move_pct=entry.impulse_move_pct,
            impulse_rv=entry.impulse_rv,
            impulse_atr_multiple=entry.impulse_atr_multiple,
            level_touches=entry.level_touches or 0,
            base_range_pct=base_range,
            base_volume_decay=volume_decay,
            score=score_result.total,
            explanation=score_result.explanation,
        )

        # Generate chart
        chart_path = self._render_breakout_chart(entry, candles, breakout_event)
        if chart_path:
            breakout_event.chart_path = chart_path

        await self._breakout_repo.save(breakout_event)

        # Update watchlist entry
        entry.status = IBCStatus.BREAKOUT_CONFIRMED
        entry.breakout_price = breakout_result.breakout_price
        entry.breakout_volume_confirmed = breakout_result.volume_confirmed
        entry.last_breakout_alert_at = utcnow()
        await self._watchlist_repo.save(entry)

        # Send Telegram alert
        await self._send_breakout_alert(breakout_event, chart_path)

        logger.info(
            "IBC Breakout: %s [%s] %s @ %.4f  score=%.0f",
            entry.symbol, entry.timeframe, direction.value,
            breakout_result.breakout_price, score_result.total,
        )
        return breakout_event

    # ------------------------------------------------------------------
    # Chart rendering
    # ------------------------------------------------------------------

    def _render_breakout_chart(
        self,
        entry: IBCWatchlistEntry,
        candles: list[OHLCV],
        event: IBCBreakoutEvent,
    ) -> Optional[str]:
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

            direction_str = event.direction if isinstance(event.direction, str) else event.direction.value

            # Hlines: level and impulse reference
            hlines_vals = [event.level_price, entry.impulse_end_price]
            hlines_colors = ["#f39c12", "#e74c3c"]
            hlines_styles = ["--", ":"]
            hlines_widths = [1.5, 1.0]

            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                volume=True,
                figsize=(14, 8),
                title=(
                    f"\n{entry.symbol}  |  IBC BREAKOUT [{entry.timeframe}]  "
                    f"|  {direction_str.upper()}  |  Score {event.score:.0f}/100"
                ),
                hlines=dict(
                    hlines=hlines_vals,
                    colors=hlines_colors,
                    linestyle=hlines_styles,
                    linewidths=hlines_widths,
                ),
                returnfig=True,
            )
            ax = axes[0]

            # Shade base zone
            if entry.base_range_pct is not None and entry.level_price is not None:
                half_range = entry.level_price * (entry.base_range_pct / 100.0) / 2
                ax.axhspan(
                    entry.level_price - half_range,
                    entry.level_price + half_range,
                    alpha=0.12,
                    color="#3498db",
                )

            # Breakout candle marker (vertical arrow on the last bar)
            ax.annotate(
                "▲ Breakout" if direction_str == "up" else "▼ Breakout",
                xy=(df.index[-1], event.breakout_price),
                xytext=(0, 20 if direction_str == "up" else -20),
                textcoords="offset points",
                fontsize=9,
                fontweight="bold",
                color="#2ecc71" if direction_str == "up" else "#e74c3c",
                ha="center",
                arrowprops=dict(
                    arrowstyle="->",
                    color="#2ecc71" if direction_str == "up" else "#e74c3c",
                ),
            )

            stats = (
                f"Impulse: {entry.impulse_move_pct:+.1f}%\n"
                f"Level: {event.level_price:.4f} ({event.level_touches} touches)\n"
                f"Breakout: {event.breakout_price:.4f}  +{event.distance_pct:.2f}%\n"
                f"Vol confirmed: {'YES ✓' if event.volume_confirmed else 'NO'}"
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
            fname = f"{entry.symbol}_ibc_breakout_{ts}.png"
            output = os.path.join(self._cfg.chart_output_dir, fname)
            fig.savefig(output, dpi=self._cfg.chart_dpi, bbox_inches="tight", facecolor="#1a1a2e")
            plt.close(fig)
            return output

        except Exception as exc:
            logger.error(
                "IBC breakout chart render failed for %s: %s", entry.symbol, exc, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Telegram alert
    # ------------------------------------------------------------------

    async def _send_breakout_alert(
        self, event: IBCBreakoutEvent, chart_path: Optional[str]
    ) -> None:
        direction_str = (
            event.direction if isinstance(event.direction, str) else event.direction.value
        )
        dir_emoji = "🟢" if direction_str == "up" else "🔴"
        vol_str = "YES ✅" if event.volume_confirmed else "NO ⚠️"
        score_bar = "█" * round(event.score / 10) + "░" * (10 - round(event.score / 10))

        text = (
            f"{dir_emoji} <b>IBC BREAKOUT: {event.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Score: <b>{score_bar} {event.score:.0f}/100</b>\n"
            f"📈 Direction: <b>{direction_str.upper()}</b>  |  TF: <b>{event.timeframe}</b>\n"
            f"💲 Breakout: <b>{event.breakout_price:.4f}</b>  "
            f"(+{event.distance_pct:.2f}% from level)\n"
            f"📐 Level: <b>{event.level_price:.4f}</b>  ({event.level_touches} touches)\n"
            f"📦 Vol confirmed: <b>{vol_str}</b>\n"
            f"⚡ Impulse: <b>{event.impulse_move_pct:+.1f}%</b>\n"
            f"🕐 {format_utc(utcnow())}"
        )
        try:
            if chart_path and Path(chart_path).exists():
                await self._tg.send_photo(chart_path, caption=text)
            else:
                await self._tg.send_message(text)
        except Exception as exc:
            logger.error(
                "IBC Breakout alert send failed for %s: %s", event.symbol, exc
            )

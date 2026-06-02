"""
Chart Service — programmatic PNG generation using mplfinance + matplotlib.

No browser, no headless Chrome. All charts rendered via matplotlib backend
'Agg' which works in Docker/VPS without a display server.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")   # must be set before any other matplotlib import

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.config import Config
from app.constants import (
    CHART_CANDLE_COUNT_DISPLAY,
    COLOR_BREAKDOWN,
    COLOR_CONSOLIDATION,
    COLOR_INVALIDATION,
    COLOR_RETRACE,
    COLOR_SPIKE,
)
from app.domain.models import BreakdownSignal, OHLCV, SpikeEvent, WatchlistItem

logger = logging.getLogger(__name__)


class ChartService:
    """Generates annotated candlestick PNG charts."""

    def __init__(self, config: Config) -> None:
        self._config = config
        Path(config.chart_output_dir).mkdir(parents=True, exist_ok=True)

    def _candles_to_df(self, candles: list[OHLCV]) -> pd.DataFrame:
        """Convert OHLCV list to mplfinance-compatible DataFrame."""
        data = {
            "Open": [c.open for c in candles],
            "High": [c.high for c in candles],
            "Low": [c.low for c in candles],
            "Close": [c.close for c in candles],
            "Volume": [c.volume for c in candles],
        }
        index = pd.DatetimeIndex(
            [c.timestamp for c in candles], name="Date"
        )
        df = pd.DataFrame(data, index=index)
        return df

    def _output_path(self, symbol: str, suffix: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"{symbol}_{suffix}_{ts}.png"
        return os.path.join(self._config.chart_output_dir, fname)

    # ------------------------------------------------------------------
    # Spike candidate chart
    # ------------------------------------------------------------------

    def render_spike_chart(
        self,
        event: SpikeEvent,
        candles: list[OHLCV],
    ) -> Optional[str]:
        """
        Render a spike candidate chart and return the file path.

        Annotates:
          - Spike candle (highlighted)
          - High of spike
          - Open of spike
          - Retracement zone (spike open → spike high)
          - Current price
        """
        try:
            display = candles[-CHART_CANDLE_COUNT_DISPLAY:]
            df = self._candles_to_df(display)
            output = self._output_path(event.symbol, "spike")

            # mplfinance style
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
                rc={"axes.labelcolor": "#cccccc", "xtick.color": "#cccccc",
                    "ytick.color": "#cccccc", "text.color": "#cccccc"},
            )

            # Hlines: spike high, spike open, current price
            hlines_vals = [event.spike_high, event.spike_open, event.current_price]
            hlines_colors = [COLOR_SPIKE, COLOR_RETRACE, "#ffffff"]
            hlines_styles = ["--", "--", ":"]
            hlines_widths = [1.2, 1.2, 0.8]

            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                volume=True,
                figsize=self._config.chart_figsize if hasattr(self._config, "chart_figsize") else (14, 8),
                title=f"\n{event.symbol}  |  Daily Spike Setup  |  Score {event.score:.0f}/100",
                hlines=dict(
                    hlines=hlines_vals,
                    colors=hlines_colors,
                    linestyle=hlines_styles,
                    linewidths=hlines_widths,
                ),
                returnfig=True,
            )

            ax = axes[0]

            # Shaded retracement zone
            ax.axhspan(
                event.spike_open,
                event.spike_high,
                alpha=0.08,
                color=COLOR_RETRACE,
                label="Retrace zone",
            )

            # Labels on hlines
            ax.annotate(
                f"Spike High {event.spike_high:.4f}",
                xy=(df.index[-1], event.spike_high),
                xytext=(5, 5), textcoords="offset points",
                color=COLOR_SPIKE, fontsize=7,
            )
            ax.annotate(
                f"Spike Open {event.spike_open:.4f}",
                xy=(df.index[-1], event.spike_open),
                xytext=(5, -12), textcoords="offset points",
                color=COLOR_RETRACE, fontsize=7,
            )
            ax.annotate(
                f"Current {event.current_price:.4f}",
                xy=(df.index[-1], event.current_price),
                xytext=(5, 5), textcoords="offset points",
                color="#ffffff", fontsize=7,
            )

            # Score + stats text box
            stats = (
                f"Spike: {event.spike_pct:+.1f}%  |  Retrace: {event.retrace_pct:.0f}%\n"
                f"RV20: {event.rv20:.1f}x  |  CLV: {event.clv:.2f}  |  ATR×: {event.atr_multiple:.1f}\n"
                f"Strength: {event.strength.upper() if isinstance(event.strength, str) else event.strength.value.upper()}"
            )
            ax.text(
                0.02, 0.97, stats,
                transform=ax.transAxes,
                fontsize=8, verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#0d0d1a", alpha=0.8, edgecolor="#444"),
                color="#e0e0e0",
            )

            fig.savefig(output, dpi=self._config.chart_dpi, bbox_inches="tight", facecolor="#1a1a2e")
            plt.close(fig)
            logger.debug("Spike chart saved: %s", output)
            return output

        except Exception as exc:
            logger.error("Chart render error for %s: %s", event.symbol, exc, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Breakdown chart
    # ------------------------------------------------------------------

    def render_breakdown_chart(
        self,
        signal: BreakdownSignal,
        item: WatchlistItem,
        candles: list[OHLCV],
    ) -> Optional[str]:
        """
        Render a breakdown confirmation chart.

        Annotates:
          - Spike high & open
          - Consolidation zone (if known)
          - Breakdown level
          - Invalidation level
        """
        try:
            display = candles[-CHART_CANDLE_COUNT_DISPLAY:]
            df = self._candles_to_df(display)
            output = self._output_path(signal.symbol, "breakdown")

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
                rc={"axes.labelcolor": "#cccccc", "xtick.color": "#cccccc",
                    "ytick.color": "#cccccc", "text.color": "#cccccc"},
            )

            hlines_vals = [signal.breakdown_level, item.spike_high]
            hlines_colors = [COLOR_BREAKDOWN, COLOR_SPIKE]
            hlines_styles = ["-", "--"]
            hlines_widths = [1.5, 1.0]

            if item.invalidation_level:
                hlines_vals.append(item.invalidation_level)
                hlines_colors.append(COLOR_INVALIDATION)
                hlines_styles.append(":")
                hlines_widths.append(0.8)

            fig, axes = mpf.plot(
                df,
                type="candle",
                style=style,
                volume=True,
                figsize=(14, 8),
                title=f"\n{signal.symbol}  |  BREAKDOWN CONFIRMED  |  Score {signal.score:.0f}/100",
                hlines=dict(
                    hlines=hlines_vals,
                    colors=hlines_colors,
                    linestyle=hlines_styles,
                    linewidths=hlines_widths,
                ),
                returnfig=True,
            )

            ax = axes[0]

            # Consolidation shaded zone
            if item.consolidation_low and item.consolidation_high:
                ax.axhspan(
                    item.consolidation_low,
                    item.consolidation_high,
                    alpha=0.12, color=COLOR_CONSOLIDATION,
                    label="Consolidation",
                )
                ax.annotate(
                    f"Consol. zone {item.consolidation_low:.4f}–{item.consolidation_high:.4f}",
                    xy=(df.index[len(df) // 4], item.consolidation_low),
                    xytext=(0, -14), textcoords="offset points",
                    color=COLOR_CONSOLIDATION, fontsize=7,
                )

            ax.annotate(
                f"Breakdown {signal.breakdown_level:.4f}",
                xy=(df.index[-1], signal.breakdown_level),
                xytext=(5, 5), textcoords="offset points",
                color=COLOR_BREAKDOWN, fontsize=8, fontweight="bold",
            )
            ax.annotate(
                f"Spike High {item.spike_high:.4f}",
                xy=(df.index[-1], item.spike_high),
                xytext=(5, 5), textcoords="offset points",
                color=COLOR_SPIKE, fontsize=7,
            )

            stats = (
                f"Quality: {signal.quality.upper() if isinstance(signal.quality, str) else signal.quality.value.upper()}\n"
                f"Spike: {signal.spike_pct:+.1f}%  |  Retrace: {signal.retrace_pct:.0f}%\n"
                f"Vol confirmed: {'YES' if signal.volume_confirmed else 'NO'}"
            )
            ax.text(
                0.02, 0.97, stats,
                transform=ax.transAxes,
                fontsize=8, verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#0d0d1a", alpha=0.8, edgecolor="#444"),
                color="#e0e0e0",
            )

            fig.savefig(output, dpi=self._config.chart_dpi, bbox_inches="tight", facecolor="#1a1a2e")
            plt.close(fig)
            logger.debug("Breakdown chart saved: %s", output)
            return output

        except Exception as exc:
            logger.error("Breakdown chart render error for %s: %s", signal.symbol, exc, exc_info=True)
            return None

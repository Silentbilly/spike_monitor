"""Text formatting helpers for Telegram messages."""

from __future__ import annotations

from app.domain.enums import BreakdownQuality, SpikeStrength


def fmt_pct(value: float, decimals: int = 1) -> str:
    return f"{value:+.{decimals}f}%"


def fmt_float(value: float, decimals: int = 4) -> str:
    return f"{value:.{decimals}f}"


def strength_emoji(strength: SpikeStrength) -> str:
    return {
        SpikeStrength.WEAK: "🟡",
        SpikeStrength.MODERATE: "🟠",
        SpikeStrength.STRONG: "🔴",
        SpikeStrength.EXTREME: "💥",
    }.get(strength, "⚪")


def quality_emoji(quality: BreakdownQuality) -> str:
    return {
        BreakdownQuality.LOW: "🟡",
        BreakdownQuality.MEDIUM: "🟠",
        BreakdownQuality.HIGH: "🔴",
    }.get(quality, "⚪")


def score_bar(score: float, width: int = 10) -> str:
    """Visual score bar: ████░░░░░░ 72"""
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {score:.0f}"

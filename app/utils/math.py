"""Numeric helpers."""

from __future__ import annotations


def pct_change(a: float, b: float) -> float:
    """(b - a) / a * 100. Returns 0 if a == 0."""
    if a == 0:
        return 0.0
    return (b - a) / a * 100.0


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division with zero-guard."""
    return numerator / denominator if denominator != 0 else default

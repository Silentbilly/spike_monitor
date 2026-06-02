"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.models import OHLCV


def make_candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1_000_000,
    quote_volume: float = 0.0,
    ts: datetime | None = None,
) -> OHLCV:
    """Helper to create a test OHLCV candle."""
    if ts is None:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return OHLCV(
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote_volume or volume * close,
    )


def make_candles_series(
    n: int = 30,
    base_price: float = 100.0,
    daily_drift: float = 0.0,
    daily_vol: float = 2.0,
) -> list[OHLCV]:
    """Create a synthetic series of N daily candles."""
    from datetime import timedelta
    import random

    candles = []
    price = base_price
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rng = random.Random(42)

    for i in range(n):
        open_ = price
        close = open_ * (1 + daily_drift / 100 + rng.uniform(-daily_vol, daily_vol) / 100)
        high = max(open_, close) * (1 + rng.uniform(0, daily_vol / 200))
        low = min(open_, close) * (1 - rng.uniform(0, daily_vol / 200))
        vol = rng.uniform(800_000, 1_200_000)
        candles.append(OHLCV(
            timestamp=ts,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=vol,
            quote_volume=vol * close,
        ))
        price = close
        ts += timedelta(days=1)

    return candles

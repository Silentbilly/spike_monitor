"""
Market data service — fetches and caches OHLCV + enrichments.

Provides a unified interface to the exchange adapter, adding:
  - ATR computation
  - 20-day average volume
  - Optional funding / OI enrichment
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import Config
from app.domain.indicators import calc_atr, calc_avg_volume
from app.domain.models import OHLCV, AssetSnapshot
from app.domain.enums import Timeframe
from app.exchanges.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class MarketDataService:
    """Thin wrapper over ExchangeAdapter with indicator pre-computation."""

    def __init__(self, exchange: ExchangeAdapter, config: Config) -> None:
        self._exchange = exchange
        self._config = config

    async def get_snapshot(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
    ) -> Optional[AssetSnapshot]:
        """
        Fetch OHLCV and return an enriched AssetSnapshot.

        Returns None if data is unavailable or insufficient.
        """
        try:
            candles = await self._exchange.get_klines(
                symbol=symbol,
                interval=timeframe.value,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("Failed to fetch klines for %s/%s: %s", symbol, timeframe.value, exc)
            return None

        if len(candles) < 20:
            logger.debug("Insufficient candles for %s: %d", symbol, len(candles))
            return None

        atr = calc_atr(candles, period=14)
        avg_vol_20d = calc_avg_volume(candles, period=20)
        latest = candles[-1]

        # Optional enrichments
        funding_rate: Optional[float] = None
        open_interest: Optional[float] = None
        mark_price: Optional[float] = None
        index_price: Optional[float] = None

        if self._config.enable_funding_enrichment:
            try:
                funding_rate = await self._exchange.get_funding_rate(symbol)
            except Exception as exc:
                logger.debug("Funding rate fetch failed for %s: %s", symbol, exc)

        if self._config.enable_oi_enrichment:
            try:
                open_interest = await self._exchange.get_open_interest(symbol)
                mark_price = await self._exchange.get_mark_price(symbol)
                index_price = await self._exchange.get_index_price(symbol)
            except Exception as exc:
                logger.debug("OI enrichment failed for %s: %s", symbol, exc)

        return AssetSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            latest_close=latest.close,
            latest_volume=latest.volume,
            avg_volume_20d=avg_vol_20d,
            atr_14=atr,
            funding_rate=funding_rate,
            open_interest=open_interest,
            index_price=index_price,
            mark_price=mark_price,
        )

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
    ) -> list[OHLCV]:
        """Direct kline fetch, no enrichment."""
        try:
            return await self._exchange.get_klines(symbol=symbol, interval=interval, limit=limit)
        except Exception as exc:
            logger.warning("Kline fetch error %s/%s: %s", symbol, interval, exc)
            return []

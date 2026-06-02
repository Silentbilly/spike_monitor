"""
Universe service — filters the available instrument list to a tradeable set.

Applies:
  - linear USDT perpetual only
  - status == Trading
  - minimum average daily quote volume
  - minimum history requirement (kline count check)
  - blacklist / whitelist
  - optional top-N by volume cap
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import Config
from app.domain.indicators import calc_avg_volume
from app.domain.models import InstrumentInfo
from app.exchanges.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class UniverseService:
    """Manages the filtered set of instruments to scan."""

    def __init__(self, exchange: ExchangeAdapter, config: Config) -> None:
        self._exchange = exchange
        self._config = config

    async def get_tradeable_universe(self) -> list[InstrumentInfo]:
        """
        Fetch and filter all instruments.

        Returns instruments sorted by descending average quote volume.
        """
        logger.info("Fetching instrument list from exchange…")
        all_instruments = await self._exchange.get_instruments()
        logger.info("Total instruments from exchange: %d", len(all_instruments))

        # Whitelist takes priority
        if self._config.whitelist:
            filtered = [i for i in all_instruments if i.symbol in self._config.whitelist]
            logger.info("Whitelist applied: %d instruments", len(filtered))
            return filtered

        # Blacklist
        blacklisted = set(self._config.blacklist)

        # Basic filters (status/type already done in adapter)
        candidates = [
            i for i in all_instruments
            if i.symbol not in blacklisted
        ]

        # Volume + history pre-check (batched, semi-parallel)
        qualified = await self._check_volume_and_history(candidates)

        # Sort by volume descending, cap at max_universe_size
        qualified.sort(key=lambda x: x._avg_quote_vol, reverse=True)  # type: ignore[attr-defined]
        if self._config.max_universe_size > 0:
            qualified = qualified[: self._config.max_universe_size]

        logger.info("Tradeable universe: %d instruments", len(qualified))
        return qualified

    async def _check_volume_and_history(
        self,
        instruments: list[InstrumentInfo],
    ) -> list[InstrumentInfo]:
        """
        Batch-check volume and history requirements.

        Uses semaphore to limit concurrency.
        """
        from app.constants import SCAN_CONCURRENCY
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        results: list[InstrumentInfo] = []

        async def check(inst: InstrumentInfo) -> Optional[InstrumentInfo]:
            async with semaphore:
                try:
                    candles = await self._exchange.get_klines(
                        symbol=inst.symbol,
                        interval="D",
                        limit=self._config.min_history_days + 5,
                    )
                    if len(candles) < self._config.min_history_days:
                        logger.debug(
                            "Skipping %s: insufficient history (%d days)",
                            inst.symbol, len(candles),
                        )
                        return None

                    avg_vol = calc_avg_volume(candles, period=20)
                    if avg_vol < self._config.min_avg_quote_volume_usdt:
                        logger.debug(
                            "Skipping %s: avg_vol %.0f < %.0f",
                            inst.symbol, avg_vol, self._config.min_avg_quote_volume_usdt,
                        )
                        return None

                    # Store avg vol for sorting
                    inst._avg_quote_vol = avg_vol  # type: ignore[attr-defined]
                    return inst
                except Exception as exc:
                    logger.debug("Universe check error %s: %s", inst.symbol, exc)
                    return None

        tasks = [check(i) for i in instruments]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                results.append(result)

        return results

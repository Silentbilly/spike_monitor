"""
Abstract base exchange adapter.

Add a new exchange by subclassing ExchangeAdapter and implementing
all abstract methods. The rest of the system consumes only this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models import OHLCV, InstrumentInfo


class ExchangeAdapter(ABC):
    """Interface contract for all exchange data sources."""

    @abstractmethod
    async def get_instruments(self) -> list[InstrumentInfo]:
        """
        Return all available linear perpetual USDT instruments.

        Must filter for active / tradeable status only.
        """
        ...

    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str,          # e.g. "D", "240", "60"
        limit: int = 200,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> list[OHLCV]:
        """
        Fetch OHLCV bars sorted oldest-first.

        Args:
            symbol:   Instrument symbol, e.g. "BTCUSDT".
            interval: Timeframe string as expected by the exchange.
            limit:    Maximum number of bars to return.
            start_ms: Optional start time in milliseconds UTC.
            end_ms:   Optional end time in milliseconds UTC.
        """
        ...

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """
        Return the most recent funding rate for a symbol.

        Returns None if unavailable or not applicable.
        """
        ...

    # Optional enrichments — default to None, override in subclasses

    async def get_open_interest(self, symbol: str) -> Optional[float]:
        """Open interest in contracts. Optional enrichment."""
        return None

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Mark / fair price. Optional enrichment."""
        return None

    async def get_index_price(self, symbol: str) -> Optional[float]:
        """Index price (underlying spot). Optional enrichment."""
        return None

    async def close(self) -> None:
        """Clean up any open connections / sessions."""
        ...

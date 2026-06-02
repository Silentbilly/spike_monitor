"""
Bybit V5 public market data adapter.

Endpoints used (all public, no API keys required):
  GET /v5/market/instruments-info  — instrument list
  GET /v5/market/kline             — OHLCV
  GET /v5/market/funding/history   — funding rate history
  GET /v5/market/tickers           — mark/index price, OI (optional)

Rate limit awareness:
  Bybit V5 public endpoints: ~600 req/min (10 req/s).
  This adapter applies per-request backoff and respects Retry-After headers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.domain.models import OHLCV, InstrumentInfo
from app.exchanges.base import ExchangeAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)

BYBIT_BASE_URL = "https://api.bybit.com"
DEFAULT_TIMEOUT = 15.0
MAX_RETRIES = 4
INSTRUMENTS_PAGE_LIMIT = 1000
KLINE_MAX_LIMIT = 1000


class BybitAdapter(ExchangeAdapter):
    """
    Bybit V5 REST adapter using httpx async client.

    Thread-safe; share one instance across the application.
    """

    def __init__(
        self,
        base_url: str = BYBIT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    @async_retry(max_attempts=MAX_RETRIES, base_delay=1.0, max_delay=30.0)
    async def _get(self, path: str, params: dict) -> dict:
        """
        Execute an authenticated-free GET request.

        Raises httpx.HTTPStatusError on 4xx/5xx after retries.
        Raises ValueError if Bybit retCode != 0.
        """
        client = await self._get_client()
        logger.debug("GET %s params=%s", path, params)
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            msg = data.get("retMsg", "unknown error")
            raise ValueError(f"Bybit API error retCode={ret_code}: {msg}")
        return data.get("result", {})

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------

    async def get_instruments(self) -> list[InstrumentInfo]:
        """
        Fetch all active linear USDT perpetual instruments.

        Handles Bybit's cursor-based pagination automatically.
        """
        instruments: list[InstrumentInfo] = []
        cursor: Optional[str] = None

        while True:
            params: dict = {
                "category": "linear",
                "limit": INSTRUMENTS_PAGE_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor

            result = await self._get("/v5/market/instruments-info", params)
            items = result.get("list", [])

            for item in items:
                quote = item.get("quoteCoin", "")
                if quote != "USDT":
                    continue
                status = item.get("status", "")
                if status != "Trading":
                    continue

                lot_filter = item.get("lotSizeFilter", {})
                price_filter = item.get("priceFilter", {})
                leverage_filter = item.get("leverageFilter", {})

                instruments.append(
                    InstrumentInfo(
                        symbol=item["symbol"],
                        base_coin=item.get("baseCoin", ""),
                        quote_coin=quote,
                        contract_type=item.get("contractType", ""),
                        status=status,
                        tick_size=float(price_filter.get("tickSize", 0)),
                        min_qty=float(lot_filter.get("minOrderQty", 0)),
                        max_leverage=float(leverage_filter.get("maxLeverage", 0)),
                    )
                )

            cursor = result.get("nextPageCursor")
            if not cursor or not items:
                break

        logger.info("Fetched %d active linear USDT perpetuals", len(instruments))
        return instruments

    # ------------------------------------------------------------------
    # Klines (OHLCV)
    # ------------------------------------------------------------------

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
    ) -> list[OHLCV]:
        """
        Fetch OHLCV candles, oldest-first.

        Bybit returns at most 1000 bars per request. If limit > 1000 this
        method pages automatically.
        """
        if limit <= KLINE_MAX_LIMIT:
            return await self._fetch_klines_page(symbol, interval, limit, start_ms, end_ms)

        # Paginate
        all_candles: list[OHLCV] = []
        current_end = end_ms
        remaining = limit

        while remaining > 0:
            batch = min(remaining, KLINE_MAX_LIMIT)
            candles = await self._fetch_klines_page(symbol, interval, batch, start_ms, current_end)
            if not candles:
                break
            # Bybit returns newest-first, we reversed; oldest is index 0
            all_candles = candles + all_candles
            remaining -= len(candles)
            # Move window before the oldest fetched
            current_end = int(candles[0].timestamp.timestamp() * 1000) - 1
            if len(candles) < batch:
                break
            await asyncio.sleep(0.12)   # ~8 req/s to stay under rate limit

        # Sort oldest-first and deduplicate
        seen: set[float] = set()
        result: list[OHLCV] = []
        for c in sorted(all_candles, key=lambda x: x.timestamp):
            ts = c.timestamp.timestamp()
            if ts not in seen:
                seen.add(ts)
                result.append(c)
        return result[-limit:]

    async def _fetch_klines_page(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_ms: Optional[int],
        end_ms: Optional[int],
    ) -> list[OHLCV]:
        params: dict = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, KLINE_MAX_LIMIT),
        }
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms

        result = await self._get("/v5/market/kline", params)
        raw_list = result.get("list", [])

        candles: list[OHLCV] = []
        for row in raw_list:
            # Bybit format: [timestamp_ms, open, high, low, close, volume, quote_volume]
            if len(row) < 7:
                continue
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(int(row[0]) / 1000.0, tz=timezone.utc)
            candles.append(
                OHLCV(
                    timestamp=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    quote_volume=float(row[6]),
                )
            )

        # Bybit returns newest-first; reverse to oldest-first
        return list(reversed(candles))

    # ------------------------------------------------------------------
    # Funding rate
    # ------------------------------------------------------------------

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Return the most recent historical funding rate."""
        try:
            result = await self._get(
                "/v5/market/funding/history",
                {"category": "linear", "symbol": symbol, "limit": 1},
            )
            entries = result.get("list", [])
            if entries:
                return float(entries[0].get("fundingRate", 0))
        except Exception as exc:
            logger.debug("Funding rate unavailable for %s: %s", symbol, exc)
        return None

    # ------------------------------------------------------------------
    # Optional enrichments
    # ------------------------------------------------------------------

    async def get_open_interest(self, symbol: str) -> Optional[float]:
        """Fetch open interest in USDT from tickers endpoint."""
        try:
            result = await self._get(
                "/v5/market/tickers",
                {"category": "linear", "symbol": symbol},
            )
            items = result.get("list", [])
            if items:
                oi = items[0].get("openInterestValue")
                if oi is not None:
                    return float(oi)
        except Exception as exc:
            logger.debug("OI unavailable for %s: %s", symbol, exc)
        return None

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Fetch mark price from tickers."""
        try:
            result = await self._get(
                "/v5/market/tickers",
                {"category": "linear", "symbol": symbol},
            )
            items = result.get("list", [])
            if items:
                mp = items[0].get("markPrice")
                if mp is not None:
                    return float(mp)
        except Exception as exc:
            logger.debug("Mark price unavailable for %s: %s", symbol, exc)
        return None

    async def get_index_price(self, symbol: str) -> Optional[float]:
        """Fetch index (spot) price from tickers."""
        try:
            result = await self._get(
                "/v5/market/tickers",
                {"category": "linear", "symbol": symbol},
            )
            items = result.get("list", [])
            if items:
                ip = items[0].get("indexPrice")
                if ip is not None:
                    return float(ip)
        except Exception as exc:
            logger.debug("Index price unavailable for %s: %s", symbol, exc)
        return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("BybitAdapter HTTP client closed")

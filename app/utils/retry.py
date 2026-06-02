"""Async retry decorator with exponential backoff."""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Callable, Type

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def async_retry(
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    retryable_exceptions: tuple[Type[Exception], ...] = (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    ),
) -> Callable:
    """
    Decorator: retry an async function with exponential backoff + jitter.

    Retries on:
      - Configured exception types (network/timeout errors)
      - httpx.HTTPStatusError with status in RETRYABLE_STATUS_CODES
      - Respects Retry-After header when present on 429 responses

    Args:
        max_attempts:          Total attempts (1 = no retry).
        base_delay:            Initial delay in seconds.
        max_delay:             Cap on delay.
        jitter:                Add random ±20% jitter to avoid thundering herd.
        retryable_exceptions:  Exceptions that trigger a retry.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                        raise
                    last_exc = exc
                    # Respect Retry-After header
                    retry_after = exc.response.headers.get("Retry-After")
                    if retry_after:
                        delay = float(retry_after)
                    else:
                        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                except retryable_exceptions as exc:
                    last_exc = exc
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                except Exception:
                    raise  # Don't retry unexpected exceptions

                if jitter:
                    delay *= 1.0 + random.uniform(-0.2, 0.2)
                delay = max(0.1, min(delay, max_delay))

                if attempt < max_attempts:
                    logger.warning(
                        "Attempt %d/%d failed (%s). Retrying in %.1fs.",
                        attempt, max_attempts, type(last_exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator

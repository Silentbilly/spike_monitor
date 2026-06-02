"""
Telegram Bot API service.

Sends text messages and photo+caption messages.
Uses httpx directly (no python-telegram-bot dependency).
All calls are retried with backoff.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from app.utils.retry import async_retry

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramService:
    """Thin wrapper over Telegram Bot HTTP API."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._base = f"{TELEGRAM_API_BASE}/bot{bot_token}"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    @async_retry(max_attempts=4, base_delay=2.0, max_delay=60.0)
    async def send_message(self, text: str, parse_mode: str = "HTML") -> Optional[int]:
        """
        Send a text message. Returns Telegram message_id or None on failure.
        """
        from app.constants import TELEGRAM_MAX_MSG_LEN
        if len(text) > TELEGRAM_MAX_MSG_LEN:
            text = text[:TELEGRAM_MAX_MSG_LEN - 10] + "\n…"

        client = await self._get_client()
        resp = await client.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        logger.warning("Telegram sendMessage not OK: %s", data)
        return None

    @async_retry(max_attempts=4, base_delay=2.0, max_delay=60.0)
    async def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        parse_mode: str = "HTML",
    ) -> Optional[int]:
        """
        Send a photo file with optional caption.
        Returns message_id or None.
        """
        if not Path(photo_path).exists():
            logger.error("Photo file not found: %s", photo_path)
            return None

        from app.constants import TELEGRAM_MAX_MSG_LEN
        if len(caption) > TELEGRAM_MAX_MSG_LEN:
            caption = caption[:TELEGRAM_MAX_MSG_LEN - 10] + "…"

        client = await self._get_client()
        with open(photo_path, "rb") as f:
            resp = await client.post(
                f"{self._base}/sendPhoto",
                data={
                    "chat_id": self._chat_id,
                    "caption": caption,
                    "parse_mode": parse_mode,
                },
                files={"photo": (Path(photo_path).name, f, "image/png")},
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        logger.warning("Telegram sendPhoto not OK: %s", data)
        return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

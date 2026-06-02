"""
Notification Service.

Formats and dispatches Telegram notifications for all event types.
Handles idempotency by checking NotificationLog before sending.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import Config
from app.domain.enums import NotificationType
from app.domain.models import (
    BreakdownSignal,
    HealthCheckLog,
    NotificationLog,
    SpikeEvent,
    WatchlistItem,
)
from app.services.telegram_service import TelegramService
from app.storage.repositories import NotificationRepository
from app.utils.formatting import fmt_float, fmt_pct, quality_emoji, score_bar, strength_emoji
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)


class NotificationService:
    """Formats messages and sends them via Telegram, tracking idempotency."""

    def __init__(
        self,
        telegram: TelegramService,
        notif_repo: NotificationRepository,
        config: Config,
    ) -> None:
        self._tg = telegram
        self._repo = notif_repo
        self._config = config

    # ------------------------------------------------------------------
    # Public send methods
    # ------------------------------------------------------------------

    async def send_spike_candidate(
        self,
        event: SpikeEvent,
        chart_path: Optional[str] = None,
    ) -> bool:
        """Send new spike candidate alert (with chart if available)."""
        already = await self._repo.already_sent(
            NotificationType.SPIKE_CANDIDATE,
            event.id,
            within_hours=self._config.notification_dedup_hours,
        )
        if already:
            logger.debug("Spike candidate %s already notified — skipping", event.symbol)
            return False

        text = self._format_spike_message(event)
        msg_id = await self._send(text, chart_path)
        await self._log(NotificationType.SPIKE_CANDIDATE, event.symbol, event.id, msg_id)
        return True

    async def send_watchlist_added(self, event: SpikeEvent, item: WatchlistItem) -> bool:
        """Notify when a strong spike is added to the watchlist."""
        already = await self._repo.already_sent(
            NotificationType.STRONG_SPIKE_WATCHLIST,
            event.id,
            within_hours=self._config.notification_dedup_hours,
        )
        if already:
            return False

        text = self._format_watchlist_added(event, item)
        msg_id = await self._send(text)
        await self._log(NotificationType.STRONG_SPIKE_WATCHLIST, event.symbol, event.id, msg_id)
        return True

    async def send_breakdown(
        self,
        signal: BreakdownSignal,
        chart_path: Optional[str] = None,
    ) -> bool:
        """Send breakdown confirmed alert (with chart if available)."""
        already = await self._repo.already_sent(
            NotificationType.BREAKDOWN_CONFIRMED,
            signal.id,
            within_hours=self._config.signal_cooldown_hours,
        )
        if already:
            return False

        text = self._format_breakdown_message(signal)
        msg_id = await self._send(text, chart_path)
        await self._log(NotificationType.BREAKDOWN_CONFIRMED, signal.symbol, signal.id, msg_id)
        return True

    async def send_setup_expired(self, item: WatchlistItem) -> bool:
        already = await self._repo.already_sent(
            NotificationType.SETUP_EXPIRED,
            item.id,
            within_hours=self._config.watchlist_ttl_hours,
        )
        if already:
            return False

        text = self._format_expired(item)
        msg_id = await self._send(text)
        await self._log(NotificationType.SETUP_EXPIRED, item.symbol, item.id, msg_id)
        return True

    async def send_health(self, log: HealthCheckLog) -> bool:
        text = self._format_health(log)
        msg_id = await self._send(text)
        await self._log(NotificationType.HEALTH, "system", log.id, msg_id)
        return True

    async def send_test(self) -> bool:
        text = (
            "✅ <b>SpikeMonitor — Test Message</b>\n\n"
            "Bot is alive and connected.\n"
            f"Time: {format_utc(utcnow())}"
        )
        msg_id = await self._send(text)
        return msg_id is not None

    async def send_error(self, context: str, error: str) -> None:
        text = (
            f"⚠️ <b>SpikeMonitor Error</b>\n"
            f"Context: <code>{context}</code>\n"
            f"Error: <code>{error[:500]}</code>\n"
            f"Time: {format_utc(utcnow())}"
        )
        try:
            await self._tg.send_message(text)
        except Exception as exc:
            logger.error("Failed to send error notification: %s", exc)

    # ------------------------------------------------------------------
    # Message formatters
    # ------------------------------------------------------------------

    def _format_spike_message(self, e: SpikeEvent) -> str:
        strength = e.strength if isinstance(e.strength, str) else e.strength.value
        emoji = strength_emoji(e.strength) if not isinstance(e.strength, str) else "🔴"
        tf = e.timeframe if isinstance(e.timeframe, str) else e.timeframe.value

        return (
            f"{emoji} <b>SPIKE CANDIDATE: {e.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Score: <b>{score_bar(e.score)}</b>\n"
            f"📈 Spike: <b>{fmt_pct(e.spike_pct)}</b>  [{strength.upper()}]\n"
            f"📉 Retrace: <b>{fmt_pct(-e.retrace_pct)}</b> of impulse\n"
            f"🕯 CLV: <b>{e.clv:.2f}</b> (−1=at low)\n"
            f"📦 RV20: <b>{e.rv20:.1f}x</b>\n"
            f"⚡ ATR×: <b>{e.atr_multiple:.1f}x</b>\n"
            f"💲 Spike High: <b>{fmt_float(e.spike_high)}</b>\n"
            f"💲 Spike Open: <b>{fmt_float(e.spike_open)}</b>\n"
            f"💲 Current: <b>{fmt_float(e.current_price)}</b>\n"
            f"⏱ TF context: <b>1D</b>\n"
            + (f"💸 Funding: <b>{e.funding_rate*100:.3f}%</b>\n" if e.funding_rate is not None else "")
            + f"\n📝 {e.explanation}\n"
            f"🕐 {format_utc(e.detected_at)}"
        )

    def _format_watchlist_added(self, e: SpikeEvent, item: WatchlistItem) -> str:
        return (
            f"👀 <b>WATCHLIST ADDED: {e.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Spike: <b>{fmt_pct(e.spike_pct)}</b>  Score: <b>{e.score:.0f}</b>\n"
            f"Monitoring: <b>4h + 1h</b> for consolidation & breakdown\n"
            f"⛔ Invalidation above: <b>{fmt_float(item.invalidation_level or 0)}</b>\n"
            f"📅 Expires: <b>{item.expires_at.strftime('%Y-%m-%d')}</b>\n"
            f"🕐 {format_utc(utcnow())}"
        )

    def _format_breakdown_message(self, s: BreakdownSignal) -> str:
        quality = s.quality if isinstance(s.quality, str) else s.quality.value
        q_emoji = quality_emoji(s.quality) if not isinstance(s.quality, str) else "🔴"
        tf = s.timeframe if isinstance(s.timeframe, str) else s.timeframe.value

        return (
            f"🚨 <b>BREAKDOWN CONFIRMED: {s.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Score: <b>{score_bar(s.score)}</b>\n"
            f"{q_emoji} Quality: <b>{quality.upper()}</b>\n"
            f"💲 Break Price: <b>{fmt_float(s.breakdown_price)}</b>\n"
            f"💲 Support: <b>{fmt_float(s.breakdown_level)}</b>\n"
            f"📈 Orig Spike: <b>{fmt_pct(s.spike_pct)}</b>\n"
            f"📉 Retrace: <b>{fmt_pct(-s.retrace_pct)}</b>\n"
            f"📦 Vol confirmed: <b>{'YES ✅' if s.volume_confirmed else 'NO'}</b>\n"
            f"⏱ Timeframe: <b>{tf}</b>\n"
            f"\n📝 {s.explanation}\n"
            f"🕐 {format_utc(s.triggered_at)}"
        )

    def _format_expired(self, item: WatchlistItem) -> str:
        return (
            f"⏰ <b>SETUP EXPIRED: {item.symbol}</b>\n"
            f"Spike was {fmt_pct(item.spike_pct)}, retrace {item.retrace_pct:.0f}%\n"
            f"Status: no breakdown confirmed within TTL\n"
            f"Added: {item.added_at.strftime('%Y-%m-%d')}  Expired: {item.expires_at.strftime('%Y-%m-%d')}"
        )

    def _format_health(self, log: HealthCheckLog) -> str:
        last_d = log.last_daily_scan.strftime("%H:%M") if log.last_daily_scan else "N/A"
        last_4h = log.last_4h_scan.strftime("%H:%M") if log.last_4h_scan else "N/A"
        last_1h = log.last_1h_scan.strftime("%H:%M") if log.last_1h_scan else "N/A"
        return (
            f"💚 <b>SpikeMonitor Health</b>\n"
            f"Watchlist: <b>{log.watchlist_count}</b> active\n"
            f"Spikes 24h: <b>{log.active_spikes_24h}</b>\n"
            f"Errors 24h: <b>{log.errors_24h}</b>\n"
            f"Last scans: 1D={last_d} / 4H={last_4h} / 1H={last_1h}\n"
            f"🕐 {format_utc(log.checked_at)}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(self, text: str, chart_path: Optional[str] = None) -> Optional[int]:
        try:
            if chart_path:
                return await self._tg.send_photo(chart_path, caption=text)
            else:
                return await self._tg.send_message(text)
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return None

    async def _log(
        self,
        ntype: NotificationType,
        symbol: str,
        ref_id: str,
        msg_id: Optional[int],
    ) -> None:
        log = NotificationLog(
            notification_type=ntype,
            symbol=symbol,
            reference_id=ref_id,
            sent_at=utcnow(),
            telegram_message_id=msg_id,
            chat_id=self._config.telegram_chat_id,
            success=msg_id is not None,
        )
        try:
            await self._repo.save(log)
        except Exception as exc:
            logger.error("Failed to log notification: %s", exc)

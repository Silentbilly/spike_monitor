"""
Application entry point.

Wires all dependencies and starts the APScheduler event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import get_config
from app.domain.enums import Timeframe
from app.exchanges.bybit import BybitAdapter
from app.logger import setup_logging
from app.scheduler import build_scheduler, register_jobs
from app.services.breakdown_service import BreakdownService
from app.services.chart_service import ChartService
from app.services.consolidation_service import ConsolidationService
from app.services.health_service import HealthService
from app.services.notification_service import NotificationService
from app.services.spike_detector_service import SpikeDetectorService
from app.services.telegram_service import TelegramService
from app.services.universe_service import UniverseService
from app.services.watchlist_service import WatchlistService
from app.storage.db import init_db
from app.storage.repositories import (
    BreakdownRepository,
    HealthRepository,
    NotificationRepository,
    SpikeEventRepository,
    WatchlistRepository,
)

logger = logging.getLogger(__name__)


class Application:
    """Dependency container + job runner."""

    def __init__(self) -> None:
        self.config = get_config()
        self.config.validate()

        # Exchange adapter
        self.exchange = BybitAdapter(
            base_url=self.config.bybit_base_url,
            timeout=self.config.exchange_timeout,
            max_retries=self.config.exchange_max_retries,
        )

        # Telegram
        self.telegram = TelegramService(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
        )
        self.chart = ChartService(self.config)

        # Repositories (initialised after DB)
        self._session_factory = None

    def _init_repos(self):
        from app.storage.db import get_session_factory
        sf = get_session_factory()

        async def make_spike_repo():
            return SpikeEventRepository(await sf())

        # Use a simple factory pattern
        self.spike_repo = None
        self.watchlist_repo = None
        self.breakdown_repo = None
        self.notif_repo = None
        self.health_repo = None
        self._session_factory = sf

    async def _get_repos(self):
        """Get fresh session-bound repositories."""
        from app.storage.db import get_session_factory
        sf = get_session_factory()
        session = await sf()
        spike_repo = SpikeEventRepository(session)
        watchlist_repo = WatchlistRepository(session)
        breakdown_repo = BreakdownRepository(session)
        notif_repo = NotificationRepository(session)
        health_repo = HealthRepository(session)
        return spike_repo, watchlist_repo, breakdown_repo, notif_repo, health_repo, session

    async def run_daily_scan(self) -> None:
        """Scan all instruments for daily spikes, notify and add to watchlist."""
        logger.info("=== Starting Daily Spike Scan ===")
        repos = await self._get_repos()
        spike_repo, watchlist_repo, breakdown_repo, notif_repo, health_repo, session = repos

        try:
            notif_service = NotificationService(self.telegram, notif_repo, self.config)
            universe_service = UniverseService(self.exchange, self.config)
            detector = SpikeDetectorService(self.exchange, self.config)
            watchlist_service = WatchlistService(watchlist_repo, notif_repo, self.config)

            instruments = await universe_service.get_tradeable_universe()
            events = await detector.scan_universe(instruments)

            for event in events:
                await spike_repo.save(event)

                # Generate chart
                candles = await self.exchange.get_klines(
                    symbol=event.symbol, interval="D", limit=60
                )
                chart_path = self.chart.render_spike_chart(event, candles) if candles else None
                if chart_path:
                    event.chart_path = chart_path
                    await spike_repo.save(event)

                # Notify
                await notif_service.send_spike_candidate(event, chart_path)

                # Add strong spikes to watchlist
                if event.is_strong:
                    added, reason = await watchlist_service.add_if_eligible(event)
                    if added:
                        item = (await watchlist_repo.get_by_symbol(event.symbol))[0]
                        await notif_service.send_watchlist_added(event, item)

            HealthService.mark_daily_scan()
            logger.info("Daily scan complete: %d events processed", len(events))
        finally:
            await session.close()

    async def run_watchlist_scan(self, timeframe: Timeframe) -> None:
        """Scan active watchlist items for consolidation + breakdown."""
        logger.info("=== Watchlist Scan [%s] ===", timeframe.value)
        repos = await self._get_repos()
        spike_repo, watchlist_repo, breakdown_repo, notif_repo, health_repo, session = repos

        try:
            notif_service = NotificationService(self.telegram, notif_repo, self.config)
            watchlist_service = WatchlistService(watchlist_repo, notif_repo, self.config)
            consolidation_service = ConsolidationService(self.exchange, watchlist_service, self.config)
            breakdown_service = BreakdownService(
                self.exchange, watchlist_service, breakdown_repo, notif_repo, self.config
            )

            # Expire stale items
            expired = await watchlist_service.expire_stale_items()
            for item in expired:
                await notif_service.send_setup_expired(item)

            # Consolidation scan
            await consolidation_service.scan_watchlist(timeframe)

            # Breakdown scan
            signals = await breakdown_service.scan_for_breakdowns(timeframe)
            for signal in signals:
                # Generate breakdown chart
                candles = await self.exchange.get_klines(
                    symbol=signal.symbol,
                    interval=timeframe.value,
                    limit=80,
                )
                item_list = await watchlist_repo.get_by_symbol(signal.symbol)
                item = item_list[0] if item_list else None

                chart_path = None
                if candles and item:
                    chart_path = self.chart.render_breakdown_chart(signal, item, candles)

                if chart_path:
                    signal.chart_path = chart_path
                    await breakdown_repo.save(signal)

                await notif_service.send_breakdown(signal, chart_path)

            if timeframe == Timeframe.H4:
                HealthService.mark_4h_scan()
            elif timeframe == Timeframe.H1:
                HealthService.mark_1h_scan()

        finally:
            await session.close()

    async def run_health_ping(self) -> None:
        repos = await self._get_repos()
        spike_repo, watchlist_repo, breakdown_repo, notif_repo, health_repo, session = repos
        try:
            notif_service = NotificationService(self.telegram, notif_repo, self.config)
            svc = HealthService(spike_repo, watchlist_repo, notif_repo, health_repo, notif_service)
            await svc.run_health_check(send_ping=True)
        finally:
            await session.close()

    async def start(self) -> None:
        """Initialise DB, register jobs, start scheduler."""
        setup_logging(self.config.log_level, self.config.log_json)
        await init_db(self.config.db_url)
        self._init_repos()

        scheduler = build_scheduler(self.config)
        register_jobs(
            scheduler,
            daily_scan_fn=self.run_daily_scan,
            watchlist_4h_fn=lambda: self.run_watchlist_scan(Timeframe.H4),
            watchlist_1h_fn=lambda: self.run_watchlist_scan(Timeframe.H1),
            health_fn=self.run_health_ping,
            config=self.config,
        )

        loop = asyncio.get_event_loop()

        def shutdown():
            logger.info("Shutdown signal received")
            scheduler.shutdown(wait=False)
            loop.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown)

        scheduler.start()
        logger.info("SpikeMonitor started. Scheduler running. Press Ctrl+C to stop.")

        try:
            await asyncio.Event().wait()  # run forever
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            scheduler.shutdown(wait=True)
            await self.exchange.close()
            await self.telegram.close()
            logger.info("SpikeMonitor stopped.")


def main() -> None:
    app = Application()
    asyncio.run(app.start())


if __name__ == "__main__":
    main()

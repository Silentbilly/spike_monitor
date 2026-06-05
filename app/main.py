"""
Application entry point.

Wires all dependencies and starts the APScheduler event loop.

ADDITIVE CHANGES:
  - IBC repositories added to _get_repos() via _get_ibc_repos()
  - Three new IBC job methods: run_ibc_impulse_scan(), run_ibc_base_monitor(),
    run_ibc_breakout_check()
  - register_ibc_jobs() called inside start()
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import get_config
from app.domain.enums import Timeframe
from app.exchanges.bybit import BybitAdapter
from app.logger import setup_logging
from app.scheduler import build_scheduler, register_ibc_jobs, register_jobs
from app.services.breakdown_service import BreakdownService
from app.services.chart_service import ChartService
from app.services.consolidation_service import ConsolidationService
from app.services.health_service import HealthService
from app.services.ibc_breakout_service import IBCBreakoutService
from app.services.ibc_monitor_service import IBCMonitorService
from app.services.impulse_detector_service import ImpulseDetectorService
from app.services.notification_service import NotificationService
from app.services.spike_detector_service import SpikeDetectorService
from app.services.telegram_service import TelegramService
from app.services.universe_service import UniverseService
from app.services.watchlist_service import WatchlistService
from app.storage.db import init_db
from app.storage.ibc_repositories import (
    IBCBreakoutRepository,
    IBCWatchlistRepository,
    ImpulseEventRepository,
)
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

        self._session_factory = get_session_factory()

    async def _get_repos(self):
        """Get fresh session-bound repositories (existing spike pipeline)."""
        from app.storage.db import get_session_factory

        sf = get_session_factory()
        session = sf()
        spike_repo = SpikeEventRepository(session)
        watchlist_repo = WatchlistRepository(session)
        breakdown_repo = BreakdownRepository(session)
        notif_repo = NotificationRepository(session)
        health_repo = HealthRepository(session)
        return spike_repo, watchlist_repo, breakdown_repo, notif_repo, health_repo, session

    async def _get_ibc_repos(self):
        """Get fresh session-bound IBC repositories."""
        from app.storage.db import get_session_factory

        sf = get_session_factory()
        session = sf()
        impulse_repo = ImpulseEventRepository(session)
        ibc_watchlist_repo = IBCWatchlistRepository(session)
        ibc_breakout_repo = IBCBreakoutRepository(session)
        return impulse_repo, ibc_watchlist_repo, ibc_breakout_repo, session

    # ------------------------------------------------------------------
    # Existing spike-short pipeline jobs (unchanged)
    # ------------------------------------------------------------------

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

                candles = await self.exchange.get_klines(
                    symbol=event.symbol, interval="D", limit=60
                )
                chart_path = self.chart.render_spike_chart(event, candles) if candles else None
                if chart_path:
                    event.chart_path = chart_path
                    await spike_repo.save(event)

                await notif_service.send_spike_candidate(event, chart_path)

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

            expired = await watchlist_service.expire_stale_items()
            for item in expired:
                await notif_service.send_setup_expired(item)

            await consolidation_service.scan_watchlist(timeframe)

            signals = await breakdown_service.scan_for_breakdowns(timeframe)
            for signal in signals:
                candles = await self.exchange.get_klines(
                    symbol=signal.symbol, interval=timeframe.value, limit=80
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

    # ------------------------------------------------------------------
    # IBC pipeline jobs (additive)
    # ------------------------------------------------------------------

    async def run_ibc_impulse_scan(self) -> None:
        """Phase 1: Full-universe impulse scan on 1H and 15M timeframes."""
        logger.info("=== IBC Phase 1: Impulse Scan ===")
        ibc_repos = await self._get_ibc_repos()
        impulse_repo, ibc_watchlist_repo, ibc_breakout_repo, session = ibc_repos
        try:
            universe_service = UniverseService(self.exchange, self.config)
            instruments = await universe_service.get_tradeable_universe()

            detector = ImpulseDetectorService(
                exchange=self.exchange,
                impulse_repo=impulse_repo,
                watchlist_repo=ibc_watchlist_repo,
                config=self.config,
            )
            events = await detector.scan_universe(instruments)
            logger.info("IBC impulse scan complete: %d new impulse events", len(events))
        except Exception as exc:
            logger.error("IBC impulse scan error: %s", exc, exc_info=True)
        finally:
            await session.close()

    async def run_ibc_base_monitor(self) -> None:
        """Phase 2: Base/level formation monitor. Sends 'Base Formed' alerts."""
        logger.info("=== IBC Phase 2: Base Monitor ===")
        ibc_repos = await self._get_ibc_repos()
        impulse_repo, ibc_watchlist_repo, ibc_breakout_repo, session = ibc_repos
        try:
            monitor = IBCMonitorService(
                exchange=self.exchange,
                watchlist_repo=ibc_watchlist_repo,
                telegram=self.telegram,
                config=self.config,
            )
            confirmed = await monitor.run_monitor_cycle()
            logger.info("IBC base monitor: %d base(s) confirmed", len(confirmed))
        except Exception as exc:
            logger.error("IBC base monitor error: %s", exc, exc_info=True)
        finally:
            await session.close()

    async def run_ibc_breakout_check(self) -> None:
        """Phase 3: Breakout detection for base-confirmed entries."""
        logger.info("=== IBC Phase 3: Breakout Check ===")
        ibc_repos = await self._get_ibc_repos()
        impulse_repo, ibc_watchlist_repo, ibc_breakout_repo, session = ibc_repos
        try:
            breakout_svc = IBCBreakoutService(
                exchange=self.exchange,
                watchlist_repo=ibc_watchlist_repo,
                breakout_repo=ibc_breakout_repo,
                telegram=self.telegram,
                config=self.config,
            )
            events = await breakout_svc.run_breakout_cycle()
            logger.info("IBC breakout check: %d breakout(s) triggered", len(events))
        except Exception as exc:
            logger.error("IBC breakout check error: %s", exc, exc_info=True)
        finally:
            await session.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise DB, register jobs, start scheduler."""
        setup_logging(self.config.log_level, self.config.log_json)
        await init_db(self.config.db_url)
        self._init_repos()

        scheduler = build_scheduler(self.config)

        # Existing spike-short pipeline
        register_jobs(
            scheduler,
            daily_scan_fn=self.run_daily_scan,
            watchlist_4h_fn=lambda: self.run_watchlist_scan(Timeframe.H4),
            watchlist_1h_fn=lambda: self.run_watchlist_scan(Timeframe.H1),
            health_fn=self.run_health_ping,
            config=self.config,
        )

        # IBC pipeline (additive)
        register_ibc_jobs(
            scheduler,
            ibc_impulse_fn=self.run_ibc_impulse_scan,
            ibc_monitor_fn=self.run_ibc_base_monitor,
            ibc_breakout_fn=self.run_ibc_breakout_check,
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
            await asyncio.Event().wait()
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

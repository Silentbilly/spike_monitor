"""
CLI entry point using Typer.

Available commands:
  full-scan           — Run universe fetch + daily spike scan once
  daily-scan          — Run daily spike scan (single run)
  watchlist-scan-4h   — Run 4H watchlist scan once
  watchlist-scan-1h   — Run 1H watchlist scan once
  send-test-telegram  — Send a test Telegram message
  healthcheck         — Run health check and print results
  list-watchlist      — Print active watchlist to stdout
  cleanup-expired     — Remove terminal items from DB
"""

from __future__ import annotations

import asyncio
import sys

import typer

app = typer.Typer(
    name="spike-monitor",
    help="Crypto futures short-setup monitoring CLI",
    add_completion=False,
)


def _run(coro):
    """Run an async coroutine from sync context."""
    return asyncio.run(coro)


async def _get_application():
    """Initialise and return the Application without starting the scheduler."""
    from app.config import get_config
    from app.logger import setup_logging
    from app.main import Application
    from app.storage.db import init_db

    config = get_config()
    config.validate()
    setup_logging(config.log_level, config.log_json)
    await init_db(config.db_url)

    app_instance = Application.__new__(Application)
    app_instance.config = config

    from app.exchanges.bybit import BybitAdapter
    from app.services.chart_service import ChartService
    from app.services.telegram_service import TelegramService

    app_instance.exchange = BybitAdapter(
        base_url=config.bybit_base_url,
        timeout=config.exchange_timeout,
        max_retries=config.exchange_max_retries,
    )
    app_instance.telegram = TelegramService(
        bot_token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
    )
    app_instance.chart = ChartService(config)
    app_instance._init_repos()
    return app_instance


@app.command("full-scan")
def full_scan():
    """Fetch universe, run daily spike scan, update watchlist."""
    async def _run_inner():
        application = await _get_application()
        try:
            await application.run_daily_scan()
        finally:
            await application.exchange.close()
            await application.telegram.close()
    _run(_run_inner())
    typer.echo("Full scan complete.")


@app.command("daily-scan")
def daily_scan():
    """Run daily spike scan only (universe assumed already known)."""
    full_scan()


@app.command("watchlist-scan-4h")
def watchlist_scan_4h():
    """Run 4H watchlist consolidation + breakdown scan."""
    from app.domain.enums import Timeframe

    async def _run_inner():
        application = await _get_application()
        try:
            await application.run_watchlist_scan(Timeframe.H4)
        finally:
            await application.exchange.close()
            await application.telegram.close()
    _run(_run_inner())
    typer.echo("4H watchlist scan complete.")


@app.command("watchlist-scan-1h")
def watchlist_scan_1h():
    """Run 1H watchlist consolidation + breakdown scan."""
    from app.domain.enums import Timeframe

    async def _run_inner():
        application = await _get_application()
        try:
            await application.run_watchlist_scan(Timeframe.H1)
        finally:
            await application.exchange.close()
            await application.telegram.close()
    _run(_run_inner())
    typer.echo("1H watchlist scan complete.")


@app.command("send-test-telegram")
def send_test_telegram():
    """Send a test message to the configured Telegram chat."""
    async def _run_inner():
        application = await _get_application()
        ok = await application.telegram.send_message(
            "✅ <b>SpikeMonitor Test</b>\nConfiguration is working correctly."
        )
        return ok is not None
    ok = _run(_run_inner())
    if ok:
        typer.echo("✅ Test message sent.")
    else:
        typer.echo("❌ Failed to send test message. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        sys.exit(1)


@app.command("healthcheck")
def healthcheck():
    """Print current health status to stdout."""
    async def _run_inner():
        application = await _get_application()
        from app.services.health_service import HealthService
        from app.services.notification_service import NotificationService
        from app.storage.repositories import (
            HealthRepository,
            NotificationRepository,
            SpikeEventRepository,
            WatchlistRepository,
        )
        repos = await application._get_repos()
        spike_repo, watchlist_repo, _, notif_repo, health_repo, session = repos
        try:
            notif_service = NotificationService(application.telegram, notif_repo, application.config)
            svc = HealthService(spike_repo, watchlist_repo, notif_repo, health_repo, notif_service)
            log = await svc.run_health_check(send_ping=False)
            typer.echo(f"Watchlist active:  {log.watchlist_count}")
            typer.echo(f"Spikes (24h):      {log.active_spikes_24h}")
            typer.echo(f"Errors (24h):      {log.errors_24h}")
        finally:
            await session.close()
            await application.exchange.close()
    _run(_run_inner())


@app.command("list-watchlist")
def list_watchlist():
    """Print all active watchlist items."""
    async def _run_inner():
        application = await _get_application()
        repos = await application._get_repos()
        _, watchlist_repo, _, _, _, session = repos
        try:
            items = await watchlist_repo.get_active()
            if not items:
                typer.echo("Watchlist is empty.")
                return
            typer.echo(f"\n{'Symbol':<15} {'Status':<20} {'Score':>6} {'Spike%':>8} {'Retrace%':>9} {'Added'}")
            typer.echo("-" * 75)
            for item in items:
                typer.echo(
                    f"{item.symbol:<15} {item.status:<20} "
                    f"{item.current_score:>6.1f} {item.spike_pct:>+8.1f}% "
                    f"{item.retrace_pct:>8.0f}%  {item.added_at.strftime('%Y-%m-%d')}"
                )
        finally:
            await session.close()
            await application.exchange.close()
    _run(_run_inner())


@app.command("cleanup-expired")
def cleanup_expired():
    """Remove stale/expired terminal items from the database."""
    async def _run_inner():
        application = await _get_application()
        from app.services.watchlist_service import WatchlistService
        repos = await application._get_repos()
        _, watchlist_repo, _, notif_repo, _, session = repos
        try:
            svc = WatchlistService(watchlist_repo, notif_repo, application.config)
            deleted = await svc.cleanup_terminal()
            typer.echo(f"Cleaned up {deleted} terminal items.")
        finally:
            await session.close()
            await application.exchange.close()
    _run(_run_inner())


def main() -> None:
    app()


if __name__ == "__main__":
    main()

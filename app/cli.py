"""
CLI entry point using Typer.

Available commands (existing):
  full-scan           — Run universe fetch + daily spike scan once
  daily-scan          — Run daily spike scan (single run)
  watchlist-scan-4h   — Run 4H watchlist scan once
  watchlist-scan-1h   — Run 1H watchlist scan once
  send-test-telegram  — Send a test Telegram message
  healthcheck         — Run health check and print results
  list-watchlist      — Print active watchlist to stdout
  cleanup-expired     — Remove terminal items from DB

ADDITIVE IBC commands:
  ibc-scan            — Run IBC Phase 1 impulse scan once
  list-ibc-watchlist  — Print IBC watchlist entries
  list-ibc-breakouts  — Print recent IBC breakout events
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


# ===========================================================================
# Existing commands (unchanged)
# ===========================================================================


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
    """Print all active spike-short watchlist items."""
    async def _run_inner():
        application = await _get_application()
        repos = await application._get_repos()
        _, watchlist_repo, _, _, _, session = repos
        try:
            items = await watchlist_repo.get_active()
            if not items:
                typer.echo("Watchlist is empty.")
                return
            typer.echo(
                f"\n{'Symbol':<15} {'Status':<20} {'Score':>6} {'Spike%':>8} "
                f"{'Retrace%':>9} {'Added'}"
            )
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


# ===========================================================================
# IBC commands (additive)
# ===========================================================================


@app.command("ibc-scan")
def ibc_scan():
    """Run IBC Phase 1 impulse scan across the full universe (once)."""
    async def _run_inner():
        application = await _get_application()
        try:
            await application.run_ibc_impulse_scan()
        finally:
            await application.exchange.close()
            await application.telegram.close()
    _run(_run_inner())
    typer.echo("IBC impulse scan complete.")


@app.command("list-ibc-watchlist")
def list_ibc_watchlist(
    status: str = typer.Option("", help="Filter by status (e.g. impulse_detected, base_confirmed)"),
    limit: int = typer.Option(50, help="Max rows to display"),
):
    """Print IBC watchlist entries."""
    async def _run_inner():
        application = await _get_application()
        ibc_repos = await application._get_ibc_repos()
        _, ibc_watchlist_repo, _, session = ibc_repos
        try:
            entries = await ibc_watchlist_repo.get_all(limit=limit)
            if status:
                entries = [e for e in entries if e.status == status]
            if not entries:
                typer.echo("No IBC watchlist entries found.")
                return
            typer.echo(
                f"\n{'Symbol':<16} {'TF':<6} {'Dir':<6} {'Status':<22} "
                f"{'Impulse%':>9} {'Level':>12} {'Touches':>8} {'Added'}"
            )
            typer.echo("-" * 100)
            for e in entries:
                dir_str = e.direction if isinstance(e.direction, str) else e.direction.value
                status_str = e.status if isinstance(e.status, str) else e.status.value
                level_str = f"{e.level_price:.4f}" if e.level_price else "—"
                touches_str = str(e.level_touches) if e.level_touches else "—"
                typer.echo(
                    f"{e.symbol:<16} {e.timeframe:<6} {dir_str.upper():<6} "
                    f"{status_str:<22} {e.impulse_move_pct:>+9.1f}% "
                    f"{level_str:>12} {touches_str:>8}  "
                    f"{e.added_at.strftime('%Y-%m-%d %H:%M')}"
                )
        finally:
            await session.close()
            await application.exchange.close()
    _run(_run_inner())


@app.command("list-ibc-breakouts")
def list_ibc_breakouts(
    hours: int = typer.Option(48, help="Look back N hours"),
):
    """Print recent IBC breakout events."""
    async def _run_inner():
        application = await _get_application()
        ibc_repos = await application._get_ibc_repos()
        _, _, ibc_breakout_repo, session = ibc_repos
        try:
            events = await ibc_breakout_repo.get_recent(hours=hours)
            if not events:
                typer.echo(f"No IBC breakouts in the last {hours}h.")
                return
            typer.echo(
                f"\n{'Symbol':<16} {'TF':<6} {'Dir':<6} {'Score':>6} "
                f"{'BrkPrice':>12} {'Level':>12} {'Dist%':>7} {'Vol':>5} {'Triggered'}"
            )
            typer.echo("-" * 100)
            for e in events:
                dir_str = e.direction if isinstance(e.direction, str) else e.direction.value
                vol_str = "✓" if e.volume_confirmed else "✗"
                typer.echo(
                    f"{e.symbol:<16} {e.timeframe:<6} {dir_str.upper():<6} "
                    f"{e.score:>6.0f} {e.breakout_price:>12.4f} "
                    f"{e.level_price:>12.4f} {e.distance_pct:>+7.2f}% "
                    f"{vol_str:>5}  "
                    f"{e.triggered_at.strftime('%Y-%m-%d %H:%M')}"
                )
        finally:
            await session.close()
            await application.exchange.close()
    _run(_run_inner())


def main() -> None:
    app()


if __name__ == "__main__":
    main()

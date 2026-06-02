"""
Application configuration loaded from environment variables / .env file.

All thresholds are configurable — change values in .env without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of app/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, str(default)).lower()
    return v in ("1", "true", "yes")


def _get_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class Config:
    # ----------------------------------------------------------------
    # Telegram
    # ----------------------------------------------------------------
    telegram_bot_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _get("TELEGRAM_CHAT_ID"))

    # ----------------------------------------------------------------
    # Database
    # ----------------------------------------------------------------
    db_url: str = field(default_factory=lambda: _get("DB_URL", "sqlite:///./data/spike_monitor.db"))

    # ----------------------------------------------------------------
    # Exchange
    # ----------------------------------------------------------------
    bybit_base_url: str = field(default_factory=lambda: _get("BYBIT_BASE_URL", "https://api.bybit.com"))
    exchange_timeout: float = field(default_factory=lambda: _get_float("EXCHANGE_TIMEOUT", 15.0))
    exchange_max_retries: int = field(default_factory=lambda: _get_int("EXCHANGE_MAX_RETRIES", 4))

    # ----------------------------------------------------------------
    # Spike detection thresholds
    # ----------------------------------------------------------------
    spike_threshold_pct: float = field(default_factory=lambda: _get_float("SPIKE_THRESHOLD_PCT", 30.0))
    strong_spike_threshold_pct: float = field(default_factory=lambda: _get_float("STRONG_SPIKE_THRESHOLD_PCT", 50.0))
    wick_ratio_min: float = field(default_factory=lambda: _get_float("WICK_RATIO_MIN", 0.40))
    retrace_threshold_pct: float = field(default_factory=lambda: _get_float("RETRACE_THRESHOLD_PCT", 70.0))
    strong_retrace_pct: float = field(default_factory=lambda: _get_float("STRONG_RETRACE_PCT", 80.0))

    # ----------------------------------------------------------------
    # Scoring / signal filters
    # ----------------------------------------------------------------
    min_score_alert: float = field(default_factory=lambda: _get_float("MIN_SCORE_ALERT", 35.0))
    min_score_watchlist: float = field(default_factory=lambda: _get_float("MIN_SCORE_WATCHLIST", 45.0))
    rv20_min: float = field(default_factory=lambda: _get_float("RV20_MIN", 1.5))
    rv20_strong: float = field(default_factory=lambda: _get_float("RV20_STRONG", 3.0))

    # ----------------------------------------------------------------
    # Volume / liquidity filters
    # ----------------------------------------------------------------
    min_avg_quote_volume_usdt: float = field(default_factory=lambda: _get_float("MIN_AVG_QUOTE_VOLUME_USDT", 500_000.0))
    min_history_days: int = field(default_factory=lambda: _get_int("MIN_HISTORY_DAYS", 30))
    max_universe_size: int = field(default_factory=lambda: _get_int("MAX_UNIVERSE_SIZE", 300))

    # ----------------------------------------------------------------
    # Consolidation detection
    # ----------------------------------------------------------------
    consolidation_min_bars: int = field(default_factory=lambda: _get_int("CONSOLIDATION_MIN_BARS", 3))
    consolidation_max_bars: int = field(default_factory=lambda: _get_int("CONSOLIDATION_MAX_BARS", 20))
    consolidation_max_range_pct: float = field(default_factory=lambda: _get_float("CONSOLIDATION_MAX_RANGE_PCT", 8.0))
    consolidation_contraction_threshold: float = field(default_factory=lambda: _get_float("CONSOLIDATION_CONTRACTION_THRESHOLD", 0.85))

    # ----------------------------------------------------------------
    # Breakdown detection
    # ----------------------------------------------------------------
    breakdown_confirmation_pct: float = field(default_factory=lambda: _get_float("BREAKDOWN_CONFIRMATION_PCT", 0.3))
    breakdown_volume_multiplier: float = field(default_factory=lambda: _get_float("BREAKDOWN_VOLUME_MULTIPLIER", 1.3))

    # ----------------------------------------------------------------
    # Watchlist / TTL
    # ----------------------------------------------------------------
    watchlist_ttl_hours: float = field(default_factory=lambda: _get_float("WATCHLIST_TTL_HOURS", 168.0))  # 7 days
    signal_cooldown_hours: float = field(default_factory=lambda: _get_float("SIGNAL_COOLDOWN_HOURS", 24.0))
    notification_dedup_hours: float = field(default_factory=lambda: _get_float("NOTIFICATION_DEDUP_HOURS", 6.0))

    # ----------------------------------------------------------------
    # Scheduler intervals (cron-style, for APScheduler)
    # ----------------------------------------------------------------
    daily_scan_cron: str = field(default_factory=lambda: _get("DAILY_SCAN_CRON", "0 1 * * *"))    # 01:00 UTC daily
    scan_4h_cron: str = field(default_factory=lambda: _get("SCAN_4H_CRON", "0 */4 * * *"))
    scan_1h_cron: str = field(default_factory=lambda: _get("SCAN_1H_CRON", "5 * * * *"))
    health_ping_cron: str = field(default_factory=lambda: _get("HEALTH_PING_CRON", "0 */6 * * *"))

    # ----------------------------------------------------------------
    # Chart output
    # ----------------------------------------------------------------
    chart_output_dir: str = field(default_factory=lambda: _get("CHART_OUTPUT_DIR", "./data/charts"))
    chart_dpi: int = field(default_factory=lambda: _get_int("CHART_DPI", 150))

    # ----------------------------------------------------------------
    # Logging
    # ----------------------------------------------------------------
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    log_json: bool = field(default_factory=lambda: _get_bool("LOG_JSON", False))

    # ----------------------------------------------------------------
    # Universe filtering
    # ----------------------------------------------------------------
    blacklist: list[str] = field(default_factory=lambda: _get_list("BLACKLIST", ""))
    whitelist: list[str] = field(default_factory=lambda: _get_list("WHITELIST", ""))

    # ----------------------------------------------------------------
    # Optional enrichments
    # ----------------------------------------------------------------
    enable_funding_enrichment: bool = field(default_factory=lambda: _get_bool("ENABLE_FUNDING_ENRICHMENT", True))
    enable_oi_enrichment: bool = field(default_factory=lambda: _get_bool("ENABLE_OI_ENRICHMENT", False))

    def validate(self) -> None:
        """Raise ValueError for critical missing config."""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")


# Singleton
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config

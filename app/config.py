"""
Application configuration loaded from environment variables / .env file.

All thresholds are configurable — change values in .env without touching code.

ADDITIVE CHANGES (IBC_* keys appended at the bottom):
  IBC impulse detection thresholds
  IBC level / base thresholds
  IBC breakout thresholds
  IBC scheduler intervals
  IBC cooldown / TTL
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

    # ================================================================
    # IBC (Impulse → Base → Continuation) configuration
    # All keys prefixed IBC_
    # ================================================================

    # ---- Phase 1: Impulse detection --------------------------------
    ibc_impulse_min_pct: float = field(
        default_factory=lambda: _get_float("IBC_IMPULSE_MIN_PCT", 15.0)
    )
    """Minimum % move to qualify as an impulse (default 15%)."""

    ibc_impulse_max_bars: int = field(
        default_factory=lambda: _get_int("IBC_IMPULSE_MAX_BARS", 5)
    )
    """Maximum consecutive bars for an impulse window (default 5)."""

    ibc_impulse_rv_min: float = field(
        default_factory=lambda: _get_float("IBC_IMPULSE_RV_MIN", 1.5)
    )
    """Minimum relative volume (impulse avg / 20-bar avg) for impulse (default 1.5×)."""

    ibc_impulse_atr_min: float = field(
        default_factory=lambda: _get_float("IBC_IMPULSE_ATR_MIN", 3.0)
    )
    """Minimum ATR multiple for impulse move (default 3.0×)."""

    ibc_impulse_scan_cron: str = field(
        default_factory=lambda: _get("IBC_IMPULSE_SCAN_CRON", "0 */4 * * *")
    )
    """Cron for Phase 1 full-universe scan (default every 4 hours)."""

    # ---- Phase 2: Level / Base detection ---------------------------
    ibc_level_cluster_pct: float = field(
        default_factory=lambda: _get_float("IBC_LEVEL_CLUSTER_PCT", 1.0)
    )
    """±% corridor for clustering extremes into a level (default 1.0%)."""

    ibc_level_min_touches: int = field(
        default_factory=lambda: _get_int("IBC_LEVEL_MIN_TOUCHES", 2)
    )
    """Minimum extreme touches to validate a level (default 2)."""

    ibc_level_max_age_bars: int = field(
        default_factory=lambda: _get_int("IBC_LEVEL_MAX_AGE_BARS", 30)
    )
    """Maximum bar age for a level to remain valid (default 30 bars)."""

    ibc_base_max_range_pct: float = field(
        default_factory=lambda: _get_float("IBC_BASE_MAX_RANGE_PCT", 10.0)
    )
    """Maximum consolidation range % for base to qualify (default 10%)."""

    ibc_base_volume_decay: float = field(
        default_factory=lambda: _get_float("IBC_BASE_VOLUME_DECAY", 0.6)
    )
    """Maximum ratio of base avg volume / impulse avg volume (default 0.6)."""

    # ---- Phase 2 ceiling detection ----------------------------------
    ibc_ceiling_cluster_tol_pct: float = field(
        default_factory=lambda: _get_float("IBC_CEILING_CLUSTER_TOL_PCT", 2.0)
    )
    """Tolerance % for grouping highs/lows into the same ceiling cluster (default 2.0%)."""

    ibc_ceiling_min_touches: int = field(
        default_factory=lambda: _get_int("IBC_CEILING_MIN_TOUCHES", 8)
    )
    """Minimum bar highs/lows that must touch the ceiling cluster (default 8)."""

    ibc_ceiling_min_flat_ratio: float = field(
        default_factory=lambda: _get_float("IBC_CEILING_MIN_FLAT_RATIO", 0.25)
    )
    """Minimum fraction of base bars that must touch the ceiling (default 0.25)."""

    ibc_ceiling_max_age_bars: int = field(
        default_factory=lambda: _get_int("IBC_CEILING_MAX_AGE_BARS", 120)
    )
    """Max bars to look back when searching for a ceiling base (default 120)."""

    ibc_scan_timeframes: list[str] = field(
        default_factory=lambda: [
            tf.strip()
            for tf in os.getenv("IBC_SCAN_TIMEFRAMES", "60,15").split(",")
            if tf.strip()
        ]
    )
    """Bybit interval strings to scan for IBC impulses (default: 60,15 = 1H and 15M)."""

    ibc_monitor_cron: str = field(
        default_factory=lambda: _get("IBC_MONITOR_CRON", "*/30 * * * *")
    )
    """Cron for Phase 2 base monitor (default every 30 minutes)."""

    # ---- Phase 3: Breakout detection --------------------------------
    ibc_breakout_confirm_pct: float = field(
        default_factory=lambda: _get_float("IBC_BREAKOUT_CONFIRM_PCT", 0.3)
    )
    """Close must be ≥ this % beyond level to confirm breakout (default 0.3%)."""

    ibc_breakout_vol_mult: float = field(
        default_factory=lambda: _get_float("IBC_BREAKOUT_VOL_MULT", 1.3)
    )
    """Breakout volume must be ≥ avg × this multiplier (default 1.3×)."""

    ibc_breakout_cron: str = field(
        default_factory=lambda: _get("IBC_BREAKOUT_CRON", "*/15 * * * *")
    )
    """Cron for Phase 3 breakout polling (default every 15 minutes)."""

    # ---- Deduplication / TTL ----------------------------------------
    ibc_cooldown_h: float = field(
        default_factory=lambda: _get_float("IBC_COOLDOWN_H", 24.0)
    )
    """Hours before the same setup can trigger another breakout alert (default 24h)."""

    ibc_watchlist_ttl_hours: float = field(
        default_factory=lambda: _get_float("IBC_WATCHLIST_TTL_HOURS", 168.0)
    )
    """Hours before an IBC watchlist entry expires without breakout (default 168h / 7d)."""

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

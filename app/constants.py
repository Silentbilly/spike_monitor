"""Application-wide constants (not user-configurable)."""

# Bybit timeframe strings
BYBIT_TF_1H = "60"
BYBIT_TF_4H = "240"
BYBIT_TF_1D = "D"

# Minimum candles required for reliable ATR/stats
MIN_CANDLES_ATR = 15
MIN_CANDLES_STATS = 25

# Chart dimensions
CHART_FIGSIZE = (14, 8)
CHART_CANDLE_COUNT_DISPLAY = 60   # bars to show on chart

# Max concurrency for universe scan (simultaneous symbol fetches)
SCAN_CONCURRENCY = 20

# Telegram message max length
TELEGRAM_MAX_MSG_LEN = 4096

# Setup age thresholds
SETUP_STALE_HOURS = 72
SETUP_EXPIRE_HOURS = 168  # same as watchlist TTL default

# Retracement zone display color
COLOR_SPIKE = "#e74c3c"
COLOR_RETRACE = "#f39c12"
COLOR_CONSOLIDATION = "#3498db"
COLOR_BREAKDOWN = "#9b59b6"
COLOR_INVALIDATION = "#95a5a6"

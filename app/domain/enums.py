"""Domain enumerations for the spike monitoring system."""

from enum import Enum


class SetupStatus(str, Enum):
    """Status of a watchlist setup lifecycle."""
    NEW = "new"
    WATCHING = "watching"
    CONSOLIDATING = "consolidating"
    BREAKDOWN_CONFIRMED = "breakdown_confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class Timeframe(str, Enum):
    """Supported OHLCV timeframes."""
    M5 = "5"
    M15 = "15"
    H1 = "60"
    H4 = "240"
    D1 = "D"

    @property
    def minutes(self) -> int:
        mapping = {"5": 5, "15": 15, "60": 60, "240": 240, "D": 1440}
        return mapping[self.value]


class SpikeStrength(str, Enum):
    """Qualitative spike strength classification."""
    WEAK = "weak"          # 15-30%
    MODERATE = "moderate"  # 30-50%
    STRONG = "strong"      # 50-100%
    EXTREME = "extreme"    # 100%+


class NotificationType(str, Enum):
    """Telegram notification categories."""
    SPIKE_CANDIDATE = "spike_candidate"
    STRONG_SPIKE_WATCHLIST = "strong_spike_watchlist"
    WATCHLIST_UPDATE = "watchlist_update"
    BREAKDOWN_CONFIRMED = "breakdown_confirmed"
    SETUP_EXPIRED = "setup_expired"
    HEALTH = "health"
    TEST = "test"
    ERROR = "error"


class BreakdownQuality(str, Enum):
    """Quality classification of a breakdown signal."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketStatus(str, Enum):
    """Exchange instrument trading status."""
    TRADING = "Trading"
    PAUSED = "PreDelivery"
    SETTLING = "Settling"
    DELIVERING = "Delivering"
    CLOSED = "Closed"

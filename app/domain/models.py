"""Core domain models using dataclasses and Pydantic."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.enums import (
    BreakdownQuality,
    NotificationType,
    SetupStatus,
    SpikeStrength,
    Timeframe,
)


# ---------------------------------------------------------------------------
# Value objects (pure data containers, no DB mapping)
# ---------------------------------------------------------------------------

@dataclass
class OHLCV:
    """Single OHLCV candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float  # base currency volume
    quote_volume: float  # USDT volume


@dataclass
class InstrumentInfo:
    """Metadata for a tradeable instrument."""
    symbol: str
    base_coin: str
    quote_coin: str
    contract_type: str   # LinearPerpetual
    status: str
    tick_size: float
    min_qty: float
    max_leverage: float


@dataclass
class AssetSnapshot:
    """
    Current market snapshot for a symbol, enriched with computed metrics.
    Used as the primary input for spike detection.
    """
    symbol: str
    timeframe: Timeframe
    candles: list[OHLCV]
    # Most recent candle metrics
    latest_close: float
    latest_volume: float
    avg_volume_20d: float
    atr_14: float
    # Optional enrichments
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    index_price: Optional[float] = None
    mark_price: Optional[float] = None

    @property
    def rv20(self) -> float:
        """Relative volume vs 20d average."""
        if self.avg_volume_20d <= 0:
            return 0.0
        return self.latest_volume / self.avg_volume_20d


# ---------------------------------------------------------------------------
# Pydantic models (DB-serialisable, validatable)
# ---------------------------------------------------------------------------

class SpikeEvent(BaseModel):
    """A detected spike event on a daily candle."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    detected_at: datetime
    spike_candle_ts: datetime
    timeframe: Timeframe = Timeframe.D1

    # Price levels
    spike_open: float
    spike_high: float
    spike_close: float
    spike_low: float
    spike_volume: float
    avg_volume_20d: float

    # Computed metrics
    spike_pct: float          # (high - open) / open * 100
    close_pct_from_high: float  # (high - close) / high * 100  — weakness
    clv: float                # Close Location Value [-1, 1]
    rv20: float               # spike_volume / avg_volume_20d
    atr_14: float
    atr_multiple: float       # spike_pct / (atr_14 / spike_open * 100)
    retrace_pct: float        # (spike_high - current_price) / (spike_high - spike_open) * 100
    current_price: float

    # Classification
    strength: SpikeStrength
    score: float              # 0-100

    # State
    status: SetupStatus = SetupStatus.NEW
    is_strong: bool = False   # spike_pct >= STRONG_SPIKE_THRESHOLD
    chart_path: Optional[str] = None
    explanation: str = ""

    # Optional enrichments
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None

    model_config = {"use_enum_values": True}


class WatchlistItem(BaseModel):
    """
    An instrument added to the monitoring watchlist after a strong spike.
    Tracks lifecycle from spike detection → breakdown or expiry.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    spike_event_id: str
    symbol: str
    added_at: datetime
    expires_at: datetime

    spike_high: float
    spike_open: float
    spike_pct: float
    initial_score: float

    # Monitored levels (updated as price evolves)
    consolidation_low: Optional[float] = None
    consolidation_high: Optional[float] = None
    post_spike_swing_low: Optional[float] = None
    breakdown_level: Optional[float] = None
    invalidation_level: Optional[float] = None

    status: SetupStatus = SetupStatus.WATCHING
    last_checked_1h: Optional[datetime] = None
    last_checked_4h: Optional[datetime] = None
    current_score: float = 0.0
    retrace_pct: float = 0.0
    failed_bounce_detected: bool = False
    consolidation_detected: bool = False

    model_config = {"use_enum_values": True}


class ConsolidationState(BaseModel):
    """Detected consolidation phase after a spike."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    watchlist_item_id: str
    symbol: str
    detected_at: datetime
    timeframe: Timeframe

    # Range
    range_high: float
    range_low: float
    range_pct: float         # (high - low) / low * 100
    candle_count: int

    # Quality metrics
    avg_range_contraction: float   # ratio vs prior N bars
    lower_highs: bool
    flat_base: bool
    failed_bounce_level: Optional[float] = None
    quality_score: float = 0.0    # 0-100

    model_config = {"use_enum_values": True}


class BreakdownSignal(BaseModel):
    """Confirmed breakdown below consolidation or swing low."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    watchlist_item_id: str
    symbol: str
    triggered_at: datetime
    timeframe: Timeframe

    breakdown_price: float
    breakdown_level: float     # support that was broken
    breakdown_volume: float
    avg_volume: float
    volume_confirmed: bool     # breakdown_volume > avg * threshold

    score: float
    quality: BreakdownQuality
    spike_pct: float
    retrace_pct: float
    consolidation_bars: int
    chart_path: Optional[str] = None
    explanation: str = ""

    model_config = {"use_enum_values": True}


class NotificationLog(BaseModel):
    """Record of a sent Telegram notification (idempotency tracking)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    notification_type: NotificationType
    symbol: str
    reference_id: str          # SpikeEvent.id or BreakdownSignal.id
    sent_at: datetime
    telegram_message_id: Optional[int] = None
    chat_id: str = ""
    success: bool = True
    error_message: Optional[str] = None

    model_config = {"use_enum_values": True}


class HealthCheckLog(BaseModel):
    """Periodic health-check snapshot."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    checked_at: datetime
    watchlist_count: int
    active_spikes_24h: int
    errors_24h: int
    last_daily_scan: Optional[datetime] = None
    last_4h_scan: Optional[datetime] = None
    last_1h_scan: Optional[datetime] = None
    details: str = ""

    model_config = {"use_enum_values": True}

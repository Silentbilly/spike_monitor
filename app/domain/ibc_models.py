"""
IBC (Impulse → Base → Continuation) domain models.

Pure data containers — no I/O or DB access.
All timestamps are UTC-aware.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ImpulseDirection(str, Enum):
    """Direction of the detected impulse move."""

    UP = "up"
    DOWN = "down"


class IBCStatus(str, Enum):
    """Lifecycle status of an IBC watchlist entry."""

    IMPULSE_DETECTED = "impulse_detected"
    BASE_FORMING = "base_forming"
    BASE_CONFIRMED = "base_confirmed"
    BREAKOUT_CONFIRMED = "breakout_confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class ImpulseResult:
    """Output of evaluate_impulse() — not persisted, used in-memory."""

    detected: bool
    direction: ImpulseDirection
    start_index: int           # index of first impulse bar in the candle slice
    end_index: int             # index of last impulse bar
    move_pct: float            # total % move from start open to end close
    bar_count: int             # number of bars forming the impulse
    avg_impulse_volume: float  # average volume of impulse bars
    avg_20_volume: float       # 20-bar prior average volume
    rv_impulse: float          # avg_impulse_volume / avg_20_volume
    atr14: float
    atr_multiple: float        # total move in $ / atr14
    start_price: float         # open of first impulse bar
    end_price: float           # close of last impulse bar
    reason: str


@dataclass
class LevelResult:
    """Output of evaluate_level() — validated horizontal level."""

    detected: bool
    level_price: float         # representative price of the level
    touches: int               # number of extremes that confirmed the level
    cluster_high: float        # top of level corridor
    cluster_low: float         # bottom of level corridor
    age_bars: int              # bars since first touch
    reason: str


@dataclass
class IBCBreakoutResult:
    """Output of evaluate_ibc_breakout() — in-memory only."""

    triggered: bool
    direction: ImpulseDirection
    breakout_price: float
    level_price: float
    volume_confirmed: bool
    candle_volume: float
    avg_volume: float
    distance_pct: float        # how far beyond the level in %
    reason: str


# ---------------------------------------------------------------------------
# Pydantic models (DB-serialisable)
# ---------------------------------------------------------------------------


class ImpulseEvent(BaseModel):
    """A detected impulse phase (Phase 1)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    timeframe: str             # Timeframe enum value ("60" or "15")
    direction: ImpulseDirection
    detected_at: datetime

    # Price levels
    start_price: float         # open of first impulse bar
    end_price: float           # close of last impulse bar
    move_pct: float
    bar_count: int

    # Volume & ATR
    avg_impulse_volume: float
    avg_20_volume: float
    rv_impulse: float
    atr14: float
    atr_multiple: float

    model_config = {"use_enum_values": True}


class IBCWatchlistEntry(BaseModel):
    """
    Tracks a symbol through the Base → Continuation lifecycle (Phases 2–3).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    impulse_event_id: str
    symbol: str
    timeframe: str
    direction: ImpulseDirection
    added_at: datetime
    expires_at: datetime

    # Impulse reference prices
    impulse_start_price: float
    impulse_end_price: float
    impulse_move_pct: float
    impulse_rv: float
    impulse_atr_multiple: float

    # Level (populated when base is confirmed)
    level_price: Optional[float] = None
    level_touches: Optional[int] = None
    level_cluster_high: Optional[float] = None
    level_cluster_low: Optional[float] = None

    # Consolidation (populated when base is confirmed)
    base_range_pct: Optional[float] = None
    base_candle_count: Optional[int] = None
    base_avg_volume: Optional[float] = None

    # Breakout tracking
    breakout_price: Optional[float] = None
    breakout_volume_confirmed: Optional[bool] = None

    status: IBCStatus = IBCStatus.IMPULSE_DETECTED
    base_alert_sent: bool = False
    last_checked_at: Optional[datetime] = None
    last_breakout_alert_at: Optional[datetime] = None

    model_config = {"use_enum_values": True}


class IBCBreakoutEvent(BaseModel):
    """A confirmed IBC breakout (Phase 3 outcome)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    watchlist_entry_id: str
    symbol: str
    timeframe: str
    direction: ImpulseDirection
    triggered_at: datetime

    # Breakout metrics
    breakout_price: float
    level_price: float
    distance_pct: float        # how far beyond level
    volume_confirmed: bool
    breakout_volume: float
    avg_volume: float

    # Reference impulse metrics (for scoring)
    impulse_move_pct: float
    impulse_rv: float
    impulse_atr_multiple: float
    level_touches: int
    base_range_pct: float
    base_volume_decay: float   # base avg vol / impulse avg vol

    # Score
    score: float
    chart_path: Optional[str] = None
    explanation: str = ""

    model_config = {"use_enum_values": True}

"""
SQLAlchemy ORM table definitions.

Uses SQLAlchemy 2.0 mapped_column / DeclarativeBase.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SpikeEventRow(Base):
    __tablename__ = "spike_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    spike_candle_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="D")

    spike_open: Mapped[float] = mapped_column(Float, nullable=False)
    spike_high: Mapped[float] = mapped_column(Float, nullable=False)
    spike_close: Mapped[float] = mapped_column(Float, nullable=False)
    spike_low: Mapped[float] = mapped_column(Float, nullable=False)
    spike_volume: Mapped[float] = mapped_column(Float, nullable=False)
    avg_volume_20d: Mapped[float] = mapped_column(Float, nullable=False)

    spike_pct: Mapped[float] = mapped_column(Float, nullable=False)
    close_pct_from_high: Mapped[float] = mapped_column(Float, nullable=False)
    clv: Mapped[float] = mapped_column(Float, nullable=False)
    rv20: Mapped[float] = mapped_column(Float, nullable=False)
    atr_14: Mapped[float] = mapped_column(Float, nullable=False)
    atr_multiple: Mapped[float] = mapped_column(Float, nullable=False)
    retrace_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)

    strength: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    is_strong: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    chart_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    funding_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_interest: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    spike_event_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    spike_high: Mapped[float] = mapped_column(Float, nullable=False)
    spike_open: Mapped[float] = mapped_column(Float, nullable=False)
    spike_pct: Mapped[float] = mapped_column(Float, nullable=False)
    initial_score: Mapped[float] = mapped_column(Float, nullable=False)

    consolidation_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    consolidation_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_spike_swing_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    breakdown_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="watching")
    last_checked_1h: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_checked_4h: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    current_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    retrace_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    failed_bounce_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consolidation_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BreakdownSignalRow(Base):
    __tablename__ = "breakdown_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_item_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)

    breakdown_price: Mapped[float] = mapped_column(Float, nullable=False)
    breakdown_level: Mapped[float] = mapped_column(Float, nullable=False)
    breakdown_volume: Mapped[float] = mapped_column(Float, nullable=False)
    avg_volume: Mapped[float] = mapped_column(Float, nullable=False)
    volume_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    spike_pct: Mapped[float] = mapped_column(Float, nullable=False)
    retrace_pct: Mapped[float] = mapped_column(Float, nullable=False)
    consolidation_bars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chart_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")


class NotificationLogRow(Base):
    __tablename__ = "notification_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    notification_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class HealthCheckLogRow(Base):
    __tablename__ = "health_check_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    watchlist_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_spikes_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_daily_scan: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_4h_scan: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_1h_scan: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")

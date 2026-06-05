# IBC Module — Impulse → Base → Continuation

The IBC module detects three-phase price patterns on Bybit USDT perpetuals and fires two Telegram alerts per valid setup: **Base Formed** (Phase 2) and **Breakout** (Phase 3).

---

## Pattern Overview

```
  Phase 1 — Impulse
  ┌─────────────────────────────────────────────────────────┐
  │  ≥15% directional move in ≤5 bars                       │
  │  Volume ≥ 1.5× 20-bar average                           │
  │  Move ≥ 3× ATR14                                        │
  │  Both UP and DOWN directions detected                    │
  │  Timeframes: 1H and 15M                                  │
  └─────────────────────────────────────────────────────────┘
            ↓
  Phase 2 — Base Monitoring
  ┌─────────────────────────────────────────────────────────┐
  │  Horizontal level forms: ≥2 extremes within ±1% cluster │
  │  Consolidation: range ≤ 10%, volume decay ≤ 0.6×        │
  │  On confirmation → 🟡 "Base Formed" Telegram alert       │
  └─────────────────────────────────────────────────────────┘
            ↓
  Phase 3 — Breakout Detection
  ┌─────────────────────────────────────────────────────────┐
  │  UP:   close > level × (1 + 0.3%)                       │
  │  DOWN: close < level × (1 − 0.3%)                       │
  │  Volume ≥ avg × 1.3                                      │
  │  On trigger → 🔴🟢 "Breakout" Telegram alert + score     │
  └─────────────────────────────────────────────────────────┘
```

---

## Telegram Alerts

| Trigger         | Emoji | Content |
|----------------|-------|---------|
| Base Formed    | 🟡    | Symbol, TF, direction, impulse %, level price, touches, consolidation range %, chart PNG |
| Breakout       | 🟢🔴  | Symbol, TF, direction, score 0-100, breakout price, distance from level, vol confirmation, chart PNG |

Charts are annotated with:
- **Base Formed**: impulse stats overlay, shaded base zone, horizontal level line
- **Breakout**: base zone, level line, breakout candle arrow marker, score stats

---

## Scoring Model (0–100)

| Component | Max pts | Notes |
|-----------|---------|-------|
| Impulse magnitude | 20 | 15%=5, 25%=10, 50%=20 |
| Impulse volume expansion | 10 | rv ≥ 2× → 5, rv ≥ 3× → 10 |
| Impulse ATR multiple | 10 | ≥ 3× → 5, ≥ 5× → 10 |
| Level touches count | 15 | 2→5, 3→10, 4+→15 |
| Consolidation tightness | 15 | ≤ 3% → 15, ≤ 6% → 8, ≤ 10% → 3 |
| Volume decay in base | 10 | ≤ 0.4× → 10, ≤ 0.6× → 7, ≤ 0.8× → 4 |
| Breakout conviction (vol + candle) | 15 | vol confirmed = 10, distance ≥ 1% = 5 |
| Breakout distance from level | 5 | ≥ 1% → 5, ≥ 0.5% → 3 |
| **Total raw** | **100** | |

**Penalties (applied after summing):**

| Condition | Penalty |
|-----------|---------|
| Weak volume (breakout not vol-confirmed) | −10 |
| Wide base (range > IBC_BASE_MAX_RANGE_PCT) | −10 |
| Stale level (age > 80% of TTL) | −5 |

---

## New Files

```
app/domain/ibc_models.py                 — ImpulseEvent, IBCWatchlistEntry,
                                           IBCBreakoutEvent dataclasses + enums
app/domain/ibc_rules.py                  — evaluate_impulse(), evaluate_level(),
                                           evaluate_ibc_breakout(), score_ibc()
app/services/impulse_detector_service.py — Phase 1: full-universe scan
app/services/ibc_monitor_service.py      — Phase 2: base/level monitoring + alert
app/services/ibc_breakout_service.py     — Phase 3: breakout detection + alert
app/storage/ibc_repositories.py          — ImpulseEventRepository,
                                           IBCWatchlistRepository,
                                           IBCBreakoutRepository
tests/test_ibc_rules.py                  — ≥15 unit tests for rule functions
tests/test_ibc_scoring.py                — scoring edge cases
```

## Modified Files

```
app/config.py        — IBC_* keys appended (no existing keys changed)
app/storage/schema.py — ImpulseEventRow, IBCWatchlistRow, IBCBreakoutEventRow added
app/scheduler.py     — register_ibc_jobs() added (register_jobs() unchanged)
app/main.py          — _get_ibc_repos(), run_ibc_* methods, register_ibc_jobs() call
app/cli.py           — ibc-scan, list-ibc-watchlist, list-ibc-breakouts commands added
.env.example         — IBC_* section appended
```

---

## Configuration Reference

All keys prefixed `IBC_` in `.env`:

| Key | Default | Description |
|-----|---------|-------------|
| `IBC_IMPULSE_MIN_PCT` | `15.0` | Minimum % move for impulse |
| `IBC_IMPULSE_MAX_BARS` | `5` | Max consecutive bars for impulse window |
| `IBC_IMPULSE_RV_MIN` | `1.5` | Min relative volume (impulse / 20-bar avg) |
| `IBC_IMPULSE_ATR_MIN` | `3.0` | Min ATR multiple for impulse move |
| `IBC_IMPULSE_SCAN_CRON` | `0 */4 * * *` | Phase 1 scan schedule (UTC cron) |
| `IBC_LEVEL_CLUSTER_PCT` | `1.0` | ±% corridor for level clustering |
| `IBC_LEVEL_MIN_TOUCHES` | `2` | Min extremes for a valid level |
| `IBC_LEVEL_MAX_AGE_BARS` | `30` | Max bars to look back for level |
| `IBC_BASE_MAX_RANGE_PCT` | `10.0` | Max consolidation range % |
| `IBC_BASE_VOLUME_DECAY` | `0.6` | Max base avg vol / recent avg vol |
| `IBC_MONITOR_CRON` | `*/30 * * * *` | Phase 2 monitor schedule |
| `IBC_BREAKOUT_CONFIRM_PCT` | `0.3` | Min % beyond level for breakout |
| `IBC_BREAKOUT_VOL_MULT` | `1.3` | Min volume multiple for breakout |
| `IBC_BREAKOUT_CRON` | `*/15 * * * *` | Phase 3 breakout schedule |
| `IBC_COOLDOWN_H` | `24.0` | Hours between breakout alerts for same setup |
| `IBC_WATCHLIST_TTL_HOURS` | `168.0` | Hours before IBC entry expires (7 days) |

---

## CLI Commands

```bash
# Run Phase 1 impulse scan once (useful for testing)
python -m app.cli ibc-scan

# List IBC watchlist entries (with optional status filter)
python -m app.cli list-ibc-watchlist --status base_confirmed

# List recent IBC breakout events
python -m app.cli list-ibc-breakouts --hours 72
```

---

## DB Tables

Three new tables are created automatically by SQLAlchemy on startup:

| Table | Purpose |
|-------|---------|
| `ibc_impulse_events` | Phase 1 detected impulse events |
| `ibc_watchlist` | Active IBC watchlist entries (Phases 2–3 lifecycle) |
| `ibc_breakout_events` | Confirmed Phase 3 breakout events |

No existing tables are modified.

---

## Running Tests

```bash
pytest tests/test_ibc_rules.py tests/test_ibc_scoring.py -v
```

All IBC tests are synchronous and require no database or network access.

# SpikeMonitor

Production-ready Python service for monitoring Bybit USDT perpetual futures for
**post-spike short setups** — detecting unsustainable parabolic pumps that fail
to hold highs, retrace deeply, and form a weak structure before breaking down.

> ⚠️ **This is a signal alerting engine, not an auto-trading bot.**
> All signals are informational. Never trade based on automated signals alone.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       APScheduler                           │
│  daily_scan │ watchlist_4h │ watchlist_1h │ health_ping     │
└──────┬──────┴──────┬───────┴──────┬───────┴────────────────┘
       │             │              │
       ▼             ▼              ▼
┌──────────────────────────────────────────────────────────────┐
│              Application (app/main.py)                       │
│                                                              │
│  UniverseService  →  SpikeDetectorService  →  WatchlistSvc  │
│                        ConsolidationSvc                      │
│                        BreakdownService                      │
│                        ChartService                          │
│                        NotificationService  →  Telegram      │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────┐    ┌────────────────────────────────────────┐
│  BybitAdapter  │    │         SQLite (SQLAlchemy)            │
│  (httpx async) │    │  spike_events / watchlist_items        │
│  V5 public API │    │  breakdown_signals / notif_logs        │
└────────────────┘    └────────────────────────────────────────┘
```

### Module Map

| Path | Responsibility |
|---|---|
| `app/domain/models.py` | Pydantic domain models (SpikeEvent, WatchlistItem, …) |
| `app/domain/enums.py` | Status, timeframe, and quality enumerations |
| `app/domain/indicators.py` | Pure-function indicator math (ATR, CLV, RV20, …) |
| `app/domain/rules.py` | Spike / retracement / consolidation / breakdown rules |
| `app/domain/scoring.py` | 0–100 score model with full breakdown |
| `app/exchanges/base.py` | Abstract exchange adapter interface |
| `app/exchanges/bybit.py` | Bybit V5 REST implementation |
| `app/services/` | Orchestration services (one concern each) |
| `app/storage/` | SQLAlchemy schema + repository layer |
| `app/scheduler.py` | APScheduler job registration |
| `app/main.py` | Dependency wiring + daemon entry point |
| `app/cli.py` | Typer CLI for one-off commands |
| `app/config.py` | All config from `.env` |
| `tests/` | pytest unit tests |

---

## Setup

### 1. Clone & Configure

```bash
git clone <repo>
cd spike_monitor
cp .env.example .env
# Edit .env — fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

### 2a. Run with Docker (recommended)

```bash
docker compose build
docker compose up -d
docker compose logs -f spike_monitor
```

### 2b. Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
mkdir -p data/charts
python -m app.main
```

---

## CLI Commands

```bash
# Run full scan once (universe + daily spikes + watchlist add)
python -m app.cli full-scan

# Run 4H watchlist scan once
python -m app.cli watchlist-scan-4h

# Run 1H watchlist scan once
python -m app.cli watchlist-scan-1h

# Send a test Telegram message
python -m app.cli send-test-telegram

# Print health status
python -m app.cli healthcheck

# Show active watchlist
python -m app.cli list-watchlist

# Remove expired entries from DB
python -m app.cli cleanup-expired
```

With Docker:
```bash
docker compose run --rm spike_monitor python -m app.cli list-watchlist
```

---

## Tests

```bash
pip install -r requirements.txt
pytest -v
```

Coverage areas: ATR, CLV, RV20 calculations · spike detection rules ·
retracement evaluation · consolidation detection · failed bounce logic ·
breakdown confirmation · score model · watchlist expiry · notification dedup.

---

## Scoring Model (0–100)

| Component | Max pts | Logic |
|---|---|---|
| Spike magnitude (spike_pct) | 15 | 30%=5, 50%=10, 80%=15 |
| Strong spike bonus (≥50%) | 5 | Flat bonus |
| Retracement depth | 15 | 70%=8, 80%=12, 90%=15 |
| CLV weakness | 10 | CLV=-1→10, CLV=0→5, CLV=+1→0 |
| Relative volume (rv20) | 10 | rv≥2→5, rv≥3→10 |
| ATR-normalised expansion | 10 | ×3→5, ×5→10 |
| Consolidation quality | 10 | quality_score/10 |
| Failed bounce detected | 10 | +10 flat |
| Breakdown quality | 10 | LOW=3, MED=6, HIGH=10 |
| Volume confirmed on breakdown | 5 | +5 flat |
| **Total** | **100** | |

**Penalties:**
| Penalty | Points |
|---|---|
| V-shape recovery (fast reclaim of spike) | -10 |
| Low liquidity instrument | -10 |
| Time decay (72h → 168h linear) | up to -20 |
| Thin history (< 40 candles) | -5 |

---

## Strategy — Signal Logic

### A. Spike Definition
- `spike_pct = (high - open) / open × 100`
- Minimum: **30%** (configurable via `SPIKE_THRESHOLD_PCT`)
- Strong: **50%+** (added to watchlist)
- Required wick ratio `(high - close) / (high - low) ≥ 0.40` — inability to hold highs
- Optional: volume expansion `rv20 ≥ 1.5`

### B. CLV (Close Location Value)
`(2×close − high − low) / (high − low)` in range **[−1, +1]**.
- −1 = closed at the low (strongest bearish signal)
- +1 = closed at the high (weakest signal, penalised)

### C. Retracement Quality
`retrace_pct = (spike_high − current_price) / (spike_high − spike_open) × 100`
- Qualifies at **≥70%**, strong at **≥80%**, deep at **≥90%**

### D. Relative Volume (rv20)
`rv20 = spike_volume / avg_volume_20d`
- ≥2 = elevated, ≥3 = extreme anomaly

### E. ATR Normalisation
`atr_multiple = spike_pct / (ATR_14 / spike_open × 100)`
- Distinguishes genuinely abnormal moves from everyday high-vol instruments

### F. Liquidity Filters
- Minimum 30 days of daily history
- Minimum 500k USDT avg daily volume
- Blacklist / whitelist support
- Signal cooldown: 24h per symbol

### G. Consolidation
After spike + retrace: price must spend 3–20 bars in a range with:
- Range ≤ 8% of price
- Range contraction ratio ≤ 0.85 vs prior window
- Lower highs pattern (optional, boosts quality)

### H. Failed Bounce
Price attempts a recovery to the 50% impulse level but fails to close above it,
with the following close lower. This directly adds 10 pts to score.

### I. Breakdown Trigger
- Close ≥ 0.3% below consolidation low (prevents wick false signals)
- **Debounce**: 2 consecutive closes required below support
- Volume confirmation bonus if volume ≥ 1.3× average
- Quality: HIGH (close + vol + bearish candle), MEDIUM (2/3), LOW (close only)

### J. Time Decay
- Score unchanged for first 72 hours
- Linear decay from 72h to 168h (−20 pts max)
- Item expires and is notified after `WATCHLIST_TTL_HOURS` (default 168h / 7 days)

---

## Telegram Message Examples

### Spike Candidate
```
🔴 SPIKE CANDIDATE: XYZUSDT
━━━━━━━━━━━━━━━━━━━━━
📊 Score: ███████░░░ 68
📈 Spike: +52.3%  [STRONG]
📉 Retrace: -78.0% of impulse
🕯 CLV: -0.71 (−1=at low)
📦 RV20: 3.4x
⚡ ATR×: 4.8x
💲 Spike High: 0.8450
💲 Spike Open: 0.5540
💲 Current: 0.5920
⏱ TF context: 1D
💸 Funding: 0.041%

📝 XYZUSDT spiked 52.3% intraday but failed to hold highs.
   CLV=-0.71 — closed near lows. Volume 3.4× average.
   Already retraced 78% of impulse. Spike = 4.8× ATR.

🕐 2025-01-15 02:00 UTC
```

### Breakdown Confirmed
```
🚨 BREAKDOWN CONFIRMED: XYZUSDT
━━━━━━━━━━━━━━━━━━━━━
📊 Score: ████████░░ 81
🔴 Quality: HIGH
💲 Break Price: 0.5630
💲 Support: 0.5700
📈 Orig Spike: +52.3%
📉 Retrace: -81.0%
📦 Vol confirmed: YES ✅
⏱ Timeframe: 240

📝 Broke below 0.5700 on 4H. Breakdown accompanied by above-avg vol.
   Prior consolidation detected. Failed bounce previously observed.

🕐 2025-01-17 08:05 UTC
```

### Watchlist Added
```
👀 WATCHLIST ADDED: XYZUSDT
━━━━━━━━━━━━━━━━━━━━━
Spike: +52.3%  Score: 68
Monitoring: 4h + 1h for consolidation & breakdown
⛔ Invalidation above: 0.8492
📅 Expires: 2025-01-22
🕐 2025-01-15 02:00 UTC
```

---

## Scheduler Jobs

| Job | Cron (UTC default) | Description |
|---|---|---|
| `daily_spike_scan` | `0 1 * * *` | Universe scan + 1D spike detection |
| `watchlist_4h` | `0 */4 * * *` | Consolidation + breakdown on 4H |
| `watchlist_1h` | `5 * * * *` | Breakdown check on 1H |
| `health_ping` | `0 */6 * * *` | System health Telegram ping |

All cron expressions are configurable via `.env`.

---

## Adding a New Exchange

1. Subclass `app/exchanges/base.py:ExchangeAdapter`
2. Implement `get_instruments()`, `get_klines()`, `get_funding_rate()`
3. Optionally override `get_open_interest()`, `get_mark_price()`, `get_index_price()`
4. Swap the adapter in `app/main.py`

---

## Strategy Improvements — 10 Ideas to Reduce False Positives

1. **Multi-timeframe confirmation gate** — require 4H structure to confirm a 1D spike before adding to watchlist. Reduces listing-pump false signals dramatically.

2. **BTC correlation filter** — if BTC is also dropping sharply on the spike day, the setup may be market-wide beta, not a genuine symbol-specific pump. Discount or skip these.

3. **News/listing event blackout** — integrate a basic event calendar or CoinGecko listing API. Discard signals within 48h of a new listing or major announcement.

4. **Open interest divergence** — if OI dropped on the spike (short squeeze), it confirms the thesis. If OI expanded (new longs), the move may be trend initiation, not a fake pump.

5. **Funding rate extreme threshold** — require funding > +0.05% on the spike candle as evidence of crowded longs being squeezed. Low funding spikes are less reliable shorts.

6. **Volume profile gap analysis** — if the spike moved through a high-volume node, more buyers are trapped. Use a simplified point-of-control approximation from OHLCV.

7. **Minimum consolidation duration** — require at least 5 bars on 4H (20h) of consolidation before triggering breakdown signal. Removes very fast V-shape structures.

8. **Sector/correlation clustering** — if 5+ altcoins spike simultaneously, this is likely a market-wide event (e.g. ETH ecosystem rally). Reduce score for correlated group spikes.

9. **Intraday lower high confirmation on 1H** — require at least 2 consecutive lower highs on 1H before allowing breakdown to qualify. Filters out single-bar false breaks.

10. **Walk-forward score calibration** — periodically backtest the scoring weights against historical outcomes (signal issued → price 72h later). Use that feedback to adjust component weights. The current weights are well-reasoned starting points but will drift over time.

---

## Risk Disclaimer

- **Not financial advice.** This tool is for research and informational purposes only.
- **News-driven pumps** (exchange listings, protocol upgrades, hacks) often produce the same signal pattern but are fundamentally different — price can sustain a new baseline.
- **Short squeezes** do not always fully retrace. A 50% spike that only retraces 50% can still trend higher.
- **Low-liquidity instruments** have highly manipulable patterns — the liquidity filters exist for this reason but are not foolproof.
- **Leverage amplifies losses.** Any use of this signal system with leveraged positions must be paired with strict risk management.
- The authors assume no responsibility for trading losses resulting from use of this software.

---

## License

MIT

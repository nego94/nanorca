# NANORCA — Memory & Progression Document
> **Purpose:** Single source of truth for project state. Update this file after every significant change.
> **Owner:** Nego (abetnego.kristiawan@gmail.com)
> **Last updated:** 2026-05-15

---

## 1. What Is This Project

NANORCA is an autonomous crypto trading bot built with:
- **Python AI brain** — Claude Haiku or claude sonet when needed makes trading decisions
- **Go executor** — fast exchange I/O, WebSocket feeds, order execution
- **PostgreSQL + TimescaleDB** — trade history, signal weights, learning data
- **Prometheus + Grafana** — real-time dashboard at `https://nanorca.creativorium.com`
- **Telegram bot** — trade alerts + command interface

**Architecture:** Python ↔ gRPC ↔ Go ↔ Binance/Hyperliquid/Polymarket

---
> **Last updated:** 2026-05-15

---

## 2. Current Phase

### Phase: 1 — Paper Trading (VPS)
**Status:** Running 24/7 on Hostinger KVM2 VPS (72.62.124.23)

| Item | State |
|---|---|
| Paper trading mode | ✅ ACTIVE (`PAPER_TRADING=true`) |
| Real Binance futures wallet funded | ✅ $11.39 USDT-M |
| Data collection | ✅ 24/7 on VPS (Hostinger KVM2, 72.62.124.23) |
| VPS deployment | ✅ LIVE since 2026-05-14 03:02 UTC |
| Win rate baseline | ⏳ Accumulating — check back in 7-14 days |
| First weekly learning | ⏳ Sunday 2026-05-18 00:00 UTC |
| Grafana dashboard | ✅ https://nanorca.creativorium.com (working as of 2026-05-14) |
| Live trading | 🔒 LOCKED until 14-day paper win rate ≥ 60% |

**What the bot does right now:**
1. Scans top-25 Binance USDT futures pairs by volume every 30 seconds
2. Pre-filter checks if any signal crosses threshold before calling Claude
3. If market is quiet → skips Claude call (saves API cost)
4. If signals fire → calls Claude Haiku → gets trading decision
5. If confidence ≥ 55 → sizes position by confidence tier → paper executes
6. All paper trades logged to PostgreSQL

---

## 3. Capital Reality

| Item | Value |
|---|---|
| Binance spot USDT | $0.00 (transferred to futures) |
| Binance USDT-M Futures | $11.39 |
| Locked coins (other) | ~$0.64 |
| Portfolio total | ~$12.04 |
| Bot tradeable capital | **$11.39** |
| Simulated paper capital (bot tracker) | **~$12.91** (after 2026-05-14 session) |

**Paper trades completed:**
- OSMO LONG → WIN +$0.33 | KITE LONG ×3 → WIN +$0.18/+$0.24/+$0.26 (early session)
- SOL LONG (129 min hold) → WIN +$0.22
- ZEC LONG (21 min hold) → WIN +$0.20
- DOGE LONG → open (monitoring)
- SAGA LONG ×7 — all WIN (fast momentum, 1–7 min holds, +$0.13 to +$0.43 each)
- SAGA LONG ×1 — LOSS -$0.50 (stop hit after momentum reversed)
- Capital peak reached: **$13.42**, pulled back to **$12.91** after SAGA stop loss
- DB bug found during session: trades stuck as "open" in DB despite capital updating correctly (fixed 2026-05-15)

**Capital bucket system:**
- **Tradeable** = USDT-M futures wallet → bot sizes trades from this
- **Locked** = other coins in spot → bot tracks for display, never trades
- **In-flight** = USDT tied up in open positions right now

**Minimum viable capital for Phase 2 (live):** $300–500

---

## 4. Architecture — File Map

```
nanorca/
├── bot/                          Python AI brain
│   ├── main.py                   Entry point, main trading loop
│   ├── config.py                 All env vars, typed, validated at startup
│   ├── scheduler.py              APScheduler: cycle(60s), daily report, weekly learning
│   ├── brain/
│   │   ├── claude_brain.py       Anthropic API calls, prompt assembly, JSON parse
│   │   ├── signal_builder.py     Transforms market snapshots → signal dict
│   │   └── confidence_scorer.py  Signal weighting helper
│   ├── risk/
│   │   ├── risk_manager.py       Position sizing, leverage caps, MAX 3 open positions
│   │   ├── capital_tracker.py    P&L tracking, floor check, daily loss cap
│   │   ├── circuit_breaker.py    Bot state machine (running/paused/stopped)
│   │   └── trading_plan.py       4 modes: nanorca_decide, conservative, aggressive, hybrid
│   ├── execution/
│   │   └── order_router.py       Python→Go gRPC bridge (scan, place, close, balances)
│   ├── data/
│   │   └── db.py                 asyncpg pool, all SQL queries (parameterized)
│   ├── alerts/
│   │   ├── telegram_bot.py       Commands + trade broadcast notifications
│   │   ├── daily_report.py       Midnight summary
│   │   └── callmebot.py          WhatsApp fallback for critical alerts
│   ├── learning/
│   │   ├── weekly_learner.py     Sunday 00:00 UTC — reweights signals from trade data
│   │   ├── outcome_logger.py     Logs trade open/close to DB
│   │   └── signal_weights.py     Default weights: momentum=0.20, volume=0.05...
│   ├── monitoring/
│   │   └── prometheus_exporter.py Metrics server port 8080
│   └── proto/                    Generated gRPC stubs (nanorca_pb2*.py)
│
├── executor/                     Go hot-path executor
│   ├── cmd/server/main.go        Entry point, starts gRPC + WebSocket feeds
│   ├── pkg/grpcserver/server.go  RPC handlers (ScanMarkets, PlaceOrder, etc.)
│   └── internal/
│       ├── exchanges/
│       │   ├── binance.go        Spot REST + Futures REST + WebSocket + balance
│       │   ├── hyperliquid.go    Funding rates, perp markets
│       │   ├── polymarket.go     CLOB price gap signals
│       │   └── common.go         HTTP helpers, JSON utils
│       ├── scanner/
│       │   └── market_scanner.go Top-N Binance pairs by volume (10-min cache)
│       ├── feed/
│       │   └── ws_feed.go        Live Binance book ticker WebSocket + PriceCache
│       └── executor/
│           └── order_executor.go Routes PlaceOrder/CloseOrder to exchange
│
├── migrations/
│   └── 001_initial_schema.sql    All DB tables (trades, signals, capital_snapshots, etc.)
├── grafana/
│   ├── dashboards/nanorca.json   Full Grafana dashboard (Prometheus + PostgreSQL)
│   └── provisioning/             Auto-provisioned datasources
├── prometheus/
│   └── prometheus.yml            Scrape config
├── docker-compose.yml            5 services: bot, executor, postgres, prometheus, grafana
├── .env                          ALL secrets — never commit to git
├── .env.example                  Template (safe to commit)
├── ruflo.yml                     Multi-agent plan (Phase 6, not active yet)
└── NANORCA_MEMORY.md             This file
```

---

## 5. Trading Logic — Decision Pipeline

Every 30 seconds:
```
1. Bot state check          → skip if paused/stopped
2. Capital floor check      → emergency stop if < 25% of starting capital
3. Daily loss check         → pause if daily loss > 8%
4. Market scan              → Go executor fetches top-25 Binance USDT pairs every 30s (incl. BTC/ETH for direction)
                               WebSocket feeds live bid/ask data between cycles
5. Auto-close positions     → stop-loss (-2%) or max hold (4h) trigger
6. Build signals            → momentum, volume spike, funding rate, price gap
7. Pre-filter               → skip Claude if: momentum < 0.30% AND volume < 1.20x AND funding < 0.01%
8. MIN_GROSS_MOVE check     → skip if momentum < 0.09% (can't cover 0.04% round-trip fee)
9. Call Claude Haiku        → get: action, market, direction, size_pct, confidence, reasoning,
                               target_profit_pct, stop_loss_pct, spot_suggestion
                               Claude reads BTC/ETH as market direction, trades only altcoins
10. Spot suggestion check   → if active + conf ≥ 65 → send Telegram (NOT executed)
11. Confidence gate:
    < 50  → hard skip, nothing logged
    50-64 → add to SuggestionStore → surfaced via /markets (BTC/ETH filtered out)
    65+   → proceed to trade
12. Open position count     → skip if 3 positions already open (MAX_OPEN_POSITIONS=3)
13. Risk manager approval   → graduated sizing + leverage cap + exposure check
14. Execute (paper or live) → Go executor PlaceOrder
15. Broadcast Telegram      → formatted FUTURES: LONG/SHORT open notification
16. Log to DB               → trades table, outcome_logger
```

---

## 6. Confidence → Action (Updated)

| Confidence | Action | Size |
|---|---|---|
| < 50 | Hard skip — nothing logged | 0% |
| 50–64 | **Suggestion only** — surfaced via /markets, NOT traded | 0% |
| 65–79 | Trade — normal | 3% of capital |
| 80–89 | Trade — full | 5% of capital |
| 90+ | Trade — max + high_conviction flag | 5% of capital |

**Why 50–64 is now suggestions instead of 1% trades:**
The human checks `/markets` or `/readmarkets` and can manually act on these.
The bot does not risk capital on low-confidence signals. Learning data
comes from the 65+ trades only.

---

## 7. Fee Model (Why Futures Only)

| Trade type | Fee | Round-trip | Min gross move needed |
|---|---|---|---|
| Spot | 0.10%/side | 0.20% | 0.25%+ (not viable for 30-min holds) |
| Futures maker (GTX) | 0.02%/side | 0.04% | **0.09%** ✅ |

Bot ALWAYS uses futures limit orders with `timeInForce=GTX` (Post-Only).
Never market orders. Never spot for short-term holds.

---

## 8. Telegram Commands

| Command | Access | What it does |
|---|---|---|
| `/status` | All | Bot state, capital, mode, open positions |
| `/capital` | All | Tradeable USDT, locked coins, portfolio breakdown |
| `/positions` | All | Current open positions with entry/size |
| `/markets` | All | Market suggestions (50–64 conf) + live top prices |
| `/readmarkets` | All | Alias for `/markets` — same output |
| `/suggestion TOKEN` | All | 2-pass (ruflo) on-demand analysis: MarketAnalyst + RiskAuditor. Returns direction/confidence/entry/target/stop/verdict. Advisory only — never executed. |
| `/suggest TOKEN` | All | Alias for `/suggestion` |
| `/check TOKEN` | All | Add coin to extra scan list (persists until bot restart) |
| `/listpriority` | All | Show extra scan list |
| `/removepriority TOKEN` | All | Remove coin from extra scan list |
| `/report` | All | **All-time** P&L + win rate (default). `/report 24h`, `/report 7d`, `/report 30d` for filtered views. Shows paper/live split, avg win/loss, fees. |
| `/history [N]` | All | Last **20** trades (default, max 100). Shows paper/live emoji per trade. `/history 50` for more. |
| `/learning` | All | Last weekly learning report |
| `/help` | All | Command list |
| `/pause` | Owner | Pause trading (keeps positions open) |
| `/resume` | Owner | Resume trading |
| `/setfloor N` | Owner | Change capital floor % |
| `/setmode` | Owner | Change trading plan mode |
| `/stop` | Owner | Emergency stop exchange |

---

## 9. Telegram Broadcast Format

### Trade opened (futures):
```
📊 [📄 PAPER] FUTURES: LONG - SOLUSDT
─────────────────────
📍 Open @$149.82
🎯 Target: +0.5% → $150.57
🛑 Stop: -2.0% → $146.82
💰 Size: $0.34 (3.0% of capital)
🧠 Confidence: 72/100
⏱ Expected hold: 90 min
📋 SOL momentum +0.47% over 8 min, volume 1.3x baseline
📈 Positions open: 1/3
```

### Trade closed:
```
✅ WIN [📄 PAPER] FUTURES: LONG - SOLUSDT CLOSED
─────────────────────
📍 Entry: $149.82 → Exit: $150.65
💰 P&L: +$0.28 (+0.55%)
⏱ Hold: 87 min
🔖 Closed by: stop-loss / max-hold (240m)
💳 Fees: $0.0001
```

### Spot suggestion (manual only):
```
💡 SPOT SUGGESTION [📄 PAPER]
─────────────────────
SPOT: LONG - SOLUSDT
🗓 Hold: 3-4 weeks
🎯 Target date: 2026-06-10
🧠 Confidence: 71/100
📋 SOL breaking key resistance, volume accumulation pattern
⚠️ Manual action only — bot does NOT execute spot trades
```

---

## 10. Active Exchanges & Market Roles

| Exchange | Trading | Intelligence | Phase |
|---|---|---|---|
| Binance USDT-M Futures | ✅ Active | ✅ Market scanner | Phase 2+ |
| Hyperliquid | ❌ Disabled | ✅ Funding rate signal | Phase 3 |
| Polymarket | ❌ Disabled | ✅ Price gap signal | Phase 3 |

### BTC and ETH — Analysis Only, Never Traded

**Critical distinction (user confirmed 2026-05-14):**

| Role | BTC | ETH | Altcoins (SOL/BNB/INJ etc.) |
|---|---|---|---|
| Scanned for price/volume | ✅ Yes | ✅ Yes | ✅ Yes |
| Used as market direction signal | ✅ Yes — primary | ✅ Yes — secondary | Used as trade targets |
| In PRIORITY_MARKETS | ✅ Yes | ✅ Yes | ✅ Yes |
| Suggested for trading | ❌ Never | ❌ Never | ✅ These are traded |
| In suggestion store (50-64 conf) | ❌ Filtered out | ❌ Filtered out | ✅ Shown |

**Why BTC/ETH are kept in analysis:**
- BTC momentum tells Claude the overall crypto market direction
- If BTC +1% → market bullish → increases confidence in altcoin LONG signals
- If BTC -2% → market risk-off → reduces confidence in any LONG, increases SHORT bias
- ETH momentum signals DeFi and Layer-1 sentiment specifically

**Why BTC/ETH are excluded from trading:**
- BTCUSDT min futures lot = 0.001 BTC ≈ $100+ (too large for $11 capital)
- ETHUSDT: lower % daily moves than altcoins → less profit per unit of risk at small capital
- Rule: when capital > $500, reassess whether to add ETH trading

---

## 11. Environment Variables (Key Ones)

```bash
PAPER_TRADING=true              # NEVER set false without 14-day paper proof
SCAN_INTERVAL_SECONDS=30        # Changed from 60; Phase 2 lean: revert to 90-120 if API cost climbs
ENABLED_EXCHANGES=binance       # Add hyperliquid,polymarket in Phase 3
BINANCE_SCAN_TOP_N=20           # Top 20 USDT pairs by 24h volume
PRIORITY_MARKETS=ETH,SOL,BNB,DOGE,ADA,AVAX,INJ,LINK,DOT,OP  # No BTC (min lot too big)
TRADING_MODE=hybrid             # aggressive for <$1k, conservative for >$10k
CONFIDENCE_THRESHOLD=65         # Used in Claude prompt context
MIN_GROSS_MOVE_PCT=0.09         # Fee break-even gate
MAX_POSITION_PCT=20             # % of capital per trade (high for $12 testing)
CAPITAL_FLOOR_PCT=25            # Emergency stop level
```

---

## 11b. Future Feature: Dynamic Priority Markets + /check TOKEN

### What was requested (not yet implemented)
The user wants:
1. Priority markets to be **dynamic** — auto-updated based on trending/high-volume coins from each scan cycle, not the static `.env` list
2. `/check TOKEN/USDT` — manually add a coin to priority list for deeper scanning
3. **Max 15 priority slots** — when full and user adds a new one, bot asks which to remove
4. **History** — remember which coins were removed and when, with reason

### Implementation plan (Phase 4 or later)
```
/check INJUSDT
→ Bot: "INJUSDT added to priority scan. Priority list now 8/15:
        [ETH, SOL, BNB, DOGE, ADA, AVAX, LINK, INJ]"

/check APTUSDT  (when at 15 limit)
→ Bot: "Priority list is full (15/15). Which would you remove?
        Least active in last 7 days: DOTUSDT (0 signals, last scanned 3d ago)
        Type /removepriority DOTUSDT to confirm."

/removepriority DOTUSDT
→ Bot: "DOTUSDT removed. Reason: replaced by APTUSDT on 2026-05-20.
        APTUSDT added. Priority: 15/15."
```

### Dynamic auto-update (Phase 5+)
Every Sunday during weekly learning:
- Scanner ranks all 25 top markets by signal quality (win rate × volume × momentum variance)
- Top 10 auto-replace lowest-performing 10 priority slots
- User gets Telegram report: "Priority updated: removed [X, Y] added [A, B]"

### Storage needed
- `priority_markets` table in PostgreSQL: symbol, added_at, added_by (user/auto), removed_at, removed_reason
- Currently: static list in `.env` — acceptable for Phase 1

---

## 12. Known Issues & Limitations

| Issue | Impact | Fix |
|---|---|---|
| Momentum signal resets on restart | Takes 5-10 min to warm up after each Docker restart | Persist price history to DB (Phase 4) |
| No 24/7 operation on laptop | Can't collect 14-day paper baseline | **Move to VPS** (Hetzner $3.50/mo or dihostingin.com Ryzen-1) |
| Position lost on executor restart | Open positions vanish from in-memory state | Persist positions to DB (Phase 4) |
| BTC excluded from trading | BTCUSDT min lot ~$100, too big for $11 capital | Resolved: prompt tells Claude to avoid BTC |
| CMC/news signals not connected | Missing trend intelligence | Phase 3 — CMC_API_KEY exists in .env |
| Proto generation was incomplete | executor Dockerfile only generated pb.go not grpc.pb.go → `UnimplementedExecutorServiceServer` undefined | Fixed: now installs protoc-gen-go-grpc and generates both files |
| /check TOKEN not implemented | User couldn't add custom coins to scan | Fixed: ExtraMarketsStore + /check /listpriority /removepriority commands; scanner unions user list with top-25 |
| /suggestion TOKEN | On-demand personal coin analysis | Implemented: scans coin + BTC/ETH context, calls Claude advisory prompt, returns direction/confidence/entry/exit/reasoning |
| Open trades stuck in DB after restart | exchange_order_id not saved to DB; _open_trades dict lost on restart → trades never closed → win rate never builds | Fixed: exchange_order_id persisted to DB; recover_from_db() on startup expires stale (>4h) trades and reloads recent ones |
| Fear & Greed Index not connected | Missing macro sentiment signal | Phase 3 |
| Weekly learning not run yet | Signal weights still at defaults | Will auto-run next Sunday 00:00 UTC |

---

## 13. Deployment Phases

### Phase 1 — Local Paper (CURRENT)
- ✅ Docker on laptop
- ✅ Paper trading
- ⚠️ Not 24/7
- Goal: Get 14+ days win rate ≥ 60%

### Phase 2 — VPS Paper → Live ($300–500)
- **Hostinger KVM2** (current plan — 8GB RAM, 2vCPU, NVMe) ← user deploying today (2026-05-14)
- OR Hetzner ARM64 Frankfurt, OR dihostingin.com Ryzen-1 (2GB RAM + 2GB swap)
- Paper mode continues until win rate proven
- Flip `PAPER_TRADING=false` only when:
  1. ≥ 14 days continuous paper operation
  2. Win rate ≥ 60%
  3. At least 1 weekly learning report reviewed
  4. $300–500 in USDT-M Futures wallet
- Cost: ~$12–15/month (VPS $3.50 + Claude API $5-10)

### Phase 3 — Full Stack ($2,000)
- Enable Hyperliquid + Polymarket trading
- Connect CMC, Fear & Greed, news feeds
- Scan every 60 seconds (currently 60s)
- Upgrade VPS to 4GB if needed
- Cost: ~$40–50/month

### Phase 4 — Stability
- Persist positions and price history to DB (survive restarts)
- Add position persistence across executor restarts

### Phase 5 — Go Live
- Only after Phase 2 proof of concept
- Full live trading with real capital

### Phase 6 — Ruflo Multi-Agent
- Replace single Claude call with ruflo.yml multi-agent flow
- MarketAnalyst → RiskAuditor → Execute
- NewsMonitor running in background
- LearningAgent on Sundays

---

## 14. VPS Operations — Deploy & Update

### ⚠️ CRITICAL: How to Deploy Code Changes

**Both `bot` and `executor` use `build:` in docker-compose.yml — code is baked into the Docker image, NOT volume-mounted.**

This means:
- `docker compose restart bot` → restarts the SAME OLD image — code changes NOT picked up
- `docker compose up -d --build bot` → rebuilds image with new code, then restarts ✅

**After every `git pull`, always use `--build`:**

```bash
# Standard update flow (after any code change):
cd /root/nanorca
git pull origin main
docker compose up -d --build bot        # rebuild + restart bot only

# If executor changed too (Go code):
docker compose up -d --build executor   # rebuild + restart executor

# If both changed:
docker compose up -d --build bot executor

# Run DB migrations if new .sql files added:
docker compose exec -T postgres psql -U nanorca_user -d nanorca < migrations/002_add_indexes.sql
```

**Signs you forgot `--build` and are running old code:**
- `/status` shows wrong format or wrong capital ($10.00 instead of restored value)
- Fixes you pushed aren't working despite being in git
- Telegram messages don't match what's in the code

### VPS Setup (First Time)

**Recommended:** dihostingin.com Ryzen-1 (2GB RAM, 40GB NVMe, Ryzen 9 7950x3D)
OR Hetzner ARM64 Frankfurt ($3.50/mo)

**Setup steps:**
```bash
# On VPS after SSH login:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
sudo apt-get install -y docker-compose-plugin

# Add 2GB swap (required for 2GB RAM VPS):
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Deploy:
git clone https://github.com/nego94/nanorca.git
cd nanorca
# Copy .env from local machine via scp (never commit .env)
sudo ufw allow 22 && sudo ufw allow 3000 && sudo ufw enable
docker compose up -d

# Access Grafana: https://nanorca.creativorium.com (or http://72.62.124.23:3000 direct)
# NPM admin: http://72.62.124.23:81
```

**PostgreSQL tuning for 2GB RAM** (add to docker-compose.yml postgres service):
```yaml
command: >
  postgres
  -c shared_buffers=256MB
  -c effective_cache_size=512MB
  -c work_mem=16MB
  -c maintenance_work_mem=64MB
```

---

## 15. Database Tables

| Table | Purpose |
|---|---|
| `trades` | Full trade lifecycle — open/close/P&L/reasoning/paper flag |
| `signals` | Per-cycle signal values for analysis |
| `capital_snapshots` | Portfolio snapshots every cycle |
| `signal_weights` | Learned weights (updated weekly by Claude Sonnet) |
| `bot_events` | All bot events, alerts, state changes |
| `learning_reports` | Weekly analysis reports |
| `news_events` | Future: CMC/news feed events |

---

## 16. What Changed — Changelog

| Date | Change | Files |
|---|---|---|
| 2026-05-14 | Phase 2A: Real Binance balance, top-20 market scan, gRPC GetBalances | binance.go, server.go, main.py |
| 2026-05-14 | Pre-filter (saves 60-70% API cost), MIN_GROSS_MOVE 0.09%, graduated confidence | main.py, risk_manager.py |
| 2026-05-14 | Live futures order: GTX Post-Only, /fapi/v1/order | binance.go, common.go |
| 2026-05-14 | Capital bucket split: Tradeable USDT vs Locked coins | binance.go, prometheus_exporter.py |
| 2026-05-14 | Altcoin focus: removed BTC from priority, Claude prompt updated | claude_brain.py, .env |
| 2026-05-14 | Futures wallet balance detection (/fapi/v2/balance) | binance.go |
| 2026-05-14 | Max 3 parallel positions, formatted Telegram broadcast | risk_manager.py, main.py |
| 2026-05-14 | Spot suggestion from Claude (manual only, not executed) | claude_brain.py, main.py |
| 2026-05-14 | Grafana: three-bucket capital panel, trade history from PostgreSQL | nanorca.json |
| 2026-05-14 | Suggestion store: 50–64 confidence → /markets advisory (not traded) | suggestion_store.py, main.py, telegram_bot.py |
| 2026-05-14 | /readmarkets alias added; /markets redesigned with suggestions + prices | telegram_bot.py |
| 2026-05-14 | Min trade confidence raised to 65; removed 1% confidence tier | risk_manager.py, main.py |
| 2026-05-14 | BINANCE_SCAN_TOP_N increased to 25 | .env |
| 2026-05-14 | Documented future /check TOKEN + dynamic priority market system | NANORCA_MEMORY.md |
| 2026-05-14 | Exclude BTC+ETH from suggestions (min lot too large / low % volatility) | suggestion_store.py, claude_brain.py |
| 2026-05-14 | VPS deployment: Hostinger KVM2 (paper mode, IP: 72.62.124.23) | — |
| 2026-05-14 | Fix: executor Dockerfile now generates both nanorca.pb.go AND nanorca_grpc.pb.go | executor/Dockerfile |
| 2026-05-14 | Fix: VPS logs dir permission — mkdir -p logs && chmod 777 on host | VPS manual step |
| 2026-05-14 | ✅ VPS FULLY OPERATIONAL — bot running 24/7 on Hostinger KVM2 | — |
| 2026-05-14 | Domain setup: nanorca.creativorium.com → Grafana via Nginx Proxy Manager + Let's Encrypt SSL | VPS /root/proxy/ |
| 2026-05-14 | Scan interval: 60s → 30s (better momentum signal quality, ~$1.16/month API vs $0.58) | .env SCAN_INTERVAL_SECONDS |
| 2026-05-14 | Fix: Grafana 11 breaks on uid field in prometheus.yml and timescaledb:true in postgres.yml — both removed | prometheus.yml, postgres.yml, nanorca.json |
| 2026-05-14 | Grafana datasource note: Prometheus uses isDefault:true (no uid needed). PostgreSQL uses uid:nanorca-postgres. timescaledb option removed (Grafana 11 deprecated it) | — |
| 2026-05-14 | /suggestion now uses 2-pass "virtual ruflo": Pass 1 MarketAnalyst → Pass 2 RiskAuditor. RiskAuditor can lower confidence. Falls back to single-pass if Pass 2 fails. Cost: ~$0.002/call. | claude_brain.py, telegram_bot.py |
| 2026-05-14 | Fix: trades not saved to DB — asyncpg rejects json.dumps() string for JSONB column; fixed by passing dict directly | db.py |
| 2026-05-14 | Fix: null exchange from Claude silently failing NOT NULL constraint; fixed with .get("exchange") or "binance" | outcome_logger.py |
| 2026-05-14 | Fix: trade save errors now re-raise + notify via Telegram instead of silently swallowing | outcome_logger.py, main.py |
| 2026-05-14 | Fix: /status Bot Tracker Capital was stale (only synced at startup); now calls capital_tracker.refresh_from_real() on every /status | telegram_bot.py, capital_tracker.py |
| 2026-05-14 | Feature: TELEGRAM_GROUP_CHAT_ID — broadcasts now go to both private chat and group if set | config.py, telegram_bot.py |
| 2026-05-14 | Feature: PaperOrderBook — full paper trade lifecycle: PLANNED→FILLED→CLOSED with target/stop/timeout monitoring. P&L calculated in Python per close. DB saved on fill not on plan. | paper_order_book.py, main.py |
| 2026-05-14 | Refactor: _manage_open_positions split into _process_paper_fills/_process_paper_exits (paper) and _manage_live_positions (live). Paper and live paths completely separate. | main.py |
| 2026-05-14 | Fix: Go executor race condition — concurrent goroutines writing prices map → fatal crash. Fixed with sync.RWMutex protecting all prices map reads/writes. | binance.go |
| 2026-05-14 | Fix: futuresWalletUSDT now reads WalletBalance (total) not AvailableBalance (free only) — prevents $0 when margin is locked by live positions | binance.go |
| 2026-05-14 | Fix: balance failures upgraded from DEBUG → WARN logging so errors are visible in logs | binance.go |
| 2026-05-14 | Fix: gRPC scan timeout 15s → 25s — 30 markets were timing out at 15s causing DEADLINE_EXCEEDED loop | order_router.py |
| 2026-05-14 | Fix: gRPC auto-reconnect on UNAVAILABLE/INTERNAL errors — was broken forever until full bot restart | order_router.py |
| 2026-05-14 | Fix: Prometheus holds last known metrics when executor is down (was zeroing Grafana on every outage) | prometheus_exporter.py |
| 2026-05-14 | Fix: extra_markets cap 10 → 5 (top-25 auto + 5 manual = 30 total max per cycle) | extra_markets_store.py |
| 2026-05-14 | Fix: startup capital sync retries up to 3× with 10s gap — executor not ready on first boot caused $10 showing at startup | main.py |
| 2026-05-14 | Feature: config.binance_scan_top_n added — startup message now shows correct top-N (was hardcoded to 3) | config.py, main.py |
| 2026-05-14 | Feature: /status shows USDT free + Total separately per exchange for full visibility | telegram_bot.py |
| 2026-05-14 | Fix: /status no longer calls refresh_from_real() in paper mode — was wiping paper P&L on every /status call. Real balance shown separately, paper tracker accumulates independently. | telegram_bot.py |
| 2026-05-14 | Fix: /report now splits Paper vs Live sections — separate win rate and P&L per mode | telegram_bot.py |
| 2026-05-14 | Fix: /suggestion adds futures symbol aliases (PEPE→1000PEPE, SHIB→1000SHIB etc.) and detects spot-only coins (OSMO, ATOM etc.) with clear error message | telegram_bot.py |
| 2026-05-14 | Fix: main_loop blocks spot-only coins (OSMO, ATOM etc.) from being planned as paper futures trades | main.py |
| 2026-05-14 | Fix: _process_paper_fills sends Telegram alert when DB save fails instead of silent log | main.py |
| 2026-05-14 | Grafana: mode dropdown filter (All/Paper/Live), separate Paper Stats and Live Stats panels, Cumulative P&L split by mode, opened_at used for trade time display | nanorca.json |
| 2026-05-14 | Fix: Grafana mode variable uses simple string values (all/paper/live) not SQL — SQL conditions in values caused stuck state when switching modes | nanorca.json |
| 2026-05-14 | Fix: asyncpg JSONB codec registered on pool init — was rejecting Python lists for signal_mix jsonb column with "expected str, got list" → paper trades not saved to DB | db.py |
| 2026-05-14 | Fix: duplicate paper orders on same market — now blocked if market already has pending/open order | paper_order_book.py |
| 2026-05-14 | Feature: periodic monitoring updates every 20 min while paper position is open — shows current price, unrealized P&L, distance to target/stop | paper_order_book.py, main.py |
| 2026-05-14 | Fix: log_trade_closed fallback — when _open_trades cleared by restart, update DB directly by exchange_order_id. Prevents trades stuck as 'open' after restart. | db.py, outcome_logger.py |
| 2026-05-14 | Fix: capital background sync — main_loop retries real balance sync every cycle until success (synced_from_real flag). Fixes $10 stuck capital after failed startup sync. | capital_tracker.py, main.py |
| 2026-05-15 | Fix: SAGA/fast-trend coins re-entering every 30s after close — added 15-min per-market cooldown to PaperOrderBook. After any close, same market is blocked for 15 min. | paper_order_book.py |
| 2026-05-15 | Fix: DB trades stuck as "open" — `close_trade_by_order_id()` is now the primary close path (was fallback). Primary path no longer depends on `_open_trades` in-memory dict. | outcome_logger.py |
| 2026-05-15 | Fix: if primary DB close fails, fallback is now always tried (was unreachable on primary exception) | outcome_logger.py |
| 2026-05-15 | Fix: duplicate trade inserts — Python-level guard in `log_trade_opened()` + DB-level SELECT check before INSERT in `save_trade()` | outcome_logger.py, db.py |
| 2026-05-15 | Fix: DB close errors now send Telegram `⚠️ DB CLOSE ERROR` alert with exact error; WIN/LOSS telegram gets `⚠️ DB ERR` tag when DB update failed | main.py |
| 2026-05-15 | Fix: capital resets to real exchange balance on every restart — added restore_from_snapshot() to capital_tracker; startup reads last capital_snapshots record before any real balance sync; real balance sync only runs on first boot (no snapshot exists) | capital_tracker.py, db.py, main.py |
| 2026-05-15 | Fix: missing DB index on exchange_order_id causing full TimescaleDB chunk scans on every paper trade close | migrations/002_add_indexes.sql |
| 2026-05-15 | DB fix: manually corrected 11 stuck "open" trades (SAGA ×8, ZEC, SOL, DOGE) with actual P&L from Telegram history using direct SQL UPDATE | VPS SQL |
| 2026-05-15 | Redesign: /status now shows 4 clear sections — (1) Bot state, (2) Real Account (actual Binance balance, display only), (3) Paper Simulation (paper capital, floor, daily P&L, open positions — completely separate from real money), (4) Trading Plan. Real money and paper money never mixed. | telegram_bot.py |
| 2026-05-15 | Fix: floor check uses paper capital only — real Binance balance dropping below floor does NOT stop paper trading. Bot continues collecting data regardless of real account balance. | main.py, capital_tracker.py |
| 2026-05-15 | Simplify: bot startup/shutdown messages now one-line only ("NANORCA online — Paper mode / Type /status for full details"). All detail moved to /status command. | main.py |
| 2026-05-15 | Fix: /status Daily P&L was reset on every restart (used in-memory counter). Now queries DB get_performance_context() for true 24h P&L + 24h/7d win rates that survive restarts. | telegram_bot.py |
| 2026-05-15 | Fix: /status % change was wrong — used config.starting_capital_usd ($10) not actual synced starting capital ($11.39). Added _effective_starting to CapitalTracker. Updated by sync_from_real_balance, restored from snapshot. pct_from_start, floor_capital, _snapshot all use it. | capital_tracker.py |
| 2026-05-15 | Fix: /status leverage showed 10x (plan value) but paper trades hardcode 3x in PaperOrderBook. Status now shows 3x for paper mode. | telegram_bot.py |
| 2026-05-15 | Fix: Prometheus daily_pnl_usd now updated from DB after each Claude cycle — Grafana shows accurate 24h P&L across restarts. | main.py |
| 2026-05-15 | Fix: paper positions lost on restart — added target_price and stop_price columns to trades table (migration 003). Saved on fill. recover_open_positions() in PaperOrderBook reconstructs PaperOrder objects at startup with correct elapsed hold time so monitoring continues. | migrations/003_add_target_stop.sql, db.py, outcome_logger.py, paper_order_book.py, main.py |
| 2026-05-15 | Fix: Grafana top stat panel 3 now shows 📄 Paper Capital (nanorca_capital_current_usd ~$13) not real USDT. Panel 4 shows 💳 Real USDT (Binance). Panel 41 chart clearly separates blue=paper capital vs green=real USDT vs orange=locked. | grafana/dashboards/nanorca.json |
| 2026-05-15 | Fix: DB error "inconsistent types deduced for parameter $3: integer versus numeric" on ALL trade closes — asyncpg saw integer literal 0 in CASE WHEN $3 > 0 conflicting with NUMERIC column type. Fix: use 0.0 (float literal) and explicitly cast pnl/fees to float() before passing. Affected every paper trade close for 4+ hours. | db.py |
| 2026-05-15 | DB fix: manually closed 10 stuck "open" trades (AIUSDT ×5, XRP, ZEC, INJ, SAGA, SOL) from 02:00-06:57 session using SQL UPDATE with actual P&L from Telegram. All had correct fills saved but closes failed due to type bug. | VPS SQL |
| 2026-05-15 | Feature: /report now defaults to all-time stats (since 2020-01-01). Filtered views: /report 24h, /report 7d, /report 30d. Shows avg win/loss, fees, paper/live split. /history defaults to 20 (was 10), max 100. Paper/live emoji per trade row. Usage hints added. | telegram_bot.py |
| 2026-05-15 | Documented: profitability break-even analysis ($125/mo overhead, need $1,000–2,000 capital for real profit, optimize to 90s scan after baseline). Grid bot architecture designed: Binance managed grid API (Option A) + manual limit order grid (Option B), Go executor handles fills event-driven, Python activates/monitors only. | NANORCA_MEMORY.md |
| 2026-05-15 | Planned: Phase B (Grid Bot, ~3.5 weeks) — B1 ranging detection (ATR/BB signals + Claude GRID action), B2 Go grid engine (WebSocket fill-reactive), B3 Python activation/monitoring, B4 DB tables (grid_sessions/grid_orders), B5 Telegram /grid commands. Phase C (Web Dashboard, ~4.5 weeks) — FastAPI backend, React+Vite frontend, TradingView charts, settings management, grid UI, Docker+Nginx deploy. Full plan in Sections 19–20. | NANORCA_MEMORY.md |
| 2026-05-15 | Capital analysis: at $100 trading capital → still -$60 to -$90/month with current $125/mo overhead. Breakeven at ~$150–200 capital with optimized (90s scan) costs. Real profit starts at $1k+ capital. | NANORCA_MEMORY.md |

---

## 17. Profitability Analysis & Break-even (as of 2026-05-15)

### Observed Performance (Paper, 3 days)
| Metric | Value |
|---|---|
| Starting capital | $11.39 |
| Paper profit (3 days) | $2.63 |
| Trades | 26 (73.1% WR) |
| Paper return rate | ~7.7%/day (inflated — no slippage, instant fills) |

### Monthly Costs
| Item | Current | Optimized (90s scan) |
|---|---|---|
| VPS (Hostinger KVM2) | $25/mo | $25/mo |
| Claude API (~30s cycle, pre-filter) | ~$100/mo | ~$35/mo |
| **Total overhead** | **~$125/mo** | **~$60/mo** |

### Break-even Capital Required
| Scenario | 5%/mo live return | 10%/mo live return | 15%/mo live return |
|---|---|---|---|
| $125/mo overhead (current) | $2,500 | $1,250 | $833 |
| $60/mo overhead (optimized) | $1,200 | **$600** | $400 |

**Realistic live return estimate:** 5–15%/month. Paper numbers (231%/month annualized) will NOT hold live — slippage, worse fills, losing streaks, and thin order books at small capital cut results significantly.

### Path to Real Profit

**Step 1 — Reduce Claude API cost (biggest lever):**
- After 14-day paper baseline (2026-05-28), increase `SCAN_INTERVAL_SECONDS=30` → `90`
- This cuts Claude calls by ~65% → drops API cost from ~$100 to ~$35/month
- Overhead drops from $125 to $60/month

**Step 2 — Capital targets:**
- **$500** → barely breaks even if live returns 10%+/month
- **$1,000** → breaks even comfortably, small real profit starts appearing
- **$2,000–3,000** → real sustainable monthly profit after all costs
- **$5,000+** → proper income scale (e.g. $500/mo profit at 10% return on $5k capital)

**Step 3 — Improve returns (in priority order):**
1. Longer hold targets (current 0.5% target is too small → increase to 1.0–1.5% for bigger wins)
2. Increase cooldown after LOSS on same market (30 min, not 15 min — avoid revenge entries)
3. Multi-signal confirmation before entry (require 2+ signals, not just momentum)
4. Add grid trading for ranging markets — earns on sideways price action where momentum bot sits idle
5. Increase capital gradually as win rate is proven live

**Bottom line:** With current settings you need ~$1,200–2,500 capital and optimized costs to break even on VPS + API overhead. For real profit: $2,000+ capital + 90s scan interval after baseline.

---

## 18. Grid Trading Architecture

### What is grid trading
Grid bot places buy/sell limit orders at regular price intervals. When price oscillates in a range, it earns the spread on each oscillation. Profits from **volatility** (sideways chop), unlike momentum bot which profits from **directional trends**.

### Two options for running grid from outside

**Option A: Binance Managed Grid (simpler)**
- API endpoint: `POST /sapi/v1/algo/spot/newOrderGridAlgo` (spot) or `/fapi/v1/algo/futures/newOrderGridAlgo` (futures)
- Binance manages ALL fill detection and order replacement server-side
- Bot just calls "start grid" → Binance runs it autonomously
- Bot monitors status via `/sapi/v1/algo/spot/openOrders`
- Stops grid via `DELETE /sapi/v1/algo/spot/order`
- **Advantage:** Extremely simple, no real-time fill handling needed
- **Limitation:** Less control over grid logic, Binance determines execution

**Option B: Manual Grid via Limit Orders (more control)**
- Bot places all N buy orders + N sell orders at grid levels via standard `/fapi/v1/order`
- Go executor WebSocket detects each fill in real-time (milliseconds)
- On fill: Go immediately places opposite order at next grid level
- Python brain checks grid health every 60s (not after each fill)
- **Advantage:** Full control, works the same way as custom grid logic
- **Limitation:** More complex, requires Go executor changes

### Can the current bot handle grid transaction speed?

**Yes, but with the right architecture split:**

| Layer | Role | Speed |
|---|---|---|
| Python brain | Detect ranging market → decide to activate grid (Claude call) | 30s cycle |
| Go executor | Detect fills via WebSocket → place next grid order | Milliseconds (real-time) |
| Python brain | Check grid health, stop if market breaks out of range | Every 60s |

**Key insight:** The 30s Python cycle is fine for **activation and monitoring**. The Go executor handles **fill reaction** event-driven. Python never needs to react to individual fills — only Go does.

### Changes needed in Go executor
- New gRPC call: `StartGrid(market, upper, lower, levels, capital_per_grid)`
- Go manages all grid orders internally (place → fill → replace cycle)
- New gRPC call: `StopGrid(market)` → cancels all open grid orders
- New gRPC call: `GetGridStatus(market)` → returns current grid P&L, fill count, open orders
- Go streams `GridFillEvent` to Python when a grid order fills (for Telegram alerts)

### Grid bot activation logic (Claude's job)
Claude detects ranging market (low ATR relative to price, oscillating around a mean) and activates a grid:
1. Claude decides: "XRPUSDT is ranging between $0.62–$0.68, grid opportunity"
2. Python calls `StartGrid("XRPUSDT", upper=0.68, lower=0.62, levels=10, budget=$50)`
3. Go places 5 buy + 5 sell orders across range
4. Go manages all fills event-driven until `StopGrid` called
5. Claude checks every 60s: is it still ranging? If breakout → call `StopGrid`

### Budget separation
- Grid budget: separate from momentum trading budget (e.g. 30% of capital to grid, 70% to momentum)
- Max 5 spot grids + 5 futures grids simultaneously
- Separate DB tables: `grid_sessions`, `grid_orders` (not mixed with `trades` table)
- Telegram commands: `/grid list`, `/grid stop SYMBOL`, `/grid status SYMBOL`

### Implementation phase
Phase B (after 14-day paper baseline confirmed). Build Option A first (Binance managed grid) — faster to implement, test that Claude activation logic works correctly, then upgrade to Option B for more control.

---

## 19. Phase B — Grid Trading Roadmap

### Overview
Grid bot earns on sideways/ranging markets where the momentum bot sits idle. Claude detects ranging conditions → activates a grid → Go executor manages all fills event-driven → Python monitors health every 60s.

**Total estimated build time: 3–4 weeks**

---

### B1 — Ranging Market Detection (Week 1)

**What needs to change:**

`bot/brain/signal_builder.py` — add ranging signals:
```python
# ATR-based ranging detection
atr = high_low_range / price  # true range proxy from OHLCV
atr_ratio = atr / avg_atr_14  # normalized: <0.7 = calm, >1.3 = volatile
# Bollinger Band squeeze: (upper - lower) / mid < threshold
bb_width = (bb_upper - bb_lower) / bb_mid
# Mean reversion: price oscillating around moving average
mean_deviation_pct = abs(price - ema_20) / ema_20 * 100
```

New signals added to signal dict:
- `atr_ratio`: float — <0.8 means ranging, >1.2 means trending
- `bb_squeeze`: bool — True when Bollinger Bands are tight (ranging)
- `price_mean_deviation_pct`: float — small = ranging around mean

**Claude prompt update** (`bot/brain/claude_brain.py`):
- Add ranging signal values to the market context block
- Add instruction: "If atr_ratio < 0.8 AND bb_squeeze=True → recommend action=GRID instead of LONG/SHORT"
- Claude response now includes `action: "GRID"` as possible output (alongside LONG/SHORT/SKIP)
- Grid-specific fields in Claude response: `grid_upper`, `grid_lower`, `grid_levels`, `grid_budget_pct`

**Files to change:** `signal_builder.py`, `claude_brain.py`, `main.py` (handle action=GRID routing)

---

### B2 — Go Grid Engine (Week 1.5–2)

New Go package: `executor/internal/grid/`

```
grid/
├── manager.go      — GridManager: lifecycle (start/stop/status per market)
├── engine.go       — places grid orders, reacts to fills, replaces orders
└── types.go        — GridSession, GridOrder structs
```

**gRPC additions** (`proto/nanorca.proto`):
```protobuf
rpc StartGrid(StartGridRequest) returns (StartGridResponse);
rpc StopGrid(StopGridRequest)  returns (StopGridResponse);
rpc GetGridStatus(GridStatusRequest) returns (GridStatusResponse);
```

**StartGrid flow (Go):**
1. Calculate N price levels between lower and upper
2. Place N buy limit orders below current price
3. Place N sell limit orders above current price
4. WebSocket fill listener: when buy fills → place sell at next level up; when sell fills → place buy at next level down
5. Track realized P&L per grid session

**Fill detection:** Reuse existing WebSocket feed (`ws_feed.go`) — add order fill event listener. Fills trigger immediate order replacement within milliseconds. Python never involved in individual fills.

**StopGrid flow:**
1. Cancel all open grid orders via `DELETE /fapi/v1/allOpenOrders`
2. Close any residual position at market
3. Mark session as closed, return final P&L

**Files to add:** `executor/internal/grid/` package, update `executor/pkg/grpcserver/server.go`, update `proto/nanorca.proto`

---

### B3 — Python Grid Activation & Monitoring (Week 2)

**`bot/data/paper_grid_book.py`** (new file):
- Paper grid emulator: mirrors Go grid logic in Python for paper mode
- Places simulated grid orders, checks price on each cycle
- Calculates grid P&L from fills
- Separate from PaperOrderBook (different trade type)

**`bot/main.py`** updates:
```python
# In main trading cycle, after Claude decision:
if result["action"] == "GRID":
    if config.paper_trading:
        paper_grid_book.activate(market, result)
    else:
        await router.start_grid(market, result)  # calls Go gRPC

# Grid health check (every 60s, not every fill):
async def _check_grid_health():
    for market, session in active_grids.items():
        status = await router.get_grid_status(market)
        # If price breaks out of grid range → stop grid
        if price > session.upper * 1.02 or price < session.lower * 0.98:
            await router.stop_grid(market)
            telegram.send_info(f"Grid stopped: {market} broke out of range")
```

**`bot/learning/outcome_logger.py`** — add `log_grid_session_closed()` method

---

### B4 — Database Tables (2 days)

**New migration:** `migrations/004_grid_tables.sql`
```sql
CREATE TABLE grid_sessions (
    id          SERIAL PRIMARY KEY,
    market      TEXT NOT NULL,
    exchange    TEXT NOT NULL DEFAULT 'binance',
    upper_price NUMERIC(20,8) NOT NULL,
    lower_price NUMERIC(20,8) NOT NULL,
    levels      INT NOT NULL,
    budget_usd  NUMERIC(12,4) NOT NULL,
    paper       BOOLEAN NOT NULL DEFAULT TRUE,
    status      TEXT NOT NULL DEFAULT 'active',  -- active/stopped/completed
    realized_pnl NUMERIC(12,4) DEFAULT 0,
    fill_count  INT DEFAULT 0,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at  TIMESTAMPTZ,
    stop_reason TEXT   -- 'breakout', 'manual', 'timeout', 'floor_hit'
);

CREATE TABLE grid_orders (
    id              SERIAL PRIMARY KEY,
    session_id      INT REFERENCES grid_sessions(id),
    level           INT NOT NULL,
    side            TEXT NOT NULL,  -- 'BUY' or 'SELL'
    price           NUMERIC(20,8) NOT NULL,
    size_usd        NUMERIC(12,4) NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',  -- open/filled/cancelled
    filled_at       TIMESTAMPTZ,
    pnl_usd         NUMERIC(12,4),
    exchange_order_id TEXT
);
```

---

### B5 — Telegram Grid Commands (2 days)

| Command | Function |
|---|---|
| `/grid` | List active grids: market, range, P&L, fill count |
| `/grid SYMBOL` | Status of specific market grid |
| `/grid stop SYMBOL` | Manually stop a grid |
| `/grid history` | Last 10 closed grid sessions with P&L |

Added to `telegram_bot.py` as `_cmd_grid()`.

---

### Grid Budget Separation
- Momentum bot: uses `capital_tracker.current_capital`
- Grid bot: separate `GRID_BUDGET_PCT` env var (e.g. 30% of capital reserved for grids)
- At $100 capital: 70% momentum ($70) + 30% grid ($30, spread across max 5 grids)
- Grid P&L feeds back into `capital_tracker` on session close

---

## 20. Phase C — Web Dashboard Roadmap

### Why Replace Grafana
Grafana is excellent for charts but cannot:
- Change bot settings (scan interval, thresholds, floor %)
- Start/stop grids interactively
- Show live positions with controls
- Be easily customized without JSON panel editing

The web app replaces Grafana's control functions but **can embed Grafana panels** (via iframe) for complex time-series charts while keeping the UI clean.

**Total estimated build time: 4–6 weeks**

---

### Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | **FastAPI** (Python) | Same language as bot, WebSocket support, async |
| Frontend | **React + Vite** | Fast dev, Claude can generate it easily, large ecosystem |
| Charts | **TradingView Lightweight Charts** (free) | Professional candlestick/line charts, no license cost |
| Styling | **Tailwind CSS** | Utility-first, fast to build, no design system needed |
| Real-time | **WebSocket** (FastAPI → React) | Live price/position/capital updates without polling |
| Auth | **Single bearer token** in `.env` | One owner, no multi-user complexity |
| Deploy | **Docker service** + Nginx proxy | Fits existing infra (NPM already running) |

---

### C1 — Backend API (Week 1)

New service: `dashboard/` directory (Python FastAPI app, separate Docker service)

```
dashboard/
├── main.py           — FastAPI app, CORS, auth middleware
├── api/
│   ├── status.py     — GET /api/status (bot state, capital, positions)
│   ├── trades.py     — GET /api/trades, GET /api/report
│   ├── settings.py   — GET/POST /api/settings (read/write .env safely)
│   ├── grid.py       — GET/POST/DELETE /api/grid (list/start/stop)
│   └── ws.py         — WebSocket /ws (live feed: prices, fills, alerts)
├── db.py             — Shared asyncpg pool (same PostgreSQL)
├── config.py         — Reads same .env as bot
└── Dockerfile
```

**Settings management approach:** API reads `.env`, writes safe subset back. Only allows changing: `SCAN_INTERVAL_SECONDS`, `CONFIDENCE_THRESHOLD`, `MAX_OPEN_POSITIONS`, `CAPITAL_FLOOR_PCT`, `TRADING_MODE`, `GRID_BUDGET_PCT`. Never writes `BINANCE_API_KEY`, `TELEGRAM_*`, passwords.

After settings change → API calls `docker compose restart bot` via subprocess (bot container has Docker socket access with read-only mount).

---

### C2 — Frontend Pages (Week 2–3)

```
dashboard/frontend/
├── src/
│   ├── pages/
│   │   ├── Dashboard.jsx    — Overview: capital, bot state, active positions
│   │   ├── Trades.jsx       — Trade history table with filters
│   │   ├── Report.jsx       — P&L charts, win rate over time
│   │   ├── Grid.jsx         — Active grids, start new grid, grid history
│   │   ├── Markets.jsx      — Live scanned markets, suggestions
│   │   └── Settings.jsx     — Bot settings form with save/restart
│   ├── components/
│   │   ├── CapitalCard.jsx  — Current capital, floor, % change
│   │   ├── PositionRow.jsx  — Single position with unrealized P&L
│   │   ├── GridCard.jsx     — Single grid session with fill count
│   │   └── LiveChart.jsx    — TradingView chart for price history
│   └── hooks/
│       └── useWebSocket.js  — Real-time data subscription
```

**Dashboard page layout:**
```
┌─────────────────────────────────────────────────┐
│ 📄 Paper: $13.42  💳 Real: $3.81  🤖 Running   │
│ 24h P&L: +$0.87  WR: 73.1%  Open: 2/3          │
├──────────────────────┬──────────────────────────┤
│ OPEN POSITIONS       │ ACTIVE GRIDS              │
│ SAGAUSDT LONG $0.34  │ XRPUSDT 0.62-0.68        │
│ unrealized +$0.12    │ 14 fills, +$0.43          │
├──────────────────────┴──────────────────────────┤
│ RECENT TRADES (last 5)                           │
│ ✅ AIUSDT +$0.21  ❌ INJUSDT -$0.38  ✅ ...    │
└─────────────────────────────────────────────────┘
```

---

### C3 — Settings Management (Week 3)

**Settings page:** Form with current values pre-filled. Changing any value shows a "Save & Restart Bot" button. On save:
1. API writes new values to `.env`
2. API triggers `docker compose up -d --build bot` (if code change) or `docker compose restart bot` (env-only change)
3. WebSocket broadcasts "Bot restarting..." → UI shows spinner
4. WebSocket broadcasts "Bot online" when heartbeat resumes

**Editable settings from UI:**
- Scan interval (slider: 30s / 60s / 90s / 120s)
- Confidence threshold (slider: 55–75)
- Max open positions (1 / 2 / 3)
- Capital floor % (slider: 15–40%)
- Trading mode (dropdown: nanorca_decide / conservative / hybrid / aggressive)
- Grid budget % (slider: 0–50%)
- Per-market cooldown minutes (slider: 5–60 min)

---

### C4 — Grid Management UI (Week 4)

**Grid page:**
- Table of active grids: market, range, levels, fills, realized P&L, time running
- "Stop" button per grid
- "New Grid" modal: pick market, set upper/lower price (or let Claude suggest), levels (6/10/15), budget
- Grid history: past sessions, P&L, fill count

**New Grid modal flow:**
1. User picks market (dropdown of scanned markets)
2. Click "Analyze" → API calls Claude on-demand to suggest range/levels
3. Claude returns suggested upper/lower based on recent ATR + support/resistance
4. User can override any field
5. "Start Grid" → API calls Go gRPC StartGrid

---

### C5 — Deploy (2 days)

**`dashboard/Dockerfile`:**
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Build frontend
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json .
RUN npm install
COPY frontend/ .
RUN npm run build
# Copy built frontend to FastAPI static files
COPY --from=frontend /frontend/dist /app/static
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
```

**`docker-compose.yml`** — add dashboard service:
```yaml
dashboard:
  build: ./dashboard
  ports: ["8090:8090"]
  environment:
    - DATABASE_URL=${DATABASE_URL}
    - DASHBOARD_SECRET=${DASHBOARD_SECRET}
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro  # for restart bot
    - ./.env:/app/.env                               # for settings write
  depends_on: [postgres]
  restart: unless-stopped
```

**Nginx Proxy Manager:** Add new proxy host `nanorca-admin.creativorium.com` → port 8090, Let's Encrypt SSL.

**`.env` additions:**
```bash
DASHBOARD_SECRET=<random 32-char token>  # bearer token for API auth
DASHBOARD_PORT=8090
```

---

### Phase Summary & Timeline

| Phase | Deliverable | Estimated Time |
|---|---|---|
| **B1** | Ranging detection signals + Claude prompt update | 1 week |
| **B2** | Go grid engine (orders, fills, WebSocket) | 1.5 weeks |
| **B3** | Python grid activation + paper grid book | 0.5 week |
| **B4** | DB tables (grid_sessions, grid_orders) | 2 days |
| **B5** | Telegram grid commands | 2 days |
| **— Phase B total —** | **Full grid trading** | **~3.5 weeks** |
| **C1** | FastAPI backend (REST + WebSocket) | 1 week |
| **C2** | React frontend (Dashboard, Trades, Report) | 1.5 weeks |
| **C3** | Settings management + bot restart | 0.5 week |
| **C4** | Grid UI (active grids, new grid modal) | 1 week |
| **C5** | Docker deploy + Nginx SSL | 2 days |
| **— Phase C total —** | **Full web dashboard** | **~4.5 weeks** |
| **TOTAL** | Grid bot + Web app | **~8 weeks** |

**Start order:** B1 → B2 → B3 → B4 → B5 (grid) then C1 → C2 → C3 → C4 → C5 (web app). Do not start Phase C until Phase B is paper-tested for at least 1 week.

---

## 21. Current Status & Roadmap

**Bot status as of 2026-05-15:** Running 24/7 on VPS. Paper trading stable. Paper capital ~$13.13 (persists across restarts via DB snapshot). 26 trades in DB (73.1% WR, +$2.63 paper P&L). /report now defaults to all-time stats, /history defaults to 20. /status shows correct 24h P&L from DB, correct % from actual starting capital, correct 3x paper leverage. Real account ($3-4 Binance) and paper simulation fully separated. Current monthly overhead ~$125 (Claude API ~$100 + VPS $25). See Section 17 for break-even analysis — need $1,000–2,000 capital for real profit. At $100 capital → still -$60 to -$90/month (overhead too high); breakeven at ~$150–200 with optimized costs. **Next build phases:** B = Grid Bot (~3.5 weeks), C = Web Dashboard (~4.5 weeks). See Sections 19–20 for full plan.

### Paper Trade Data as Analysis Source
All paper trades are saved to DB with FULL context:
- `market`: which coin was traded
- `signal_mix`: JSONB — exactly which signals fired and at what value
- `confidence_score`: Claude's confidence when it decided to trade
- `claude_reasoning`: full reasoning text from Claude
- `direction`, `entry_price`, `exit_price`, `pnl_usd`, `win`: outcome

This data feeds the **weekly learning loop** (Sunday 00:00 UTC):
1. Claude Sonnet reads all closed trades from the past week
2. Analyzes which signals predicted wins vs losses
3. Outputs updated signal weights + reasoning
4. Weights saved to `signal_weights` table
5. Next week's trades use the updated weights → bot learns and improves

**First weekly learning:** Sunday 2026-05-18 00:00 UTC — will analyze the ~15 paper trades already in DB.

### Confirmed Feature Decisions
- ✅ Grid trading — OUR bot does it (not Binance built-in), AI-activated, separate from momentum
- ✅ Claude news analysis — replace placeholder CMC scraper with Claude Haiku reading headlines
- ❌ Gemini news — skipped (use Claude instead)
- ❌ Binance AI Signal — skipped (premium API required, not accessible)
- ❌ Next.js dashboard — skipped (Grafana is sufficient for now)

### Bug Fixes (Phase A — COMPLETED)

| # | Bug | Root Cause | Status |
|---|---|---|---|
| 1 | Capital shows 0 after paper trade | `refresh_from_real()` in `/status` reset paper P&L on every call | ✅ Fixed |
| 2 | Paper P&L overwritten on every /status | Same — real balance sync conflicted with paper simulation | ✅ Fixed |
| 3 | /report shows 0 closed, no paper/live split | No paper filter in query | ✅ Fixed — now separate Paper/Live sections |
| 4 | /suggestion says OSMO/PEPE not found | Spot-only coins have no futures; 1000PEPE naming on futures | ✅ Fixed — aliases + spot-only detection |
| 5 | Grafana shows no paper trade history | DB was empty (old trades had JSONB bug); Grafana had no mode filter | ✅ Fixed — mode dropdown, paper/live split panels |
| 6 | Grafana mode dropdown stuck after switching to Live | Variable values contained SQL syntax causing URL encoding issues | ✅ Fixed — simple string values (all/paper/live) |
| 7 | asyncpg "expected str, got list" on signal_mix | JSONB column received Python list without codec — needs json.dumps or codec | ✅ Fixed — registered JSONB codec on pool init; pass dicts directly |
| 8 | Duplicate paper orders on same market (KITE ×3) | PaperOrderBook.plan() allowed multiple orders per market | ✅ Fixed — blocks if market already has pending/open order |
| 9 | Open trades stuck as 'open' in DB after restart | _open_trades dict lost on restart → log_trade_closed() found nothing | ✅ Fixed — fallback close_trade_by_order_id(); recover_from_db() on startup |
| 10 | Capital stuck at $10 after startup sync failure | Executor not ready at boot → sync failed → synced_from_real never set | ✅ Fixed — 3-retry startup loop + background sync every cycle until flag set |
| 11 | Go executor fatal crash (concurrent map writes) | Multiple goroutines writing prices map without mutex | ✅ Fixed — sync.RWMutex on all prices map reads/writes |
| 12 | Grafana zeros all metrics when executor is down | update_exchange_balances([]) called with empty list → zeroed gauges | ✅ Fixed — early return if balances empty (hold last known value) |
| 13 | SAGA (or any fast-trending coin) re-enters every 30s after close | After close, PaperOrderBook had no cooldown — next cycle saw free slot, Claude still saw momentum, planned again | ✅ Fixed — 15-min per-market cooldown added to PaperOrderBook |
| 14 | DB trades stuck as "open" despite Telegram showing WIN/LOSS | `log_trade_closed()` was using `_open_trades[id]` as primary path; if that failed the fallback was never tried; exception swallowed silently | ✅ Fixed — `close_trade_by_order_id()` is now primary (always knows order_id); PK path is fallback; errors now raise → Telegram alert |
| 15 | Duplicate trade insert possible if fill detected twice | No guard in `log_trade_opened()` or `save_trade()` | ✅ Fixed — Python-level duplicate guard in `log_trade_opened()`; DB-level check in `save_trade()` before INSERT |
| 16 | DB close error silently swallowed — user had no visibility | `except` in `_process_paper_exits` only wrote to file log on VPS | ✅ Fixed — now sends Telegram `⚠️ DB CLOSE ERROR` alert with exact exception; WIN/LOSS message gets `⚠️ DB ERR` tag |
| 17 | Capital resets to real exchange balance ($3-4) on every restart | `sync_from_real_balance()` always called on startup — overwrites accumulated paper capital with real Binance balance | ✅ Fixed — startup reads last `capital_snapshots` record first (`restore_from_snapshot()`). Real balance sync only on first boot (no snapshot). `synced_from_real=True` blocks background sync from overwriting. |
| 18 | Historical trades stuck as "open" in DB (SAGA ×8, ZEC, SOL, DOGE) | DB close wasn't being called reliably due to old code bug | ✅ Fixed manually via SQL — ROW_NUMBER() for SAGA batch, direct UPDATE for ZEC/SOL/DOGE with exact Telegram P&L values |
| 19 | Missing index on `exchange_order_id` — close_trade_by_order_id() scans all chunks | No index → full TimescaleDB hypertable scan on every paper trade close | ✅ Fixed — migration 002_add_indexes.sql: `idx_trades_order_id` + `idx_trades_open_paper` |

### User Decision: Settings Freeze
**Decided 2026-05-14 — User will NOT change any bot settings for 2 weeks.**
Items frozen: pre-filter threshold (0.30%), scan top-N (25), target_profit_pct, stop_loss_pct, confidence threshold (65), scan interval (30s).
Reason: Need baseline data before tuning — changing variables before data is collected invalidates the learning period.
Review date: **2026-05-28** — after first weekly learning report and 14-day paper baseline.

### Next Phase: Grid Trading (Phase B)
- Spot grid: Claude activates when coin is ranging, sets price range + levels via ATR
- Futures grid: same but uses long/short positions, earns funding rate
- Max 5 spot grids + 5 futures grids simultaneously
- Paper grid emulation: separate from real grids, own DB tables
- Telegram commands: /grid list, /grid stop SYMBOL
- DB tables: grid_sessions, grid_orders (separate from trades table)
- Capital allocation: separate budget from momentum trading

### Next Phase: Claude News Analysis (Phase C)
- Claude Haiku reads last 24h CMC/crypto headlines each cycle
- Returns sentiment (-1 to +1) + affected coins
- Feeds into trading decision as additional signal (weight ~0.10)
- Cost: minimal (Haiku is cheap, runs once per cycle not per coin)

### Monitoring
1. **Run `/report` daily** to watch win rate and P&L accumulate
2. **Check Sunday 2026-05-18 00:00 UTC** — first weekly learning report fires
3. **After 14+ days profitable paper trading** → flip `PAPER_TRADING=false`
4. **Watch first paper trade** fire on Telegram (market needs to move > 0.30%)
5. **Check Sunday learning report** — first auto-run 2026-05-18 00:00 UTC
6. **Monitor daily** via `/report` on Telegram
7. **When win rate ≥ 60% over 14+ days** → flip `PAPER_TRADING=false` in VPS `.env` and restart

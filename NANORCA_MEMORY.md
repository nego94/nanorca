# NANORCA — Memory & Progression Document
> **Purpose:** Single source of truth for project state. Update this file after every significant change.
> **Owner:** Nego (abetnego.kristiawan@gmail.com)
> **Last updated:** 2026-05-14

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
> **Last updated:** 2026-05-14 (VPS deployment day — Hostinger KVM2)

---

## 2. Current Phase

### Phase: 1 — Paper Trading (LOCAL)
**Status:** Running on owner's laptop via Docker Desktop

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
1. Scans top-25 Binance USDT futures pairs by volume every 60 seconds
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

Every 60 seconds:
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
| `/report` | All | Today's P&L, win rate, trade count |
| `/history` | All | Last 10 closed trades |
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
SCAN_INTERVAL_SECONDS=60        # Phase 2 lean: use 90-120
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

## 14. VPS Setup (When Ready)

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

---

## 17. Current Status & Next Actions

**Bot status as of 2026-05-14:** Running on VPS with all fixes deployed. Paper trading active.

1. **Monitor for first paper trade** — bot will broadcast `📋 PLANNED`, `✅ FILLED`, then `🎉/❌/⏰` on close
2. **Check Grafana trade history** — will populate once first trade closes (PLANNED→FILLED→CLOSED lifecycle)
3. **Run `/report` daily** to watch win rate and P&L accumulate
4. **Check Sunday 2026-05-18 00:00 UTC** — first weekly learning report fires
5. **After 14+ days of profitable paper trading** → flip `PAPER_TRADING=false` on VPS and restart
4. **Watch first paper trade** fire on Telegram (market needs to move > 0.30%)
5. **Check Sunday learning report** — first auto-run 2026-05-18 00:00 UTC
6. **Monitor daily** via `/report` on Telegram
7. **When win rate ≥ 60% over 14+ days** → flip `PAPER_TRADING=false` in VPS `.env` and restart

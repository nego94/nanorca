# NANORCA — Autonomous Trading Bot

> **Python AI brain. Go hot paths. Zero sleep.**

An autonomous multi-exchange trading bot running on Binance, Polymarket, and Hyperliquid — powered by Claude AI as decision brain, with Go handling speed-critical market data and order execution.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Network                       │
│                                                         │
│  ┌──────────────────────┐  gRPC  ┌──────────────────┐  │
│  │   Python Bot (brain) │◄──────►│  Go Executor     │  │
│  │                      │        │  (hot paths)      │  │
│  │  • Claude AI brain   │        │  • WS feeds       │  │
│  │  • Risk manager      │        │  • Order exec     │  │
│  │  • Telegram bot      │        │  • Market scan    │  │
│  │  • Learning loop     │        │  • Signal agg.    │  │
│  │  • Prometheus        │        │                   │  │
│  └──────────────────────┘        └──────────────────┘  │
│            │                             │              │
│     asyncpg│                     SQL (Go)│              │
│            ▼                             ▼              │
│  ┌──────────────────────────────────────────────────┐  │
│  │          PostgreSQL 15 + TimescaleDB             │  │
│  └──────────────────────────────────────────────────┘  │
│            │                                            │
│  ┌─────────▼─────────┐   ┌────────────────────────┐   │
│  │     Prometheus    │──►│       Grafana           │   │
│  └───────────────────┘   └────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Clone & configure
```bash
git clone <your-repo> nanorca
cd nanorca
cp .env.example .env
# Edit .env — fill in all values
```

### 2. Generate protobuf files
```bash
make proto
```

### 3. Start in paper mode (always first!)
```bash
./scripts/paper_mode.sh
# or:
docker-compose up -d
```

### 4. Check it's alive
```bash
# Telegram — message your bot:
/status

# Grafana dashboard:
http://YOUR_VPS_IP:3000

# Health check:
curl http://localhost:8081/health
```

---

## Build Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation — DB, config, Telegram `/status` | 🔲 |
| 2 | Data — exchange connections, market scanner | 🔲 |
| 3 | Brain — signals + Claude decisions (paper) | 🔲 |
| 4 | Safety — risk manager, circuit breaker, alerts | 🔲 |
| 5 | Go live — only after 14+ days profitable paper | 🔲 |
| 6 | Self-improvement — weekly learning loop | 🔲 |

---

## Directory Structure

```
nanorca/
├── bot/           # Python service (AI brain, Telegram, learning)
├── executor/      # Go service (market feeds, order execution)
├── migrations/    # SQL schema files
├── grafana/       # Dashboard config
├── prometheus/    # Metrics config
├── scripts/       # VPS setup + deploy
├── docker-compose.yml
├── .env.example
└── Makefile
```

## Safety Rules (Non-Negotiable)

- ⛔ Never set `PAPER_TRADING=false` before 14 days of profitable paper trading
- ⛔ Never commit `.env` to git
- ⛔ Never enable Withdraw on Binance API key
- ⛔ Never expose PostgreSQL port externally
- ⛔ Never exceed 3x leverage on Binance

---

*NANORCA v1.0 — See `NANORCA_OWNERS_GUIDE.md` for owner documentation.*

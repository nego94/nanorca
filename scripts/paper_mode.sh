#!/usr/bin/env bash
# scripts/paper_mode.sh — Start NANORCA in paper trading mode.
# This is the SAFE way to start. Always use this first.
set -euo pipefail

echo "📄 Starting NANORCA in PAPER TRADING mode..."
echo "No real orders will be placed."
echo ""

# Force paper mode regardless of .env setting
export PAPER_TRADING=true
export BINANCE_TESTNET=true
export HYPERLIQUID_TESTNET=true

docker compose up -d

echo ""
echo "✅ NANORCA running in paper mode."
echo "📊 Grafana:     http://localhost:3000"
echo "📡 Metrics:     http://localhost:8080/metrics"
echo "💬 Telegram:    message your bot /status"
echo ""
echo "Logs: docker compose logs -f bot"

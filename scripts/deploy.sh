#!/usr/bin/env bash
# scripts/deploy.sh — Pull latest code and restart containers.
# Run on VPS after pushing new code to git.
set -euo pipefail

echo "🚀 Deploying NANORCA update..."
git pull origin main

# Rebuild only changed services (--no-deps skips dependency chain)
docker compose build bot executor
docker compose up -d --no-deps bot executor

echo "✅ Deploy complete. Watching logs (Ctrl+C to exit):"
docker compose logs -f bot executor

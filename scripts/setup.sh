#!/usr/bin/env bash
# scripts/setup.sh — One-command VPS setup for NANORCA
# Run once as root on a fresh Ubuntu 22.04 VPS.
# Usage: bash setup.sh
set -euo pipefail

echo "🚀 NANORCA VPS Setup starting..."

# ── System updates ─────────────────────────────────────────────────────────
apt-get update -qq && apt-get upgrade -y -qq

# ── Docker ────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
fi

# ── Docker Compose ────────────────────────────────────────────────────────
if ! command -v docker compose &>/dev/null; then
  echo "Installing Docker Compose..."
  apt-get install -y docker-compose-plugin
fi

# ── Go (for protoc code generation) ──────────────────────────────────────
if ! command -v go &>/dev/null; then
  echo "Installing Go 1.22..."
  wget -q https://go.dev/dl/go1.22.3.linux-amd64.tar.gz
  tar -C /usr/local -xzf go1.22.3.linux-amd64.tar.gz
  rm go1.22.3.linux-amd64.tar.gz
  echo 'export PATH=$PATH:/usr/local/go/bin' >> /etc/profile
  export PATH=$PATH:/usr/local/go/bin
fi

# ── protoc + plugins ──────────────────────────────────────────────────────
if ! command -v protoc &>/dev/null; then
  echo "Installing protoc..."
  apt-get install -y protobuf-compiler
  go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
  go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
  pip3 install grpcio-tools
fi

# ── UFW Firewall ──────────────────────────────────────────────────────────
ufw --force enable
ufw allow 22     comment "SSH"
ufw allow 3000   comment "Grafana"
ufw allow 8080   comment "Prometheus metrics (restrict in prod)"
ufw allow 8081   comment "Health check"
echo "UFW rules applied"

# ── Create logs directory ─────────────────────────────────────────────────
mkdir -p /opt/nanorca/logs
echo "✅ VPS setup complete!"
echo ""
echo "Next steps:"
echo "  1. Clone your repo to /opt/nanorca"
echo "  2. cp .env.example .env && fill in all values"
echo "  3. make proto"
echo "  4. docker compose up -d"

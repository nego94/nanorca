"""
bot/monitoring/prometheus_exporter.py — Exposes /metrics on port 8080.

All metrics defined in the spec are implemented here.
Grafana reads from Prometheus which scrapes this endpoint every 15s.
"""
from __future__ import annotations

import logging
import threading
from typing import Any
from wsgiref.simple_server import make_server

from prometheus_client import (
    Counter, Gauge, Histogram,
    make_wsgi_app, REGISTRY,
)

log = logging.getLogger("nanorca.monitoring")


class PrometheusExporter:
    """Registers and updates all Prometheus metrics for the NANORCA bot."""

    def __init__(self, config) -> None:
        self._config = config

        # ── Counters ──────────────────────────────────────────────────────
        self.trades_total = Counter(
            "nanorca_trades_total", "Total trades placed",
            ["exchange", "paper"],
        )
        self.trades_won_total = Counter(
            "nanorca_trades_won_total", "Total winning trades",
            ["exchange"],
        )
        self.trades_lost_total = Counter(
            "nanorca_trades_lost_total", "Total losing trades",
            ["exchange"],
        )
        self.scan_errors_total = Counter(
            "nanorca_scan_errors_total", "Market scan errors"
        )
        self.claude_errors_total = Counter(
            "nanorca_claude_errors_total", "Claude API errors"
        )
        self.skips_total = Counter(
            "nanorca_skips_total", "Cycles skipped due to low confidence or no signal"
        )

        # ── Gauges ────────────────────────────────────────────────────────
        self.capital_current_usd = Gauge(
            "nanorca_capital_current_usd", "Current total capital in USD"
        )
        self.capital_starting_usd = Gauge(
            "nanorca_capital_starting_usd", "Starting capital in USD"
        )
        self.capital_pct_change = Gauge(
            "nanorca_capital_pct_change", "% change from starting capital"
        )
        self.daily_pnl_usd = Gauge(
            "nanorca_daily_pnl_usd", "Today's P&L in USD"
        )
        self.open_positions_count = Gauge(
            "nanorca_open_positions_count", "Current open positions"
        )
        self.confidence_last_trade = Gauge(
            "nanorca_confidence_last_trade", "Confidence score of last executed trade"
        )
        self.win_rate_24h = Gauge(
            "nanorca_win_rate_24h", "Rolling 24h win rate (0–1)"
        )
        self.win_rate_7d = Gauge(
            "nanorca_win_rate_7d", "Rolling 7d win rate (0–1)"
        )
        self.bot_state = Gauge(
            "nanorca_bot_state",
            "Bot state: 0=running,1=paused_manual,2=paused_floor,3=paused_daily,4=paused_circuit"
        )

        # Exchange-specific gauges
        for exchange in ("binance", "polymarket", "hyperliquid"):
            Gauge(f"nanorca_{exchange}_pnl_usd", f"Cumulative P&L for {exchange}")

        # ── Histograms ────────────────────────────────────────────────────
        self.trade_hold_duration = Histogram(
            "nanorca_trade_hold_duration_minutes",
            "Distribution of trade hold times in minutes",
            buckets=[1, 5, 10, 30, 60, 120, 240, 480, 1440],
        )
        self.trade_pnl = Histogram(
            "nanorca_trade_pnl_usd",
            "Distribution of P&L per trade",
            buckets=[-50, -20, -10, -5, 0, 5, 10, 20, 50, 100],
        )
        self.signal_confidence = Histogram(
            "nanorca_signal_confidence_score",
            "Distribution of confidence scores",
            buckets=list(range(0, 110, 10)),
        )

        # ── Seed starting capital gauge ───────────────────────────────────
        self.capital_starting_usd.set(config.starting_capital_usd)
        self.capital_current_usd.set(config.starting_capital_usd)

    def start_server(self, port: int = 8080) -> None:
        """Start the Prometheus /metrics HTTP server in a background thread."""
        app = make_wsgi_app()

        def _serve():
            httpd = make_server("0.0.0.0", port, app)
            log.info(f"Prometheus metrics server on :{port}")
            httpd.serve_forever()

        t = threading.Thread(target=_serve, daemon=True, name="prometheus_metrics")
        t.start()

    # ── Update methods called by main loop ────────────────────────────────

    def record_trade(self, decision: dict[str, Any], result: dict[str, Any]) -> None:
        exchange = decision.get("exchange", "unknown")
        paper = str(result.get("paper", True)).lower()
        self.trades_total.labels(exchange=exchange, paper=paper).inc()
        confidence = decision.get("confidence", 0)
        self.confidence_last_trade.set(confidence)
        self.signal_confidence.observe(confidence)

    def record_trade_closed(self, exchange: str, won: bool, pnl: float, hold_minutes: float) -> None:
        if won:
            self.trades_won_total.labels(exchange=exchange).inc()
        else:
            self.trades_lost_total.labels(exchange=exchange).inc()
        self.trade_pnl.observe(pnl)
        self.trade_hold_duration.observe(hold_minutes)

    def update_capital(self, current: float, starting: float, daily_pnl: float) -> None:
        self.capital_current_usd.set(current)
        self.capital_pct_change.set((current - starting) / starting * 100)
        self.daily_pnl_usd.set(daily_pnl)

    def update_bot_state(self, state_value: str) -> None:
        state_map = {
            "running": 0,
            "paused_manual": 1,
            "paused_floor_hit": 2,
            "paused_daily_loss": 3,
            "paused_consecutive": 4,
        }
        self.bot_state.set(state_map.get(state_value, 0))

    def record_scan_error(self) -> None:
        self.scan_errors_total.inc()

    def record_claude_error(self) -> None:
        self.claude_errors_total.inc()

    def record_skip(self) -> None:
        self.skips_total.inc()

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

        # Per-exchange P&L gauges
        for exchange in ("binance", "polymarket", "hyperliquid"):
            Gauge(f"nanorca_{exchange}_pnl_usd", f"Cumulative P&L for {exchange}")

        # Real exchange balances — updated every cycle via GetBalances gRPC call
        self.exchange_balance_usd = Gauge(
            "nanorca_exchange_balance_usd",
            "Real account balance per exchange in USD",
            ["exchange"],
        )
        self.exchange_balance_available = Gauge(
            "nanorca_exchange_balance_available",
            "1 if exchange balance was fetched successfully, 0 if error",
            ["exchange"],
        )
        self.exchange_balance_total_usd = Gauge(
            "nanorca_exchange_balance_total_usd",
            "Total real balance across all exchanges in USD",
        )

        # Three-bucket capital breakdown
        # tradeable = USDT/stablecoins — what bot uses as futures margin
        # locked    = other coins in spot wallet — can't be used directly as futures collateral
        # inflight  = capital tied up in open positions right now
        self.capital_tradeable_usd = Gauge(
            "nanorca_capital_tradeable_usd",
            "Stablecoin (USDT) balance — usable as futures margin",
            ["exchange"],
        )
        self.capital_locked_usd = Gauge(
            "nanorca_capital_locked_usd",
            "Non-stablecoin coin value — locked, not usable as futures margin directly",
            ["exchange"],
        )
        self.capital_tradeable_total_usd = Gauge(
            "nanorca_capital_tradeable_total_usd",
            "Total tradeable USDT across all exchanges",
        )
        self.capital_locked_total_usd = Gauge(
            "nanorca_capital_locked_total_usd",
            "Total locked (non-stablecoin) value across all exchanges",
        )
        self.trading_mode = Gauge(
            "nanorca_paper_mode",
            "1 if paper trading, 0 if live",
        )
        self.trading_mode.set(1 if config.paper_trading else 0)

        # Seed per-exchange labels so they appear from startup (even before first fetch)
        for ex in ("binance", "hyperliquid", "polymarket"):
            self.exchange_balance_usd.labels(exchange=ex).set(0)
            self.exchange_balance_available.labels(exchange=ex).set(0)
            self.capital_tradeable_usd.labels(exchange=ex).set(0)
            self.capital_locked_usd.labels(exchange=ex).set(0)
        self.exchange_balance_total_usd.set(0)
        self.capital_tradeable_total_usd.set(0)
        self.capital_locked_total_usd.set(0)

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

        # ── Signal metrics (live — updated every cycle) ───────────────────
        self.signal_normalized = Gauge(
            "nanorca_signal_normalized",
            "Normalized signal value 0.0–1.0 (0.5=neutral)",
            ["signal"],
        )
        self.signal_fired = Gauge(
            "nanorca_signal_fired",
            "1 if signal exceeded its fire threshold this cycle, 0 otherwise",
            ["signal"],
        )
        self.market_snapshots_last = Gauge(
            "nanorca_market_snapshots_last",
            "Number of market snapshots returned in the last scan",
        )

        # ── Claude decision metrics ────────────────────────────────────────
        self.claude_last_action = Gauge(
            "nanorca_claude_last_action",
            "Last Claude action: 0=skip, 1=buy, 2=sell",
        )
        self.claude_last_confidence = Gauge(
            "nanorca_claude_last_confidence",
            "Confidence score of Claude's last decision (0–100)",
        )
        self.claude_cycles_total = Counter(
            "nanorca_claude_cycles_total",
            "Total number of Claude decision cycles",
        )

        # Seed signal labels so they appear from startup
        for sig in ("binance_momentum", "funding_rate_hyperliquid",
                    "price_gap_polymarket", "volume_spike"):
            self.signal_normalized.labels(signal=sig).set(0.5)
            self.signal_fired.labels(signal=sig).set(0)
        self.market_snapshots_last.set(0)
        self.claude_last_action.set(0)
        self.claude_last_confidence.set(0)

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

    def update_signals(self, signals: dict, snapshot_count: int) -> None:
        """Update live signal metrics. Called every trading cycle."""
        self.market_snapshots_last.set(snapshot_count)
        for key in ("binance_momentum", "funding_rate_hyperliquid",
                    "price_gap_polymarket", "volume_spike"):
            s = signals.get(key, {})
            self.signal_normalized.labels(signal=key).set(s.get("normalized", 0.5))
            self.signal_fired.labels(signal=key).set(1 if s.get("fired") else 0)

    def update_claude_decision(self, action: str, confidence: int) -> None:
        """Update Claude decision metrics after each cycle."""
        self.claude_cycles_total.inc()
        self.claude_last_action.set({"skip": 0, "buy": 1, "sell": 2}.get(action, 0))
        self.claude_last_confidence.set(confidence)

    def update_exchange_balances(self, balances: list[dict]) -> None:
        """
        Update real exchange balance metrics. Called every trading cycle.

        If balances is empty (executor unreachable), skip the update entirely
        so Grafana keeps the last known value instead of dropping to 0.

        Three-bucket model:
          tradeable = usdt field (stablecoin — usable as futures margin)
          locked    = total_usd - usdt (other coins — cannot be futures collateral directly)
          inflight  = tracked separately via open_positions_count
        """
        if not balances:
            return  # executor down — preserve last known metrics rather than zeroing out
        total_portfolio = 0.0
        total_tradeable = 0.0
        total_locked = 0.0
        for b in balances:
            ex = b.get("exchange", "unknown")
            available = b.get("available", False)
            tradeable = b.get("usdt", 0.0) if available else 0.0       # stablecoin only
            total_usd = b.get("total_usd", 0.0) if available else 0.0  # full portfolio
            locked = max(0.0, total_usd - tradeable)                    # other coins

            self.exchange_balance_usd.labels(exchange=ex).set(tradeable)
            self.exchange_balance_available.labels(exchange=ex).set(1.0 if available else 0.0)
            self.capital_tradeable_usd.labels(exchange=ex).set(tradeable)
            self.capital_locked_usd.labels(exchange=ex).set(locked)

            total_portfolio += total_usd
            total_tradeable += tradeable
            total_locked += locked

        self.exchange_balance_total_usd.set(total_portfolio)
        self.capital_tradeable_total_usd.set(total_tradeable)
        self.capital_locked_total_usd.set(total_locked)

    def record_scan_error(self) -> None:
        self.scan_errors_total.inc()

    def record_claude_error(self) -> None:
        self.claude_errors_total.inc()

    def record_skip(self) -> None:
        self.skips_total.inc()

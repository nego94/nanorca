"""
bot/main.py — NANORCA entry point.

Starts the full bot system:
  1. Validates config
  2. Connects to DB
  3. Connects to Go executor via gRPC
  4. Starts Telegram bot
  5. Starts Prometheus metrics server
  6. Runs the main trading loop via APScheduler
  7. Handles graceful shutdown

Architecture: asyncio throughout. The main trading cycle runs every
SCAN_INTERVAL_SECONDS via APScheduler. Exchange I/O goes through the
Go executor service via gRPC. All decisions go through the Claude brain.

Pre-filter thresholds (before Claude is called — saves 60-70% API cost on quiet days):
  _PREFILTER_MOMENTUM_PCT  : price moved >X% in rolling window → call Claude
  _PREFILTER_VOLUME_RATIO  : volume >X× baseline → call Claude
  _PREFILTER_FUNDING_RATE  : funding rate |X| → call Claude
  _MIN_GROSS_MOVE_PCT      : minimum expected price move to cover fees (0.04% round-trip + 0.05% profit target)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config import config
from data.db import Database
from alerts.telegram_bot import TelegramBot
from monitoring.prometheus_exporter import PrometheusExporter
from risk.circuit_breaker import CircuitBreaker, BotState
from risk.capital_tracker import CapitalTracker
from risk.risk_manager import RiskManager
from brain.claude_brain import ClaudeBrain
from brain.signal_builder import SignalBuilder
from brain.confidence_scorer import ConfidenceScorer
from execution.order_router import OrderRouter
from learning.outcome_logger import OutcomeLogger
from scheduler import build_scheduler

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/nanorca.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("nanorca.main")

# Stop-loss and max hold time applied to every open position each cycle.
_DEFAULT_STOP_LOSS_PCT = 2.0       # close if position is down 2%
_MAX_HOLD_MINUTES      = 240       # close if held more than 4 hours

# ── Pre-filter thresholds (before Claude API call) ────────────────────────────
# At least ONE must trigger; otherwise the market is too quiet to call Claude.
_PREFILTER_MOMENTUM_PCT = 0.30    # price moved >0.30% in rolling window
_PREFILTER_VOLUME_RATIO = 1.20    # current volume >1.20× EMA baseline
_PREFILTER_FUNDING_RATE = 0.0001  # funding rate |x| > 0.01%
_PREFILTER_POLY_GAP     = 0.01    # Polymarket YES+NO gap > 1%

# Minimum gross price move to cover maker fees + target profit.
# Futures maker: 0.02% per side × 2 = 0.04% round-trip. Profit target: 0.05%.
# Total minimum: 0.09%. Below this, the trade loses money after fees.
_MIN_GROSS_MOVE_PCT = 0.09

# Graduated confidence hard skip — below this, never trade regardless of signals.
_CONFIDENCE_HARD_SKIP = 55


def _prefilter_should_skip(signals: dict) -> tuple[bool, str]:
    """
    Returns (should_skip, reason).
    At least one raw signal must cross its pre-filter threshold before calling Claude.
    This saves 60-70% of Claude API cost on quiet market days.
    """
    momentum_pct = abs(signals.get("binance_momentum", {}).get("raw_value", 0.0))
    if momentum_pct >= _PREFILTER_MOMENTUM_PCT:
        return False, f"binance_momentum={momentum_pct:.3f}%"

    volume_ratio = signals.get("volume_spike", {}).get("raw_value", 0.0)
    if volume_ratio >= _PREFILTER_VOLUME_RATIO:
        return False, f"volume_spike={volume_ratio:.2f}×"

    funding_raw = abs(signals.get("funding_rate_hyperliquid", {}).get("raw_value", 0.0))
    if funding_raw >= _PREFILTER_FUNDING_RATE:
        return False, f"funding_rate={funding_raw:.6f}"

    poly_gap = signals.get("price_gap_polymarket", {}).get("raw_value", 0.0)
    if poly_gap >= _PREFILTER_POLY_GAP:
        return False, f"poly_gap={poly_gap:.3f}"

    return True, "all signals below pre-filter thresholds (market quiet)"


async def _manage_open_positions(
    order_router, outcome_logger, capital_tracker, telegram, metrics,
    market_snapshots: list,
) -> None:
    """
    Check every open position each cycle.
    Closes automatically on stop-loss hit or max hold time exceeded.
    """
    import time as _time

    positions = await order_router.get_positions()
    if not positions:
        metrics.open_positions_count.set(0)
        return

    metrics.open_positions_count.set(len(positions))

    # Build a quick price lookup from the snapshots we just fetched
    price_map: dict[str, float] = {
        s["market"]: s["price"]
        for s in market_snapshots
        if s.get("price", 0) > 0
    }

    now_ms = int(_time.time() * 1000)

    for pos in positions:
        order_id = pos["exchange_order_id"]
        market   = pos["market"]
        exchange = pos["exchange"]
        entry    = pos.get("entry_price", 0)
        side     = pos.get("side", "BUY").upper()
        opened   = pos.get("opened_at_ms", now_ms)

        current_price = price_map.get(market, 0)
        if current_price <= 0 or entry <= 0:
            continue

        # P&L % from entry (positive = winning)
        if side in ("BUY", "LONG"):
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100

        hold_min = (now_ms - opened) / 60_000
        stop_hit = pnl_pct <= -_DEFAULT_STOP_LOSS_PCT
        time_out = hold_min >= _MAX_HOLD_MINUTES

        if not (stop_hit or time_out):
            continue

        reason = "stop-loss" if stop_hit else f"max-hold ({hold_min:.0f}m)"
        log.info(f"Auto-closing {market} {side}: {reason} | pnl={pnl_pct:+.2f}%")

        try:
            close = await order_router.close_position(order_id, exchange, market)
            exit_price = close.get("exit_price", current_price)
            pnl_usd    = close.get("pnl_usd", 0.0)
            fees_usd   = close.get("fees_usd", 0.0)

            await outcome_logger.log_trade_closed(order_id, exit_price, pnl_usd, fees_usd)
            await capital_tracker.update_from_trade({
                "pnl_usd": pnl_usd,
                "fees_usd": fees_usd,
            })

            emoji = "✅" if pnl_usd >= 0 else "❌"
            await telegram.send_info(
                f"{emoji} [PAPER] Position closed — {reason}\n"
                f"{exchange.upper()} {market}\n"
                f"Entry: ${entry:.4f} → Exit: ${exit_price:.4f}\n"
                f"P&L: ${pnl_usd:+.4f} | Hold: {hold_min:.0f}m"
            )
            metrics.record_trade_closed(exchange, pnl_usd >= 0, pnl_usd, hold_min)
        except Exception as e:
            log.error(f"Auto-close failed for {order_id}: {e}")


async def main_loop(
    db: Database,
    circuit_breaker: CircuitBreaker,
    capital_tracker: CapitalTracker,
    risk_manager: RiskManager,
    signal_builder: SignalBuilder,
    claude_brain: ClaudeBrain,
    confidence_scorer: ConfidenceScorer,
    order_router: OrderRouter,
    outcome_logger: OutcomeLogger,
    telegram: TelegramBot,
    metrics: PrometheusExporter,
) -> None:
    """
    Main trading cycle — runs every SCAN_INTERVAL_SECONDS.

    Decision tree:
      1. Is bot paused?                          → sleep, retry
      2. Capital dropped > CAPITAL_FLOOR_PCT?    → alert + pause forever
      3. Daily loss > MAX_DAILY_LOSS_PCT?        → pause until midnight
      4. Scan all markets via Go executor
     4b. Auto-close open positions (stop-loss / max hold time)
      5. Build signal dict
      6. Send to Claude → get confidence + decision JSON
      7. confidence < CONFIDENCE_THRESHOLD?      → log skip
      8. Risk manager size check
      9. Execute trade (paper or live)
     10. Log to DB, update Prometheus metrics
    """
    log.info("=== NANORCA main cycle start ===")

    # ── Step 1: Check bot state ────────────────────────────────────────────
    if circuit_breaker.state != BotState.RUNNING:
        log.info(f"Bot is {circuit_breaker.state.value} — skipping cycle")
        return

    # ── Step 2: Capital floor check (ALWAYS FIRST) ─────────────────────────
    floor_hit = await capital_tracker.check_floor()
    if floor_hit:
        log.critical("Capital floor hit — triggering emergency stop")
        await circuit_breaker.trigger_floor_hit()
        await order_router.close_all_positions()
        await telegram.send_floor_alert(capital_tracker)
        return

    # ── Step 3: Daily loss check ───────────────────────────────────────────
    if await capital_tracker.daily_loss_exceeded():
        log.warning("Daily loss cap reached — pausing until midnight")
        await circuit_breaker.pause_daily_loss()
        await telegram.send_warning("⚠️ Daily loss cap hit. Trading paused until midnight UTC.")
        return

    # ── Step 3b: Trading plan drawdown rules ──────────────────────────────
    if capital_tracker.day_stopped:
        log.info("Trading plan: day stopped due to drawdown rule")
        return

    drawdown_action = await capital_tracker.check_drawdown_rules(risk_manager)
    if drawdown_action in ("stop_day", "hard_stop", "emergency"):
        await telegram.send_warning(f"⚠️ Drawdown rule: {drawdown_action} — trading halted for today")
        return

    # ── Step 4: Scan markets + fetch real balances via Go executor ────────
    try:
        market_snapshots = await order_router.scan_markets(config.priority_markets)
    except Exception as e:
        log.error(f"Market scan failed: {e}")
        metrics.record_scan_error()
        return

    # Update real exchange balance metrics for Grafana (non-fatal)
    try:
        balances = await order_router.get_balances()
        metrics.update_exchange_balances(balances)
    except Exception as e:
        log.debug(f"Balance fetch skipped: {e}")

    if not market_snapshots:
        log.warning("No market snapshots returned — skipping cycle")
        return

    # ── Step 5: Build signal dict ─────────────────────────────────────────
    try:
        signal_weights = await db.get_signal_weights()
        signals = await signal_builder.build(market_snapshots, signal_weights)
    except Exception as e:
        log.error(f"Signal build failed: {e}")
        return

    # ── Step 4b: Auto-close open positions (stop-loss + max hold time) ────
    await _manage_open_positions(
        order_router, outcome_logger, capital_tracker, telegram, metrics,
        market_snapshots,
    )

    # Push live signal values to Prometheus so Grafana shows what's happening
    metrics.update_signals(signals, len(market_snapshots))

    # ── Step 5b: Pre-filter — skip Claude if market is too quiet ──────────
    # Saves 60-70% of Claude API cost. At least one signal must cross its threshold.
    should_skip, prefilter_reason = _prefilter_should_skip(signals)
    if should_skip:
        log.info(f"Pre-filter: market quiet — {prefilter_reason} — skipping Claude call")
        metrics.record_skip()
        return
    log.debug(f"Pre-filter passed: {prefilter_reason}")

    # ── Step 5c: Minimum gross move check (fee break-even gate) ──────────
    # Futures maker fee: 0.04% round-trip. Profit target: 0.05%. Total needed: 0.09%.
    # If best momentum across all markets is below this, no trade can be profitable.
    momentum_pct = abs(signals.get("binance_momentum", {}).get("raw_value", 0.0))
    if momentum_pct < _MIN_GROSS_MOVE_PCT:
        log.info(
            f"MIN_GROSS_MOVE: momentum={momentum_pct:.3f}% < {_MIN_GROSS_MOVE_PCT}% "
            f"— trade can't cover fees, skip"
        )
        metrics.record_skip()
        return

    # ── Step 6: Ask Claude for a decision ─────────────────────────────────
    try:
        performance_ctx = await db.get_performance_context()
        decision = await claude_brain.decide(signals, signal_weights, performance_ctx)
    except Exception as e:
        log.error(f"Claude brain failed: {e}")
        metrics.record_claude_error()
        return

    # ── Step 7: Confidence check (graduated: <55 hard skip) ──────────────
    if decision is None or decision.get("action") == "skip":
        confidence = decision.get("confidence", 0) if decision else 0
        log.info(f"Claude skipped — confidence={confidence}")
        metrics.update_claude_decision("skip", confidence)
        metrics.record_skip()
        return

    confidence = decision.get("confidence", 0)
    if confidence < _CONFIDENCE_HARD_SKIP:
        log.info(f"Confidence {confidence} < {_CONFIDENCE_HARD_SKIP} (hard skip) — skip")
        metrics.update_claude_decision("skip", confidence)
        metrics.record_skip()
        return

    # ── Step 8: Risk manager approval ─────────────────────────────────────
    approved, reason = await risk_manager.approve(decision, capital_tracker)
    if not approved:
        log.info(f"Risk manager rejected trade: {reason}")
        return

    # ── Step 9: Execute trade ─────────────────────────────────────────────
    try:
        trade_result = await order_router.place_order(decision, paper=config.paper_trading)
        paper_prefix = "[PAPER] " if config.paper_trading else ""
        log.info(
            f"{paper_prefix}Trade executed: {decision['exchange']} "
            f"{decision['market']} {decision['direction']} "
            f"size={decision['size_pct']}% confidence={confidence}"
        )
    except Exception as e:
        log.error(f"Order execution failed: {e}")
        await telegram.send_warning(f"⚠️ Order execution failed: {e}")
        return

    metrics.update_claude_decision(decision.get("action", "buy"), confidence)
    # ── Step 10: Log outcome and update metrics ────────────────────────────
    await outcome_logger.log_trade_opened(decision, trade_result)
    await capital_tracker.update_from_trade(trade_result)
    metrics.record_trade(decision, trade_result)

    log.info("=== NANORCA cycle complete ===")


async def run() -> None:
    """Bootstrap and run the full NANORCA system."""
    log.info("🚀 NANORCA starting up...")

    # ── Validate config ────────────────────────────────────────────────────
    try:
        config.validate()
    except (ValueError, AssertionError) as e:
        log.critical(f"Config validation failed: {e}")
        sys.exit(1)

    if config.paper_trading:
        log.warning("⚠️  PAPER TRADING MODE — no real orders will be placed")
    else:
        log.warning("🔴 LIVE TRADING MODE — real money at risk")

    # ── Connect DB ─────────────────────────────────────────────────────────
    db = Database(config.db_dsn)
    await db.connect()
    log.info("✅ Database connected")

    # ── Build services ─────────────────────────────────────────────────────
    capital_tracker    = CapitalTracker(db, config)
    circuit_breaker    = CircuitBreaker(db, config)
    risk_manager       = RiskManager(config, circuit_breaker, capital_tracker)
    signal_builder     = SignalBuilder(config)
    confidence_scorer  = ConfidenceScorer(config)
    claude_brain       = ClaudeBrain(config, confidence_scorer)
    order_router       = OrderRouter(config)
    outcome_logger     = OutcomeLogger(db)
    metrics            = PrometheusExporter(config)

    # ── Start Prometheus metrics server (port 8080) ────────────────────────
    metrics.start_server(port=8080)
    log.info("✅ Prometheus metrics server started on :8080")

    # ── Start Telegram bot ─────────────────────────────────────────────────
    telegram = TelegramBot(
        config=config,
        db=db,
        circuit_breaker=circuit_breaker,
        capital_tracker=capital_tracker,
        order_router=order_router,
    )
    await telegram.start()
    log.info("✅ Telegram bot started")

    # ── Connect to Go executor via gRPC ────────────────────────────────────
    await order_router.connect()
    log.info(f"✅ Go executor connected at {config.executor_grpc_addr}")

    # ── In paper mode: sync starting capital from real exchange balance ─────
    # This ensures trade sizing is based on what you actually have,
    # not the static STARTING_CAPITAL_USD config value.
    if config.paper_trading:
        try:
            real_balances = await order_router.get_balances()
            # Use usdt (stablecoin only) — this is the actual futures margin available.
            # total_usd includes other locked coins the bot can't use as futures collateral.
            real_tradeable = sum(b["usdt"] for b in real_balances if b.get("available") and b["usdt"] > 0)
            if real_tradeable > 0:
                capital_tracker.sync_from_real_balance(real_tradeable)
                metrics.update_capital(
                    capital_tracker.current_capital,
                    config.starting_capital_usd,
                    capital_tracker.daily_pnl,
                )
                real_total_usd = sum(b["total_usd"] for b in real_balances if b.get("available"))
                locked_usd = real_total_usd - real_tradeable
                log.info(
                    f"✅ Paper capital synced: tradeable=${real_tradeable:.2f} USDT "
                    f"| locked=${locked_usd:.2f} (other coins) "
                    f"| portfolio=${real_total_usd:.2f}"
                )
            else:
                log.info("ℹ️  No real balance available — using STARTING_CAPITAL_USD")
        except Exception as e:
            log.warning(f"Balance sync skipped: {e} — using STARTING_CAPITAL_USD")

    # ── Announce readiness ─────────────────────────────────────────────────
    await telegram.send_info(
        f"🟢 NANORCA online\n"
        f"Mode: {'📄 PAPER' if config.paper_trading else '🔴 LIVE'}\n"
        f"Capital: ${capital_tracker.current_capital:.2f}\n"
        f"Exchanges: {', '.join(sorted(config.enabled_exchanges))}\n"
        f"Binance scan: top-{getattr(config, 'binance_scan_top_n', 3)} USDT pairs\n"
        f"Scan interval: {config.scan_interval_seconds}s"
    )

    # ── APScheduler: main loop + daily report + weekly learning ───────────
    scheduler = build_scheduler(
        config=config,
        db=db,
        telegram=telegram,
        circuit_breaker=circuit_breaker,
        capital_tracker=capital_tracker,
        risk_manager=risk_manager,
        signal_builder=signal_builder,
        claude_brain=claude_brain,
        confidence_scorer=confidence_scorer,
        order_router=order_router,
        outcome_logger=outcome_logger,
        metrics=metrics,
    )
    scheduler.start()
    log.info("✅ Scheduler started — NANORCA fully operational")

    # ── Graceful shutdown ──────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal_handler(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    log.info("Shutting down NANORCA...")
    scheduler.shutdown(wait=False)
    await telegram.send_info("🔴 NANORCA shutting down.")
    await telegram.stop()
    await order_router.disconnect()
    await db.disconnect()
    log.info("NANORCA stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run())

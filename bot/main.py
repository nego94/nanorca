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

    # ── Step 4: Scan markets via Go executor ──────────────────────────────
    try:
        market_snapshots = await order_router.scan_markets(config.priority_markets)
    except Exception as e:
        log.error(f"Market scan failed: {e}")
        metrics.record_scan_error()
        return

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

    # ── Step 6: Ask Claude for a decision ─────────────────────────────────
    try:
        performance_ctx = await db.get_performance_context()
        decision = await claude_brain.decide(signals, signal_weights, performance_ctx)
    except Exception as e:
        log.error(f"Claude brain failed: {e}")
        metrics.record_claude_error()
        return

    # ── Step 7: Confidence check ──────────────────────────────────────────
    if decision is None or decision.get("action") == "skip":
        log.info(f"Claude skipped — confidence={decision.get('confidence', 0) if decision else 'N/A'}")
        metrics.record_skip()
        return

    confidence = decision.get("confidence", 0)
    if confidence < config.confidence_threshold:
        log.info(f"Confidence {confidence} below threshold {config.confidence_threshold} — skip")
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

    # ── Announce readiness ─────────────────────────────────────────────────
    await telegram.send_info(
        f"🟢 NANORCA online\n"
        f"Mode: {'📄 PAPER' if config.paper_trading else '🔴 LIVE'}\n"
        f"Capital: ${config.starting_capital_usd}\n"
        f"Markets: {', '.join(config.priority_markets)}\n"
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

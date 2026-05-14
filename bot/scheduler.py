"""
bot/scheduler.py — APScheduler configuration.

Three scheduled jobs:
  1. main_cycle     — every SCAN_INTERVAL_SECONDS (default: 60s)
  2. daily_report   — every day at 00:00 owner's timezone (Asia/Makassar)
  3. weekly_learner — every Sunday at 00:00 UTC
"""
from __future__ import annotations

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("nanorca.scheduler")


def build_scheduler(
    config,
    db,
    telegram,
    circuit_breaker,
    capital_tracker,
    risk_manager,
    signal_builder,
    claude_brain,
    confidence_scorer,
    order_router,
    outcome_logger,
    metrics,
    suggestion_store=None,
) -> AsyncIOScheduler:
    """Build and return the configured APScheduler instance (not yet started)."""
    from main import main_loop
    from alerts.daily_report import send_daily_report
    from learning.weekly_learner import WeeklyLearner

    weekly_learner = WeeklyLearner(db=db, claude_brain=claude_brain, telegram=telegram, config=config)

    scheduler = AsyncIOScheduler(timezone="UTC")

    # ── Job 1: Main trading cycle ─────────────────────────────────────────
    scheduler.add_job(
        main_loop,
        trigger=IntervalTrigger(seconds=config.scan_interval_seconds),
        id="main_cycle",
        name="NANORCA Main Trading Cycle",
        max_instances=1,           # Never run two cycles simultaneously
        coalesce=True,             # Skip missed cycles instead of stacking
        misfire_grace_time=10,
        kwargs=dict(
            db=db,
            circuit_breaker=circuit_breaker,
            capital_tracker=capital_tracker,
            risk_manager=risk_manager,
            signal_builder=signal_builder,
            claude_brain=claude_brain,
            confidence_scorer=confidence_scorer,
            order_router=order_router,
            outcome_logger=outcome_logger,
            telegram=telegram,
            metrics=metrics,
            suggestion_store=suggestion_store,
        ),
    )

    # ── Job 2: Daily report — midnight owner timezone ─────────────────────
    scheduler.add_job(
        send_daily_report,
        trigger=CronTrigger(hour=0, minute=0, timezone=config.bot_timezone),
        id="daily_report",
        name="NANORCA Daily Report",
        max_instances=1,
        kwargs=dict(db=db, telegram=telegram, capital_tracker=capital_tracker),
    )

    # ── Job 3: Weekly learning — Sunday 00:00 UTC ─────────────────────────
    scheduler.add_job(
        weekly_learner.run,
        trigger=CronTrigger(day_of_week="sun", hour=0, minute=0, timezone="UTC"),
        id="weekly_learner",
        name="NANORCA Weekly Self-Learning",
        max_instances=1,
        kwargs={},
    )

    log.info(
        f"Scheduler configured: "
        f"cycle={config.scan_interval_seconds}s, "
        f"daily_report=00:00 {config.bot_timezone}, "
        f"weekly_learner=Sun 00:00 UTC"
    )
    return scheduler

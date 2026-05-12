"""
bot/risk/circuit_breaker.py — Bot state machine.

States:
  RUNNING           — Normal operation
  PAUSED_DAILY_LOSS — Daily loss cap hit. Auto-resumes at midnight UTC.
  PAUSED_CONSECUTIVE— N consecutive losses. Resumes after 2-hour cooldown.
  PAUSED_FLOOR_HIT  — Capital dropped > floor %. ONLY resumes on /resume.
  PAUSED_MANUAL     — Owner sent /pause. ONLY resumes on /resume.
"""
from __future__ import annotations

import logging
from enum import Enum
from datetime import datetime, timezone, timedelta

log = logging.getLogger("nanorca.risk.circuit_breaker")


class BotState(Enum):
    RUNNING            = "running"
    PAUSED_DAILY_LOSS  = "paused_daily_loss"
    PAUSED_CONSECUTIVE = "paused_consecutive"
    PAUSED_FLOOR_HIT   = "paused_floor_hit"
    PAUSED_MANUAL      = "paused_manual"


class CircuitBreaker:
    """Manages bot pause/resume state. Thread-safe for asyncio context."""

    def __init__(self, db, config) -> None:
        self._db = db
        self._config = config
        self.state = BotState.RUNNING
        self._consecutive_losses = 0
        self._paused_at: datetime | None = None

    # ── State transitions ──────────────────────────────────────────────────

    async def trigger_floor_hit(self) -> None:
        self.state = BotState.PAUSED_FLOOR_HIT
        self._paused_at = datetime.now(timezone.utc)
        await self._log_event("floor_hit", "critical", "Capital floor triggered — all trading paused")
        log.critical("CIRCUIT BREAKER: floor hit — PAUSED_FLOOR_HIT")

    async def pause_daily_loss(self) -> None:
        self.state = BotState.PAUSED_DAILY_LOSS
        self._paused_at = datetime.now(timezone.utc)
        await self._log_event("paused_daily_loss", "warning", "Daily loss cap reached")
        log.warning("CIRCUIT BREAKER: daily loss cap — PAUSED_DAILY_LOSS")

    async def trigger_consecutive(self) -> None:
        self.state = BotState.PAUSED_CONSECUTIVE
        self._paused_at = datetime.now(timezone.utc)
        await self._log_event("circuit_breaker", "warning",
                              f"{self._config.circuit_breaker_n} consecutive losses — 2h cooldown")
        log.warning("CIRCUIT BREAKER: consecutive losses — PAUSED_CONSECUTIVE")

    async def pause_manual(self) -> None:
        self.state = BotState.PAUSED_MANUAL
        self._paused_at = datetime.now(timezone.utc)
        await self._log_event("paused_manual", "info", "Manual pause via /pause command")
        log.info("CIRCUIT BREAKER: manual pause — PAUSED_MANUAL")

    async def resume(self) -> bool:
        """Resume from any paused state. Returns False if state can't be resumed."""
        if self.state == BotState.RUNNING:
            return False
        prev = self.state
        self.state = BotState.RUNNING
        self._consecutive_losses = 0
        self._paused_at = None
        await self._log_event("resumed", "info", f"Resumed from {prev.value}")
        log.info(f"CIRCUIT BREAKER: resumed from {prev.value}")
        return True

    # ── Auto-resume checks (called each cycle) ────────────────────────────

    async def check_auto_resume(self) -> None:
        """Check if any auto-resumable pause conditions have elapsed."""
        now = datetime.now(timezone.utc)

        if self.state == BotState.PAUSED_CONSECUTIVE and self._paused_at:
            cooldown = timedelta(hours=2)
            if now - self._paused_at >= cooldown:
                log.info("Auto-resuming after 2-hour consecutive loss cooldown")
                await self.resume()

        elif self.state == BotState.PAUSED_DAILY_LOSS and self._paused_at:
            # Resumes at next UTC midnight
            next_midnight = self._paused_at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if now >= next_midnight:
                log.info("Auto-resuming at midnight UTC after daily loss pause")
                await self.resume()

    # ── Loss tracking ──────────────────────────────────────────────────────

    async def record_loss(self) -> None:
        """Called after a losing trade. May trigger PAUSED_CONSECUTIVE."""
        self._consecutive_losses += 1
        log.info(f"Consecutive losses: {self._consecutive_losses}/{self._config.circuit_breaker_n}")
        if self._consecutive_losses >= self._config.circuit_breaker_n:
            await self.trigger_consecutive()

    async def record_win(self) -> None:
        """Reset consecutive loss counter on a win."""
        self._consecutive_losses = 0

    # ── Internal ───────────────────────────────────────────────────────────

    async def _log_event(self, event_type: str, severity: str, message: str) -> None:
        try:
            await self._db.log_event(event_type, severity, message)
        except Exception as e:
            log.error(f"Failed to log circuit breaker event: {e}")

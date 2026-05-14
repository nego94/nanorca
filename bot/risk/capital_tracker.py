"""
bot/risk/capital_tracker.py — Tracks capital, P&L, floor, and drawdown triggers.

Drawdown monitoring feeds into the trading plan's recovery rules:
  -1%  → reduce_target / allow_recovery
  -3%  → leverage_reduced = True (risk manager halves leverage for rest of day)
  -5%  → day_stopped = True (no more trades today)
  -10% → strategy_review / hard_stop
  -20% → emergency (aggressive mode only)

All flags reset at UTC midnight.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from risk.risk_manager import RiskManager

log = logging.getLogger("nanorca.risk.capital")


class CapitalTracker:
    def __init__(self, db, config) -> None:
        self._db = db
        self._config = config
        self.current_capital = config.starting_capital_usd
        self.daily_pnl = 0.0
        self._day_start_capital = config.starting_capital_usd
        self._day_reset_at: datetime | None = None
        self._peak_capital = config.starting_capital_usd
        self.synced_from_real = False  # True once real exchange balance is applied

        # Drawdown recovery flags — read by risk_manager and main_loop
        self.leverage_reduced = False
        self.high_risk_paused = False
        self.day_stopped = False

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def floor_capital(self) -> float:
        return self._config.starting_capital_usd * (1 - self._config.capital_floor_pct / 100)

    @property
    def pct_from_start(self) -> float:
        return (self.current_capital - self._config.starting_capital_usd) / self._config.starting_capital_usd * 100

    @property
    def pct_from_floor(self) -> float:
        if self.floor_capital <= 0:
            return 100.0
        return (self.current_capital - self.floor_capital) / self.floor_capital * 100

    @property
    def daily_drawdown_pct(self) -> float:
        """Today's change as % (negative = loss)."""
        if self._day_start_capital <= 0:
            return 0.0
        return (self.current_capital - self._day_start_capital) / self._day_start_capital * 100

    @property
    def peak_drawdown_pct(self) -> float:
        """Drawdown from all-time peak (negative = below peak)."""
        if self._peak_capital <= 0:
            return 0.0
        return (self.current_capital - self._peak_capital) / self._peak_capital * 100

    # ── Safety checks (called every cycle, in order) ───────────────────────

    async def check_floor(self) -> bool:
        """True if capital hit or dropped below the floor."""
        hit = self.current_capital <= self.floor_capital
        if hit:
            log.critical(
                f"CAPITAL FLOOR HIT: current=${self.current_capital:.2f} "
                f"floor=${self.floor_capital:.2f}"
            )
        return hit

    async def daily_loss_exceeded(self) -> bool:
        """True if today's loss % >= MAX_DAILY_LOSS_PCT."""
        self._maybe_reset_day()
        loss_pct = -self.daily_drawdown_pct
        exceeded = loss_pct >= self._config.max_daily_loss_pct
        if exceeded:
            log.warning(f"Daily loss cap: {loss_pct:.1f}% >= {self._config.max_daily_loss_pct}%")
        return exceeded

    async def check_drawdown_rules(self, risk_manager: "RiskManager") -> str | None:
        """
        Check trading plan drawdown rules against today's P&L.
        Updates internal flags (leverage_reduced, day_stopped, high_risk_paused).
        Returns the action string if a rule fired, else None.
        """
        self._maybe_reset_day()
        dd = self.daily_drawdown_pct  # negative when losing

        rule = risk_manager.get_drawdown_action(dd)
        if rule is None:
            return None

        action = rule.action
        log.warning(f"Drawdown rule: {dd:.2f}% → {action} ({rule.description})")

        if action == "reduce_leverage" and not self.leverage_reduced:
            self.leverage_reduced = True
            log.info("Leverage halved for remainder of day")

        elif action in ("stop_day", "hard_stop") and not self.day_stopped:
            self.day_stopped = True
            log.warning(f"Day stopped: {rule.description}")

        elif action == "pause_high_risk" and not self.high_risk_paused:
            self.high_risk_paused = True
            log.info("High-risk setups paused")

        elif action == "emergency":
            log.critical("EMERGENCY PROTECTION MODE TRIGGERED")

        return action

    # ── Real balance sync ──────────────────────────────────────────────────

    def restore_from_snapshot(self, snapshot: dict) -> None:
        """
        Restore capital from the last DB snapshot on bot restart.

        PRIMARY startup path for paper mode. Preserves accumulated paper P&L
        across restarts instead of resetting to the real exchange balance.
        Sets synced_from_real=True so the background sync loop cannot
        overwrite this value with the live exchange balance.
        """
        total = float(snapshot.get("total_usd") or 0)
        if total <= 0:
            return
        old = self.current_capital
        self.current_capital    = total
        self._day_start_capital = total
        self._peak_capital      = max(total, self._peak_capital)
        self.synced_from_real   = True
        log.info(f"Capital restored from DB snapshot: ${old:.2f} → ${total:.2f}")

    def sync_from_real_balance(self, real_usd: float) -> None:
        """
        Seed the paper trading bankroll from the real exchange balance.
        Called ONLY on first run when no DB snapshot exists yet.
        After restore_from_snapshot() runs, synced_from_real=True blocks this.
        """
        if real_usd <= 0:
            return
        old = self.current_capital
        self.current_capital      = real_usd
        self._day_start_capital   = real_usd
        self._peak_capital        = max(real_usd, self._peak_capital)
        self.synced_from_real     = True
        log.info(f"Capital seeded from real exchange balance: ${old:.2f} → ${real_usd:.2f}")

    def refresh_from_real(self, real_usd: float) -> None:
        """
        Refresh current_capital from the live exchange balance without
        resetting daily P&L counters. Called by /status so the display
        always shows the real value, not a stale startup snapshot.
        """
        if real_usd <= 0:
            return
        self.current_capital = real_usd
        if real_usd > self._peak_capital:
            self._peak_capital = real_usd

    # ── Trade result updates ───────────────────────────────────────────────

    async def update_from_trade(self, trade_result: dict) -> None:
        """Update capital after a closed position. Called by main loop."""
        pnl  = trade_result.get("pnl_usd", 0.0) or 0.0
        fees = trade_result.get("fees_usd", 0.0) or 0.0
        net  = pnl - fees
        self.current_capital += net
        self.daily_pnl += net
        if self.current_capital > self._peak_capital:
            self._peak_capital = self.current_capital
        log.info(f"Capital: ${self.current_capital:.2f} (net={net:+.2f})")
        await self._snapshot()

    # ── Internal ───────────────────────────────────────────────────────────

    async def _snapshot(self) -> None:
        try:
            await self._db.save_capital_snapshot({
                "total_usd":    self.current_capital,
                "starting_usd": self._config.starting_capital_usd,
                "pct_change":   self.pct_from_start,
                "daily_pnl":    self.daily_pnl,
            })
        except Exception as e:
            log.error(f"Failed to save capital snapshot: {e}")

    def _maybe_reset_day(self) -> None:
        """Reset daily counters and drawdown flags at UTC midnight."""
        now = datetime.now(timezone.utc)
        if self._day_reset_at is None:
            self._day_reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_midnight = self._day_reset_at + timedelta(days=1)
        if now >= next_midnight:
            self._day_start_capital = self.current_capital
            self.daily_pnl = 0.0
            self._day_reset_at = next_midnight
            self.leverage_reduced = False
            self.high_risk_paused = False
            self.day_stopped = False
            log.info("Daily counters and drawdown flags reset at midnight UTC")

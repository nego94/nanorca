"""
bot/learning/outcome_logger.py — Logs trade open/close events with full context.

Position persistence across restarts:
  On every trade open, exchange_order_id is saved to DB.
  On startup, recover_from_db() reloads the open trade map from DB.
  Stale open trades (> max_hold_minutes) are auto-expired with 0 P&L.
  This ensures no open trade is permanently stuck in DB after a restart.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("nanorca.learning.outcomes")


class OutcomeLogger:
    """Records trade lifecycle events to DB for learning and reporting."""

    def __init__(self, db) -> None:
        self._db = db
        # In-memory map: exchange_order_id → DB trade_id
        self._open_trades: dict[str, int] = {}

    async def recover_from_db(self, max_hold_minutes: int = 240) -> None:
        """
        Called once at startup. Recovers open trades from DB after a restart.

        Trades older than max_hold_minutes are expired immediately with 0 P&L
        (they would have been auto-closed by the hold-time rule anyway).
        Recent trades are loaded into _open_trades so the close path works correctly.
        """
        open_trades = await self._db.get_open_trades()
        if not open_trades:
            log.info("Startup recovery: no open trades in DB")
            return

        now = datetime.now(timezone.utc)
        expired = 0
        recovered = 0

        for trade in open_trades:
            opened_at = trade["opened_at"]
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            age_minutes = (now - opened_at).total_seconds() / 60

            if age_minutes > max_hold_minutes:
                # Would have been auto-closed — expire cleanly
                await self._db.expire_trade(trade["id"])
                log.info(
                    f"Expired stale open trade: db_id={trade['id']} "
                    f"market={trade.get('market')} age={age_minutes:.0f}m"
                )
                expired += 1
            else:
                # Recent trade — recover mapping so log_trade_closed() works
                order_id = trade.get("exchange_order_id", "")
                if order_id:
                    self._open_trades[order_id] = trade["id"]
                    recovered += 1

        log.info(
            f"Startup recovery: {expired} stale trades expired, "
            f"{recovered} recent trades recovered to memory"
        )

    async def log_trade_opened(self, decision: dict[str, Any], result: dict[str, Any]) -> None:
        """Record a newly opened trade in the DB (including exchange_order_id)."""
        order_id = result.get("exchange_order_id", "")
        trade = {
            "exchange":          decision.get("exchange") or "binance",
            "market":            decision.get("market", "UNKNOWN"),
            "direction":         decision.get("direction", "long"),
            "entry_price":       result.get("filled_price"),
            "size_usd":          result.get("filled_size_usd"),
            "confidence_score":  decision.get("confidence"),
            "signal_mix":        decision.get("signals_used", []),
            "claude_reasoning":  decision.get("reasoning"),
            "paper":             result.get("paper", True),
            "exchange_order_id": order_id,
        }
        trade_id = await self._db.save_trade(trade)
        self._open_trades[order_id] = trade_id
        log.info(f"Trade opened: db_id={trade_id}, order={order_id}")

    async def log_trade_closed(self, order_id: str, exit_price: float, pnl: float, fees: float) -> None:
        """Update the trade record when a position is closed."""
        trade_id = self._open_trades.pop(order_id, None)
        if not trade_id:
            log.warning(f"log_trade_closed: unknown order_id {order_id}")
            return
        try:
            await self._db.close_trade(trade_id, exit_price, pnl, fees)
            log.info(f"Trade closed: db_id={trade_id}, pnl=${pnl:.2f}")
        except Exception as e:
            log.error(f"Failed to log trade close: {e}")

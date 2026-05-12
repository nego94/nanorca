"""
bot/learning/outcome_logger.py — Logs trade open/close events with full context.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nanorca.learning.outcomes")


class OutcomeLogger:
    """Records trade lifecycle events to DB for learning and reporting."""

    def __init__(self, db) -> None:
        self._db = db
        # In-memory map: exchange_order_id → DB trade_id
        self._open_trades: dict[str, int] = {}

    async def log_trade_opened(self, decision: dict[str, Any], result: dict[str, Any]) -> None:
        """Record a newly opened trade in the DB."""
        trade = {
            "exchange": decision["exchange"],
            "market": decision["market"],
            "direction": decision["direction"],
            "entry_price": result.get("filled_price"),
            "size_usd": result.get("filled_size_usd"),
            "confidence_score": decision.get("confidence"),
            "signal_mix": decision.get("signals_used", []),
            "claude_reasoning": decision.get("reasoning"),
            "paper": result.get("paper", True),
        }
        try:
            trade_id = await self._db.save_trade(trade)
            order_id = result.get("exchange_order_id", "")
            self._open_trades[order_id] = trade_id
            log.info(f"Trade opened: db_id={trade_id}, order={order_id}")
        except Exception as e:
            log.error(f"Failed to log trade open: {e}")

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

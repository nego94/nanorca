"""
bot/data/paper_order_book.py — In-memory paper order lifecycle manager.

Simulates limit-order fill + position monitoring for paper trading.

Lifecycle:
  PLANNED → price reaches entry → FILLED (saved to DB as 'open')
           → price reaches target/stop/timeout → CLOSED (DB updated, P&L recorded)

Pending orders are in-memory only (not saved to DB until filled).
This avoids polluting the DB with plans that never execute.

On bot restart: pending orders are lost (acceptable for paper mode).
Open (filled) orders are recovered from DB via OutcomeLogger.recover_from_db().
"""
from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("nanorca.data.paper_book")

# Fill a LONG limit order when price is within 0.3% of entry (accounts for spread).
# This means: entry $5.01 fills anywhere from $5.01 down to ~$4.999.
_FILL_TOLERANCE_PCT = 0.003

# Futures leverage assumption for P&L and fee calculation.
# Binance hard-caps at 3x for this capital level.
_PAPER_LEVERAGE = 3.0

# Maker fee per side: 0.02%. Round-trip: 0.04%.
_FEE_RATE = 0.0004


@dataclass
class PaperOrder:
    order_id: str
    exchange: str
    market: str
    direction: str        # "long" | "short"
    entry_price: float    # planned limit entry
    target_price: float   # price at which we declare WIN
    stop_price: float     # price at which we declare LOSS
    target_pct: float     # e.g. 1.5 means +1.5%
    stop_pct: float       # e.g. 2.0 means -2.0%
    size_usd: float       # margin used (not notional)
    confidence: int
    reasoning: str
    signals_used: list
    created_at: float = field(default_factory=time.monotonic)
    filled_at: float | None = None
    fill_price: float | None = None
    status: str = "pending"   # pending | open | closed

    @property
    def notional_usd(self) -> float:
        return self.size_usd * _PAPER_LEVERAGE

    @property
    def hold_minutes(self) -> float:
        if self.filled_at is None:
            return 0.0
        return (time.monotonic() - self.filled_at) / 60

    def calc_pnl(self, exit_price: float) -> tuple[float, float]:
        """Returns (net_pnl_usd, fees_usd) from entry fill to exit_price."""
        entry = self.fill_price or self.entry_price
        if self.direction == "long":
            move_pct = (exit_price - entry) / entry
        else:
            move_pct = (entry - exit_price) / entry
        gross_pnl = move_pct * self.notional_usd
        fees = self.notional_usd * _FEE_RATE
        return round(gross_pnl - fees, 6), round(fees, 6)

    def pnl_pct_from_entry(self, exit_price: float) -> float:
        entry = self.fill_price or self.entry_price
        if self.direction == "long":
            return (exit_price - entry) / entry * 100
        return (entry - exit_price) / entry * 100


class PaperOrderBook:
    """
    Tracks all paper orders: pending (planned), open (filled), closed (exited).

    Called each main cycle to:
      1. check_fills()  — move pending → open when price reaches entry
      2. check_exits()  — move open → closed when target/stop/timeout
    """

    def __init__(self, max_hold_minutes: int = 240, max_pending: int = 3) -> None:
        self._orders: dict[str, PaperOrder] = {}
        self._max_hold_minutes = max_hold_minutes
        self._max_pending = max_pending

    # ── Adding orders ──────────────────────────────────────────────────────

    def plan(self, decision: dict[str, Any], current_price: float) -> PaperOrder | None:
        """
        Create a new pending paper order from a Claude decision.

        Entry is the current market price (market order simulation).
        Target and stop are calculated from the decision percentages.
        Returns None if already at max pending orders.
        """
        if len(self.get_pending()) >= self._max_pending:
            log.info(f"Paper book: max pending ({self._max_pending}) reached — skipping new order")
            return None

        direction  = (decision.get("direction") or "long").lower()
        stop_pct   = float(decision.get("stop_loss_pct", 2.0))
        target_pct = float(decision.get("target_profit_pct", 1.0))

        if direction == "long":
            stop_price   = current_price * (1 - stop_pct / 100)
            target_price = current_price * (1 + target_pct / 100)
        else:
            stop_price   = current_price * (1 + stop_pct / 100)
            target_price = current_price * (1 - target_pct / 100)

        order = PaperOrder(
            order_id     = f"PAPER_{uuid.uuid4().hex[:8].upper()}",
            exchange     = decision.get("exchange") or "binance",
            market       = decision.get("market", "UNKNOWN"),
            direction    = direction,
            entry_price  = current_price,
            target_price = target_price,
            stop_price   = stop_price,
            target_pct   = target_pct,
            stop_pct     = stop_pct,
            size_usd     = float(decision.get("size_usd", 0)),
            confidence   = decision.get("confidence", 0),
            reasoning    = decision.get("reasoning", ""),
            signals_used = decision.get("signals_used", []),
        )
        self._orders[order.order_id] = order
        log.info(
            f"Paper PLANNED: {order.market} {direction.upper()} "
            f"entry=${current_price:.4f} target=${target_price:.4f} stop=${stop_price:.4f} "
            f"notional=${order.notional_usd:.2f}"
        )
        return order

    # ── Cycle checks ───────────────────────────────────────────────────────

    def check_fills(self, price_map: dict[str, float]) -> list[PaperOrder]:
        """
        Inspect pending orders against current prices.

        A LONG limit order fills when price ≤ entry + tolerance.
        A SHORT limit order fills when price ≥ entry - tolerance.
        This simulates price "coming to" the limit.

        Returns list of newly filled orders (status → 'open').
        """
        newly_filled: list[PaperOrder] = []
        for order in list(self._orders.values()):
            if order.status != "pending":
                continue
            current = price_map.get(order.market, 0)
            if current <= 0:
                continue
            tolerance = order.entry_price * _FILL_TOLERANCE_PCT
            if order.direction == "long":
                fills = current <= order.entry_price + tolerance
            else:
                fills = current >= order.entry_price - tolerance

            # Also expire if pending for more than 2× max hold time (price never came)
            pending_minutes = (time.monotonic() - order.created_at) / 60
            if pending_minutes > self._max_hold_minutes * 2:
                order.status = "closed"
                log.info(f"Paper order EXPIRED (never filled): {order.market} after {pending_minutes:.0f}m")
                continue

            if fills:
                order.status    = "open"
                order.fill_price = current
                order.filled_at  = time.monotonic()
                newly_filled.append(order)
                log.info(
                    f"Paper FILLED: {order.market} {order.direction.upper()} "
                    f"@ ${current:.4f} (planned ${order.entry_price:.4f})"
                )
        return newly_filled

    def check_exits(self, price_map: dict[str, float]) -> list[tuple[PaperOrder, str, float]]:
        """
        Inspect open orders for target hit, stop loss, or timeout.

        Returns list of (order, reason, exit_price). Marks order as 'closed'.
        Caller is responsible for logging to DB + Telegram.
        """
        exits: list[tuple[PaperOrder, str, float]] = []
        for order in list(self._orders.values()):
            if order.status != "open" or order.filled_at is None:
                continue
            current = price_map.get(order.market, 0)
            if current <= 0:
                continue

            if order.direction == "long":
                hit_target = current >= order.target_price
                hit_stop   = current <= order.stop_price
            else:
                hit_target = current <= order.target_price
                hit_stop   = current >= order.stop_price

            timed_out = order.hold_minutes >= self._max_hold_minutes

            if hit_target:
                reason = "target_hit"
            elif hit_stop:
                reason = "stop_loss"
            elif timed_out:
                reason = f"timeout_{order.hold_minutes:.0f}m"
            else:
                continue

            order.status = "closed"
            exits.append((order, reason, current))
            log.info(
                f"Paper CLOSED: {order.market} {order.direction.upper()} "
                f"reason={reason} exit=${current:.4f} hold={order.hold_minutes:.0f}m"
            )
        return exits

    # ── Queries ────────────────────────────────────────────────────────────

    def get_pending(self) -> list[PaperOrder]:
        return [o for o in self._orders.values() if o.status == "pending"]

    def get_open(self) -> list[PaperOrder]:
        return [o for o in self._orders.values() if o.status == "open"]

    def count_active(self) -> int:
        return sum(1 for o in self._orders.values() if o.status in ("pending", "open"))

    def purge_closed(self) -> None:
        """Remove closed orders from memory (call after DB logging)."""
        self._orders = {oid: o for oid, o in self._orders.items() if o.status != "closed"}

    def format_telegram(self) -> str:
        """Format all active orders for /positions command."""
        pending = self.get_pending()
        open_orders = self.get_open()
        if not pending and not open_orders:
            return "_No active paper orders._"
        lines = []
        for o in pending:
            age_min = (time.monotonic() - o.created_at) / 60
            lines.append(
                f"⏳ PENDING {o.direction.upper()} `{o.market}`\n"
                f"   Entry: ${o.entry_price:.4f} | Target: ${o.target_price:.4f} | Stop: ${o.stop_price:.4f}\n"
                f"   Waiting {age_min:.0f}m | Conf: {o.confidence}/100"
            )
        for o in open_orders:
            lines.append(
                f"🟢 OPEN {o.direction.upper()} `{o.market}`\n"
                f"   Fill: ${o.fill_price:.4f} | Target: ${o.target_price:.4f} | Stop: ${o.stop_price:.4f}\n"
                f"   Hold: {o.hold_minutes:.0f}m | Size: ${o.size_usd:.2f} (${o.notional_usd:.2f} notional)"
            )
        return "\n\n".join(lines)

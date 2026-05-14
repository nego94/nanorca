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

    def __init__(
        self,
        max_hold_minutes: int = 240,
        max_pending: int = 3,
        cooldown_minutes: float = 15.0,
    ) -> None:
        self._orders: dict[str, PaperOrder] = {}
        self._max_hold_minutes = max_hold_minutes
        self._max_pending = max_pending
        self._cooldown_seconds = cooldown_minutes * 60
        # market → monotonic time when cooldown expires
        self._cooldowns: dict[str, float] = {}

    # ── Adding orders ──────────────────────────────────────────────────────

    def plan(self, decision: dict[str, Any], current_price: float) -> PaperOrder | None:
        """
        Create a new pending paper order from a Claude decision.

        Entry is the current market price (market order simulation).
        Target and stop are calculated from the decision percentages.
        Returns None if already at max pending orders or same market already active.
        """
        if len(self.get_pending()) >= self._max_pending:
            log.info(f"Paper book: max pending ({self._max_pending}) reached — skipping new order")
            return None

        # Block duplicate orders on same market — wait for existing one to close first
        market = decision.get("market", "UNKNOWN")
        existing = next(
            (o for o in self._orders.values() if o.market == market and o.status in ("pending", "open")),
            None,
        )
        if existing:
            log.info(f"Paper book: already have {existing.status} order for {market} — skipping duplicate")
            return None

        # Block re-entry during cooldown after a recent close
        cooldown_until = self._cooldowns.get(market, 0)
        if time.monotonic() < cooldown_until:
            remaining = (cooldown_until - time.monotonic()) / 60
            log.info(f"Paper book: {market} in cooldown ({remaining:.0f}m remaining) — skipping")
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
            # Start cooldown to prevent immediate re-entry on the same market
            self._cooldowns[order.market] = time.monotonic() + self._cooldown_seconds
            log.info(
                f"Paper CLOSED: {order.market} {order.direction.upper()} "
                f"reason={reason} exit=${current:.4f} hold={order.hold_minutes:.0f}m "
                f"(cooldown {self._cooldown_seconds/60:.0f}m)"
            )
        return exits

    # ── Queries ────────────────────────────────────────────────────────────

    def get_monitoring_updates(
        self, price_map: dict[str, float], interval_seconds: float = 1200
    ) -> list[tuple[PaperOrder, float]]:
        """
        Return open orders that are due for a periodic monitoring update.
        Default interval: 20 minutes (1200s).
        Returns list of (order, current_price) pairs.
        """
        now = time.monotonic()
        due = []
        for order in self._orders.values():
            if order.status != "open" or order.filled_at is None:
                continue
            current = price_map.get(order.market, 0)
            if current <= 0:
                continue
            last = getattr(order, "_last_update", order.filled_at)
            if (now - last) >= interval_seconds:
                order._last_update = now  # type: ignore[attr-defined]
                due.append((order, current))
        return due

    def get_pending(self) -> list[PaperOrder]:
        return [o for o in self._orders.values() if o.status == "pending"]

    def get_open(self) -> list[PaperOrder]:
        return [o for o in self._orders.values() if o.status == "open"]

    def count_active(self) -> int:
        return sum(1 for o in self._orders.values() if o.status in ("pending", "open"))

    def recover_open_positions(self, open_trades: list[dict]) -> int:
        """
        Reconstruct PaperOrder objects from DB records after a bot restart.

        Called once at startup after outcome_logger.recover_from_db().
        Only paper trades with target_price and stop_price stored are recovered
        (trades opened before migration 003 won't have these and are skipped —
        they will be expired by recover_from_db after max_hold_minutes).

        Returns count of positions recovered into active monitoring.
        """
        from datetime import datetime, timezone

        recovered = 0
        now_utc = datetime.now(timezone.utc)

        for trade in open_trades:
            if not trade.get("paper"):
                continue

            order_id    = trade.get("exchange_order_id", "")
            target      = trade.get("target_price")
            stop        = trade.get("stop_price")
            fill_price  = float(trade.get("entry_price") or 0)
            size_usd    = float(trade.get("size_usd") or 0)
            market      = trade.get("market", "")

            if not order_id or not target or not stop or fill_price <= 0 or not market:
                continue  # missing target/stop — can't monitor, recover_from_db handles expiry

            target_f = float(target)
            stop_f   = float(stop)
            direction = trade.get("direction", "long")

            # Approximate target_pct and stop_pct from stored prices
            if direction == "long":
                target_pct = (target_f - fill_price) / fill_price * 100
                stop_pct   = (fill_price - stop_f) / fill_price * 100
            else:
                target_pct = (fill_price - target_f) / fill_price * 100
                stop_pct   = (stop_f - fill_price) / fill_price * 100

            # Calculate elapsed hold time so hold_minutes is accurate after restart
            opened_at = trade.get("opened_at")
            if opened_at and hasattr(opened_at, "tzinfo") and opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            age_seconds = (now_utc - opened_at).total_seconds() if opened_at else 0

            order = PaperOrder(
                order_id     = order_id,
                exchange     = trade.get("exchange", "binance"),
                market       = market,
                direction    = direction,
                entry_price  = fill_price,
                target_price = target_f,
                stop_price   = stop_f,
                target_pct   = round(target_pct, 2),
                stop_pct     = round(stop_pct, 2),
                size_usd     = size_usd,
                confidence   = int(trade.get("confidence_score") or 0),
                reasoning    = trade.get("claude_reasoning", ""),
                signals_used = trade.get("signal_mix") or [],
            )
            order.status     = "open"
            order.fill_price = fill_price
            order.filled_at  = time.monotonic() - age_seconds  # preserves elapsed hold time

            self._orders[order_id] = order
            recovered += 1
            log.info(
                f"Recovered paper position: {market} {direction.upper()} "
                f"fill=${fill_price:.4f} target=${target_f:.4f} stop=${stop_f:.4f} "
                f"held={age_seconds/60:.0f}m already"
            )

        return recovered

    def purge_closed(self) -> None:
        """Remove closed orders from memory (call after DB logging)."""
        self._orders = {oid: o for oid, o in self._orders.items() if o.status != "closed"}
        # Clean up expired cooldowns
        now = time.monotonic()
        self._cooldowns = {m: t for m, t in self._cooldowns.items() if t > now}

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

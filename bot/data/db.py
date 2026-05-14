"""
bot/data/db.py — asyncpg connection pool and query helpers.

All DB access goes through this class. Never use raw connections elsewhere.
All queries use parameterized statements — no string formatting with user data.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import asyncpg

log = logging.getLogger("nanorca.data.db")


class Database:
    """asyncpg connection pool wrapper with typed query methods."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self, min_size: int = 2, max_size: int = 10) -> None:
        async def _init(conn):
            # Register JSONB codec so asyncpg serializes Python dicts/lists automatically.
            # Without this, asyncpg rejects lists/dicts for jsonb columns with "expected str".
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
            init=_init,
        )
        log.info(f"DB pool connected (min={min_size}, max={max_size})")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            log.info("DB pool closed")

    # ── Signal weights ─────────────────────────────────────────────────────

    async def get_signal_weights(self) -> dict[str, float]:
        """Load current signal weights from DB. Returns {signal_type: weight}."""
        rows = await self._fetch(
            "SELECT signal_type, weight FROM signal_weights ORDER BY signal_type"
        )
        return {r["signal_type"]: float(r["weight"]) for r in rows}

    async def update_signal_weights(self, weights: dict[str, float]) -> None:
        """Upsert new signal weights (called by weekly learner)."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for sig_type, weight in weights.items():
                    await conn.execute(
                        """
                        INSERT INTO signal_weights (signal_type, weight, updated_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (signal_type) DO UPDATE
                        SET weight = $2, updated_at = NOW()
                        """,
                        sig_type, weight,
                    )

    # ── Trades ─────────────────────────────────────────────────────────────

    async def save_trade(self, trade: dict[str, Any]) -> int:
        """
        Insert a new trade record. Returns the new trade ID.

        If a trade with the same exchange_order_id already exists (duplicate fill
        detected at DB level), return the existing ID instead of inserting again.
        """
        order_id = trade.get("exchange_order_id", "")

        # Check for existing record with this order_id to prevent duplicates
        if order_id:
            existing = await self._fetchrow(
                "SELECT id FROM trades WHERE exchange_order_id = $1 LIMIT 1",
                order_id,
            )
            if existing:
                log.warning(f"save_trade: order_id {order_id!r} already in DB (id={existing['id']}) — skipping insert")
                return existing["id"]

        row = await self._fetchrow(
            """
            INSERT INTO trades (
                exchange, market, direction, entry_price, size_usd,
                confidence_score, signal_mix, claude_reasoning, status,
                opened_at, paper, exchange_order_id, target_price, stop_price
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'open',NOW(),$9,$10,$11,$12)
            RETURNING id
            """,
            trade["exchange"], trade["market"], trade["direction"],
            trade.get("entry_price"), trade.get("size_usd"),
            trade.get("confidence_score"), trade.get("signal_mix", {}),
            trade.get("claude_reasoning"), trade.get("paper", True),
            order_id,
            trade.get("target_price"),
            trade.get("stop_price"),
        )
        return row["id"]

    async def get_open_trades(self) -> list[dict]:
        """
        Return all open trades for restart recovery.
        Includes target_price and stop_price so PaperOrderBook can reconstruct
        open positions and continue monitoring them after restart.
        """
        rows = await self._fetch(
            """
            SELECT id, exchange_order_id, exchange, market, direction,
                   entry_price, size_usd, target_price, stop_price,
                   confidence_score, signal_mix, claude_reasoning, opened_at, paper
            FROM trades WHERE status = 'open' ORDER BY opened_at
            """
        )
        return [dict(r) for r in rows]

    async def expire_trade(self, trade_id: int) -> None:
        """Close a trade with 0 P&L — used when position is lost on restart."""
        await self._execute(
            """
            UPDATE trades SET
                status = 'closed',
                pnl_usd = 0,
                fees_usd = 0,
                outcome = 'breakeven',
                win = false,
                closed_at = NOW(),
                hold_minutes = EXTRACT(EPOCH FROM (NOW() - opened_at)) / 60,
                claude_reasoning = COALESCE(claude_reasoning, '') || ' [AUTO-CLOSED: position lost on bot restart]'
            WHERE id = $1
            """,
            trade_id,
        )

    async def close_trade(self, trade_id: int, exit_price: float, pnl: float, fees: float) -> None:
        """Update a trade record as closed."""
        await self._execute(
            """
            UPDATE trades SET
                exit_price = $2,
                pnl_usd = $3,
                fees_usd = $4,
                status = 'closed',
                closed_at = NOW(),
                hold_minutes = EXTRACT(EPOCH FROM (NOW() - opened_at)) / 60,
                outcome = CASE WHEN $3 > 0 THEN 'win' WHEN $3 < 0 THEN 'loss' ELSE 'breakeven' END,
                win = ($3 > 0)
            WHERE id = $1
            """,
            trade_id, exit_price, pnl, fees,
        )

    async def close_trade_by_order_id(self, order_id: str, exit_price: float, pnl: float, fees: float) -> bool:
        """
        Fallback: close a trade by exchange_order_id when the in-memory trade_id is unknown.
        Used when _open_trades was cleared (e.g. bot restart between fill and close).
        Returns True if a matching open trade was found and closed.
        """
        row = await self._fetchrow(
            """
            UPDATE trades SET
                exit_price = $2,
                pnl_usd = $3,
                fees_usd = $4,
                status = 'closed',
                closed_at = NOW(),
                hold_minutes = EXTRACT(EPOCH FROM (NOW() - opened_at)) / 60,
                outcome = CASE WHEN $3 > 0 THEN 'win' WHEN $3 < 0 THEN 'loss' ELSE 'breakeven' END,
                win = ($3 > 0)
            WHERE exchange_order_id = $1 AND status = 'open'
            RETURNING id
            """,
            order_id, exit_price, pnl, fees,
        )
        return row is not None

    async def get_recent_trades(self, limit: int = 10) -> list[dict]:
        rows = await self._fetch(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT $1", limit
        )
        return [dict(r) for r in rows]

    async def get_trades_in_range(self, since: datetime, until: datetime | None = None) -> list[dict]:
        until = until or datetime.now(timezone.utc)
        rows = await self._fetch(
            "SELECT * FROM trades WHERE created_at >= $1 AND created_at <= $2 ORDER BY created_at DESC",
            since, until,
        )
        return [dict(r) for r in rows]

    # ── Capital snapshots ──────────────────────────────────────────────────

    async def save_capital_snapshot(self, snap: dict[str, Any]) -> None:
        await self._execute(
            """
            INSERT INTO capital_snapshots (total_usd, starting_usd, pct_change, daily_pnl)
            VALUES ($1, $2, $3, $4)
            """,
            snap["total_usd"], snap["starting_usd"], snap.get("pct_change"), snap.get("daily_pnl"),
        )

    async def get_last_capital_snapshot(self) -> dict | None:
        """
        Fetch the most recent capital snapshot.
        Used on bot restart to restore accumulated paper P&L without overwriting
        it with the real exchange balance.
        """
        row = await self._fetchrow(
            """
            SELECT total_usd, starting_usd, pct_change, daily_pnl, recorded_at
            FROM capital_snapshots
            ORDER BY recorded_at DESC
            LIMIT 1
            """
        )
        return dict(row) if row else None

    # ── Performance context ────────────────────────────────────────────────

    async def get_performance_context(self) -> dict[str, Any]:
        """Assemble performance stats for Claude's decision prompt."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        trades_24h = await self.get_trades_in_range(day_ago)
        trades_7d  = await self.get_trades_in_range(week_ago)

        def win_rate(trades):
            closed = [t for t in trades if t.get("status") == "closed"]
            if not closed:
                return 0.0
            return round(sum(1 for t in closed if t.get("win")) / len(closed) * 100, 1)

        daily_pnl = sum(t.get("pnl_usd", 0) or 0 for t in trades_24h if t.get("status") == "closed")

        return {
            "win_rate_24h": win_rate(trades_24h),
            "win_rate_7d": win_rate(trades_7d),
            "daily_pnl": round(daily_pnl, 2),
            "streak": "0",  # TODO Phase 3: calculate actual win/loss streak
        }

    # ── Events ─────────────────────────────────────────────────────────────

    async def log_event(self, event_type: str, severity: str, message: str, payload: dict | None = None) -> None:
        await self._execute(
            """
            INSERT INTO bot_events (event_type, severity, message, payload)
            VALUES ($1, $2, $3, $4)
            """,
            event_type, severity, message, json.dumps(payload or {}),
        )

    # ── Learning reports ───────────────────────────────────────────────────

    async def save_learning_report(self, report: dict[str, Any]) -> None:
        await self._execute(
            """
            INSERT INTO learning_reports
                (period_start, period_end, total_trades, win_rate, total_pnl,
                 claude_analysis, weight_changes, applied, confidence_in_analysis)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            report["period_start"], report["period_end"],
            report.get("total_trades"), report.get("win_rate"),
            report.get("total_pnl"), report.get("claude_analysis"),
            report.get("weight_changes", {}),
            report.get("applied", False),
            report.get("confidence_in_analysis"),
        )

    async def get_last_learning_report(self) -> dict | None:
        row = await self._fetchrow(
            "SELECT * FROM learning_reports ORDER BY generated_at DESC LIMIT 1"
        )
        return dict(row) if row else None

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _fetch(self, query: str, *args) -> list:
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def _fetchrow(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _execute(self, query: str, *args) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(query, *args)

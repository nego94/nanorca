"""
bot/alerts/telegram_bot.py — Telegram bot: sends alerts + handles owner commands.

All commands must respond within 3 seconds (async handlers).
Only accepts messages from TELEGRAM_CHAT_ID to prevent unauthorized access.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

log = logging.getLogger("nanorca.alerts.telegram")


class TelegramBot:
    """Telegram bot for owner notifications and two-way command interface."""

    def __init__(self, config, db, circuit_breaker, capital_tracker, order_router) -> None:
        self._config = config
        self._db = db
        self._cb = circuit_breaker
        self._cap = capital_tracker
        self._router = order_router
        self._app: Application | None = None

    async def start(self) -> None:
        """Build the application and register all command handlers."""
        self._app = (
            Application.builder()
            .token(self._config.telegram_bot_token)
            .build()
        )
        handlers = [
            ("start",        self._cmd_status),
            ("status",       self._cmd_status),
            ("pause",        self._cmd_pause),
            ("resume",       self._cmd_resume),
            ("report",       self._cmd_report),
            ("capital",      self._cmd_capital),
            ("positions",    self._cmd_positions),
            ("markets",      self._cmd_markets),
            ("history",      self._cmd_history),
            ("learning",     self._cmd_learning),
            ("setfloor",     self._cmd_setfloor),
            ("setthreshold", self._cmd_setthreshold),
            ("stop",         self._cmd_stop_exchange),
            ("help",         self._cmd_help),
        ]
        for cmd, handler in handlers:
            self._app.add_handler(CommandHandler(cmd, handler))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram bot polling started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    # ── Guard: only owner can use commands ────────────────────────────────

    def _is_owner(self, update: Update) -> bool:
        """Allow commands from the configured chat (DM or group) sent by owner."""
        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id) if update.effective_user else ""
        cfg_id  = str(self._config.telegram_chat_id)
        # Accept if chat matches (DM) OR if the user is the bot owner (group chat)
        return chat_id == cfg_id or user_id == cfg_id

    async def _deny(self, update: Update) -> None:
        await update.message.reply_text("⛔ Unauthorized.")

    # ── Commands ──────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        from risk.circuit_breaker import BotState
        from risk.trading_plan import TradingMode, format_plan_summary
        state_emoji = {"running": "🟢", "paused_daily_loss": "🟡", "paused_consecutive": "🟡",
                       "paused_floor_hit": "🔴", "paused_manual": "⏸️"}
        s = self._cb.state
        mode = TradingMode(getattr(self._config, 'trading_mode', 'nanorca_decide'))
        plan_line = format_plan_summary(mode, self._cap.current_capital)
        msg = (
            f"*NANORCA Status*\n"
            f"State: {state_emoji.get(s.value, '❓')} `{s.value}`\n"
            f"Mode: {'📄 PAPER' if self._config.paper_trading else '🔴 LIVE'}\n"
            f"Capital: ${self._cap.current_capital:.2f} "
            f"({self._cap.pct_from_start:+.1f}%)\n"
            f"Daily P&L: ${self._cap.daily_pnl:+.2f} "
            f"(drawdown: {self._cap.daily_drawdown_pct:.2f}%)\n"
            f"Floor: ${self._cap.floor_capital:.2f} "
            f"({self._cap.pct_from_floor:.1f}% above floor)\n\n"
            f"{plan_line}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        await self._cb.pause_manual()
        await update.message.reply_text("⏸️ Trading paused. Send /resume to restart.")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        resumed = await self._cb.resume()
        if resumed:
            await update.message.reply_text("▶️ Trading resumed.")
        else:
            await update.message.reply_text("ℹ️ Bot is already running.")

    async def _cmd_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        args = ctx.args or []
        period = "7d" if args and args[0] == "7d" else "24h"
        from datetime import datetime, timezone, timedelta
        since = datetime.now(timezone.utc) - (timedelta(days=7) if period == "7d" else timedelta(hours=24))
        trades = await self._db.get_trades_in_range(since)
        closed = [t for t in trades if t.get("status") == "closed"]
        wins = sum(1 for t in closed if t.get("win"))
        pnl = sum(t.get("pnl_usd", 0) or 0 for t in closed)
        wr = round(wins / len(closed) * 100, 1) if closed else 0
        msg = (
            f"📊 *Report — last {period}*\n"
            f"Trades: {len(trades)} | Closed: {len(closed)}\n"
            f"Win rate: {wr}% ({wins}W / {len(closed)-wins}L)\n"
            f"P&L: ${pnl:+.2f}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_capital(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        msg = (
            f"💰 *Capital Overview*\n"
            f"Starting: ${self._config.starting_capital_usd:.2f}\n"
            f"Current:  ${self._cap.current_capital:.2f} ({self._cap.pct_from_start:+.1f}%)\n"
            f"Floor:    ${self._cap.floor_capital:.2f} ({self._config.capital_floor_pct}%)\n"
            f"Above floor: {self._cap.pct_from_floor:.1f}%"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        try:
            positions = await self._router.get_positions()
            if not positions:
                await update.message.reply_text("📋 No open positions right now.")
                return
            lines = []
            for p in positions:
                lines.append(
                    f"{'🟢' if p.get('side') in ('BUY','LONG') else '🔴'} "
                    f"{p.get('exchange','?').upper()} {p.get('market','?')} "
                    f"${p.get('size_usd',0):.2f} "
                    f"entry@{p.get('entry_price',0):.4f}"
                )
            await update.message.reply_text("📋 *Open positions:*\n" + "\n".join(lines),
                                            parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Could not fetch positions: {e}")

    async def _cmd_markets(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show live market prices fetched from Go executor."""
        if not self._is_owner(update): await self._deny(update); return
        try:
            snapshots = await self._router.scan_markets(["BTC", "ETH", "SOL"])
            if not snapshots:
                await update.message.reply_text("📡 No market data yet — executor may still be warming up.")
                return
            lines = []
            for s in snapshots:
                ex = s.get("exchange", "?")
                market = s.get("market", "?")
                price = s.get("price", 0)
                fr = s.get("funding_rate", 0)
                avail = "✅" if s.get("available") else "❌"
                fr_str = f" fr:{fr*100:.4f}%" if fr else ""
                lines.append(f"{avail} {ex.upper()} {market}: ${price:,.2f}{fr_str}")
            await update.message.reply_text(
                "📊 *Live Market Prices:*\n" + "\n".join(lines),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Market scan error: {e}")

    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        n = int(ctx.args[0]) if ctx.args else 10
        n = min(n, 50)  # cap at 50
        trades = await self._db.get_recent_trades(limit=n)
        if not trades:
            await update.message.reply_text("No trades yet."); return
        lines = []
        for t in trades[:n]:
            emoji = "✅" if t.get("win") else "❌" if t.get("win") is False else "🔄"
            pnl_str = f"${t.get('pnl_usd', 0):+.2f}" if t.get("pnl_usd") is not None else "open"
            lines.append(f"{emoji} {t['exchange']} {t['market']} → {pnl_str}")
        await update.message.reply_text(
            f"📜 Last {len(lines)} trades:\n" + "\n".join(lines)
        )

    async def _cmd_learning(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        report = await self._db.get_last_learning_report()
        weights = await self._db.get_signal_weights()
        wstr = "\n".join(f"  • {k}: {v:.2f}" for k, v in weights.items())
        if not report:
            await update.message.reply_text(f"📊 No learning report yet.\n\nCurrent weights:\n{wstr}")
            return
        msg = (
            f"🧠 *Last Learning Report* ({str(report.get('generated_at'))[:10]})\n"
            f"Win rate: {float(report.get('win_rate', 0)) * 100:.1f}% | "
            f"P&L: ${report.get('total_pnl', 0):.2f}\n"
            f"Applied: {'✅' if report.get('applied') else '❌'}\n\n"
            f"*Analysis:* {report.get('claude_analysis', 'N/A')}\n\n"
            f"*Current weights:*\n{wstr}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_setfloor(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        if not ctx.args:
            await update.message.reply_text("Usage: /setfloor <pct>  e.g. /setfloor 20"); return
        try:
            pct = float(ctx.args[0])
            assert 5 <= pct <= 50
        except (ValueError, AssertionError):
            await update.message.reply_text("❌ Floor must be between 5 and 50"); return
        # Note: config is frozen — this would require dynamic override in Phase 4
        await update.message.reply_text(
            f"⚠️ Floor update to {pct}% noted. "
            f"Dynamic floor update requires Phase 4 implementation. "
            f"For now, update CAPITAL_FLOOR_PCT in .env and restart."
        )

    async def _cmd_setthreshold(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        await update.message.reply_text(
            "⚠️ Dynamic threshold update requires Phase 4. "
            "Update CONFIDENCE_THRESHOLD in .env and restart."
        )

    async def _cmd_stop_exchange(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        exchange = ctx.args[0].lower() if ctx.args else ""
        if exchange not in ("binance", "polymarket", "hyperliquid", "all"):
            await update.message.reply_text("Usage: /stop binance|polymarket|hyperliquid"); return
        # TODO Phase 4: implement per-exchange disable flag
        await update.message.reply_text(
            f"⚠️ Per-exchange disable for {exchange} — Phase 4 feature. "
            f"Use /pause to stop all trading immediately."
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update): await self._deny(update); return
        msg = (
            "🤖 *NANORCA Commands*\n"
            "/status — Current state & capital\n"
            "/pause — Stop all trading\n"
            "/resume — Restart trading\n"
            "/report [7d] — Trade breakdown\n"
            "/capital — Money overview\n"
            "/positions — Open trades\n"
            "/markets — Live prices from all exchanges\n"
            "/history [n] — Last N trades\n"
            "/learning — Weekly AI analysis\n"
            "/setfloor <pct> — Change floor %\n"
            "/setthreshold <n> — Change confidence\n"
            "/stop binance|polymarket|hyperliquid\n"
            "/help — This message"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    # ── Programmatic send methods ─────────────────────────────────────────

    async def send_info(self, text: str) -> None:
        """Send an informational message to the owner."""
        await self._send(text)

    async def send_warning(self, text: str) -> None:
        """Send a warning message."""
        await self._send(f"⚠️ {text}")

    async def send_floor_alert(self, capital_tracker) -> None:
        """Send the capital floor emergency alert."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        msg = (
            f"🚨 *NANORCA — CAPITAL PROTECTION TRIGGERED*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Starting capital: ${self._config.starting_capital_usd:.2f}\n"
            f"Current capital:  ${capital_tracker.current_capital:.2f}\n"
            f"Floor:            ${capital_tracker.floor_capital:.2f} ({self._config.capital_floor_pct}%)\n\n"
            f"⛔ ALL TRADING PAUSED\n"
            f"All open positions closed at market.\n\n"
            f"To restart: /resume\n"
            f"To see breakdown: /report\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await self._send(msg)

    async def _send(self, text: str) -> None:
        if not self._app:
            log.warning(f"Telegram not started, would send: {text[:100]}")
            return
        try:
            await self._app.bot.send_message(
                chat_id=self._config.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            log.error(f"Telegram send failed: {e}")

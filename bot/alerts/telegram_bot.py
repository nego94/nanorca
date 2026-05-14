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

    def __init__(self, config, db, circuit_breaker, capital_tracker, order_router,
                 suggestion_store=None, extra_markets=None,
                 signal_builder=None, claude_brain=None) -> None:
        self._config = config
        self._db = db
        self._cb = circuit_breaker
        self._cap = capital_tracker
        self._router = order_router
        self._suggestions = suggestion_store
        self._extra_markets = extra_markets
        self._signal_builder = signal_builder
        self._claude_brain = claude_brain
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
            ("readmarkets",    self._cmd_markets),
            ("suggestion",     self._cmd_suggestion),
            ("suggest",        self._cmd_suggestion),
            ("check",          self._cmd_check),
            ("listpriority",   self._cmd_listpriority),
            ("removepriority", self._cmd_removepriority),
            ("history",      self._cmd_history),
            ("learning",     self._cmd_learning),
            ("setfloor",     self._cmd_setfloor),
            ("setthreshold", self._cmd_setthreshold),
            ("setmode",      self._cmd_setmode),
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

    # ── Authorization ─────────────────────────────────────────────────────

    def _user_id(self, update: Update) -> str:
        return str(update.effective_user.id) if update.effective_user else ""

    def _is_owner(self, update: Update) -> bool:
        """Owner = TELEGRAM_CHAT_ID. Can run control commands (pause, resume, setfloor)."""
        uid = self._user_id(update)
        return uid == str(self._config.telegram_chat_id)

    def _is_authorized(self, update: Update) -> bool:
        """
        Authorized = owner OR any user listed in TELEGRAM_ALLOWED_USER_IDS.
        Authorized users can run read-only commands (status, capital, markets, etc.).
        """
        uid = self._user_id(update)
        if uid == str(self._config.telegram_chat_id):
            return True
        return uid in self._config.telegram_allowed_user_ids

    async def _deny(self, update: Update) -> None:
        await update.message.reply_text("⛔ Unauthorized.")

    async def _deny_owner_only(self, update: Update) -> None:
        await update.message.reply_text("⛔ This command is owner-only.")

    # ── Commands ──────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        from risk.circuit_breaker import BotState
        from risk.trading_plan import TradingMode, format_plan_summary

        state_emoji = {
            "running": "🟢", "paused_daily_loss": "🟡", "paused_consecutive": "🟡",
            "paused_floor_hit": "🔴", "paused_manual": "⏸️",
        }
        s = self._cb.state
        mode = TradingMode(getattr(self._config, 'trading_mode', 'nanorca_decide'))
        plan_line = format_plan_summary(mode, self._cap.current_capital)

        # Fetch real exchange balances from Go executor
        balances = await self._router.get_balances()
        bal_lines = []
        real_total = 0.0
        ex_icons = {"binance": "🟡", "hyperliquid": "🔵", "polymarket": "🟣"}
        for b in balances:
            icon = ex_icons.get(b["exchange"], "⚪")
            if b["available"]:
                bal_lines.append(f"  {icon} {b['exchange'].capitalize()}: ${b['total_usd']:.2f}")
                real_total += b["total_usd"]
            else:
                note = b["error"] or "unavailable"
                bal_lines.append(f"  {icon} {b['exchange'].capitalize()}: — ({note})")

        bal_section = (
            f"*💰 Real Balances*\n" + "\n".join(bal_lines) +
            f"\n  ━━━ Total: *${real_total:.2f}*"
        ) if bal_lines else "  _(executor not connected)_"

        msg = (
            f"*NANORCA Status*\n"
            f"State: {state_emoji.get(s.value, '❓')} `{s.value}`\n"
            f"Mode:  {'📄 PAPER' if self._config.paper_trading else '🔴 LIVE'}\n\n"
            f"{bal_section}\n\n"
            f"*📊 Bot Tracker*\n"
            f"Capital:    ${self._cap.current_capital:.2f} ({self._cap.pct_from_start:+.1f}%)\n"
            f"Daily P&L:  ${self._cap.daily_pnl:+.2f} "
            f"(drawdown: {self._cap.daily_drawdown_pct:.2f}%)\n"
            f"Floor:      ${self._cap.floor_capital:.2f} "
            f"({self._cap.pct_from_floor:.1f}% above)\n\n"
            f"{plan_line}"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
        await self._cb.pause_manual()
        await update.message.reply_text("⏸️ Trading paused. Send /resume to restart.")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
        resumed = await self._cb.resume()
        if resumed:
            await update.message.reply_text("▶️ Trading resumed.")
        else:
            await update.message.reply_text("ℹ️ Bot is already running.")

    async def _cmd_report(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
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
        if not self._is_authorized(update): await self._deny(update); return

        balances = await self._router.get_balances()
        ex_icons = {"binance": "🟡", "hyperliquid": "🔵", "polymarket": "🟣"}
        real_total = 0.0
        bal_lines = []
        for b in balances:
            icon = ex_icons.get(b["exchange"], "⚪")
            name = b["exchange"].capitalize()
            if b["available"]:
                bal_lines.append(
                    f"  {icon} {name}\n"
                    f"     USDT free: ${b['usdt']:.4f}\n"
                    f"     Total USD: ${b['total_usd']:.4f}"
                )
                real_total += b["total_usd"]
            else:
                bal_lines.append(f"  {icon} {name}: — ({b['error'] or 'unavailable'})")

        exchange_section = "\n".join(bal_lines) if bal_lines else "  _(executor not connected)_"

        msg = (
            f"💰 *Capital Overview*\n\n"
            f"*Exchange Balances (real)*\n"
            f"{exchange_section}\n"
            f"  ━━━━━━━━━━━━━━━━\n"
            f"  Total on-chain: *${real_total:.2f}*\n\n"
            f"*Bot Tracker*\n"
            f"  Starting: ${self._config.starting_capital_usd:.2f}\n"
            f"  Current:  ${self._cap.current_capital:.2f} ({self._cap.pct_from_start:+.1f}%)\n"
            f"  Floor:    ${self._cap.floor_capital:.2f} ({self._config.capital_floor_pct}% of start)\n"
            f"  Above floor: {self._cap.pct_from_floor:.1f}%"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
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
        """Show market suggestions (50-64 confidence) + top live prices."""
        if not self._is_authorized(update): await self._deny(update); return

        # ── Part 1: Market suggestions (50–64 confidence band) ────────────
        if self._suggestions:
            suggestion_msg = self._suggestions.format_telegram(self._config.paper_trading)
            await update.message.reply_text(suggestion_msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "📊 *Market Suggestions*\nSuggestion store not initialised yet.",
                parse_mode=ParseMode.MARKDOWN
            )

        # ── Part 2: Live top prices from scanner ──────────────────────────
        try:
            priority = self._config.priority_markets[:10]
            snapshots = await self._router.scan_markets(priority)
            bn = [s for s in snapshots if s.get("exchange") == "binance" and s.get("available")]
            if not bn:
                await update.message.reply_text("📡 No live price data yet.")
                return
            lines = ["📡 *Live Prices (top scanned markets):*"]
            for s in sorted(bn, key=lambda x: x.get("volume_24h", 0), reverse=True)[:10]:
                market = s.get("market", "?")
                price  = s.get("price", 0)
                vol    = s.get("volume_24h", 0)
                fr     = s.get("funding_rate", 0)
                fr_str = f" | fr:{fr*100:.3f}%" if fr else ""
                vol_str = f" | vol:${vol/1e6:.0f}M" if vol > 0 else ""
                lines.append(f"  `{market}` ${price:,.4f}{fr_str}{vol_str}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Price scan error: {e}")

    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
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
        if not self._is_authorized(update): await self._deny(update); return
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
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
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
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
        await update.message.reply_text(
            "⚠️ Dynamic threshold update requires Phase 4. "
            "Update CONFIDENCE_THRESHOLD in .env and restart."
        )

    async def _cmd_setmode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
        from risk.trading_plan import TradingMode, get_plan_params
        valid = ["nanorca_decide", "conservative", "aggressive", "hybrid"]
        current = getattr(self._config, 'trading_mode', 'nanorca_decide')
        if not ctx.args or ctx.args[0] not in valid:
            lines = []
            for m in valid:
                mode = TradingMode(m)
                p = get_plan_params(mode, self._cap.current_capital)
                marker = " ← current" if m == current else ""
                if m == "nanorca_decide":
                    lines.append(f"  `{m}` — Claude sizes freely{marker}")
                else:
                    lines.append(
                        f"  `{m}` — {p['risk_pct']:.0f}% risk, "
                        f"{p['leverage']:.0f}x lev, {p['daily_goal_pct']:.1f}% goal/day{marker}"
                    )
            await update.message.reply_text(
                f"*Trading Modes*\n" + "\n".join(lines) +
                f"\n\nTo switch:\n"
                f"1\\. Edit `TRADING_MODE=<mode>` in `.env`\n"
                f"2\\. Run `docker compose restart bot`\n\n"
                f"Usage: `/setmode hybrid`",
                parse_mode="MarkdownV2"
            )
            return
        new_mode = ctx.args[0]
        await update.message.reply_text(
            f"✅ *To switch to `{new_mode}`:*\n\n"
            f"1\\. Edit `.env` → `TRADING_MODE={new_mode}`\n"
            f"2\\. `docker compose restart bot`\n\n"
            f"Current mode: `{current}`",
            parse_mode="MarkdownV2"
        )

    async def _cmd_suggestion(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """On-demand coin analysis. Usage: /suggestion PEPE or /suggest SOL"""
        import time as _time
        if not self._is_authorized(update): await self._deny(update); return

        if not ctx.args:
            await update.message.reply_text(
                "Usage: `/suggestion TOKEN`\n"
                "Example: `/suggestion PEPE` or `/suggest SOL`\n\n"
                "Gives you an on-demand analysis with direction, confidence,\n"
                "entry/exit prediction and reasoning. Never auto-executed.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if not self._claude_brain:
            await update.message.reply_text("❌ Claude brain not available.")
            return

        raw_symbol = ctx.args[0].upper().strip().lstrip("$")
        for suffix in ("USDT", "BUSD", "USDC", "/USDT", "-USDT"):
            if raw_symbol.endswith(suffix):
                raw_symbol = raw_symbol[:-len(suffix)]
                break
        symbol = raw_symbol  # base only e.g. "PEPE"

        await update.message.reply_text(f"🔍 Scanning `{symbol}USDT` — give me a moment...", parse_mode=ParseMode.MARKDOWN)

        # Scan the requested coin + BTC/ETH for market context
        try:
            snaps = await self._router.scan_markets([symbol, "BTC", "ETH"])
        except Exception as e:
            await update.message.reply_text(f"❌ Scan failed: {e}")
            return

        # Find the requested coin snapshot
        coin_snap = next((s for s in snaps if s.get("market", "").upper() == f"{symbol}USDT"
                         and s.get("available")), None)

        if not coin_snap or coin_snap.get("price", 0) == 0:
            await update.message.reply_text(
                f"❌ No data for `{symbol}USDT`.\n\n"
                f"This coin may not exist on Binance Futures, or the symbol is wrong.\n"
                f"Try: `/check {symbol}` first to add it to the scan list, then wait a few cycles.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Market context from BTC/ETH snapshots
        btc_snap = next((s for s in snaps if "BTCUSDT" in s.get("market", "")), {})
        eth_snap = next((s for s in snaps if "ETHUSDT" in s.get("market", "")), {})

        # Check scan age from signal_builder price history
        scan_age_minutes = 0.0
        if self._signal_builder and hasattr(self._signal_builder, "_price_history"):
            history = self._signal_builder._price_history.get(f"{symbol}USDT", [])
            if len(history) >= 2:
                oldest_ts = history[0][1]
                scan_age_minutes = (_time.monotonic() - oldest_ts) / 60

        btc_price = btc_snap.get("price", 0)
        eth_price = eth_snap.get("price", 0)
        market_bias = "neutral"
        btc_trend = "stable"
        if self._signal_builder and hasattr(self._signal_builder, "_price_history"):
            btc_hist = self._signal_builder._price_history.get("BTCUSDT", [])
            if len(btc_hist) >= 2:
                change = (btc_hist[-1][0] - btc_hist[0][0]) / btc_hist[0][0] * 100
                btc_trend = f"+{change:.2f}%" if change > 0 else f"{change:.2f}%"
                market_bias = "bullish" if change > 0.2 else ("bearish" if change < -0.2 else "neutral")

        market_context = {
            "btc_price": btc_price,
            "eth_price": eth_price,
            "btc_trend": btc_trend,
            "market_bias": market_bias,
        }

        # Call Claude for analysis
        result = await self._claude_brain.analyze_on_demand(
            symbol=symbol,
            snapshot=coin_snap,
            market_context=market_context,
            scan_age_minutes=scan_age_minutes,
        )

        if not result:
            await update.message.reply_text("❌ Claude analysis failed — try again in a moment.")
            return

        # Format result
        direction   = result.get("direction", "neutral").upper()
        confidence  = result.get("confidence", 0)
        entry       = result.get("entry_zone", coin_snap.get("price", 0))
        target      = result.get("target_price", 0)
        stop        = result.get("stop_price", 0)
        tgt_pct     = result.get("target_pct", 0)
        stp_pct     = result.get("stop_pct", 0)
        hold        = result.get("hold_estimate", "?")
        risk        = result.get("risk_level", "?").upper()
        reasoning   = result.get("reasoning", "—")
        data_note   = result.get("data_note", "")
        quality     = result.get("data_quality", "?")

        dir_emoji = {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT", "NEUTRAL": "⚪ NEUTRAL"}.get(direction, direction)
        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(risk, "⚪")
        conf_bar = "█" * (confidence // 10) + "░" * (10 - confidence // 10)

        msg = (
            f"🔍 *ON-DEMAND ANALYSIS — {symbol}USDT*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Current: `${coin_snap.get('price', 0):.6f}`\n"
            f"📊 Direction: *{dir_emoji}*\n"
            f"🧠 Confidence: `{confidence}/100` {conf_bar}\n\n"
            f"🎯 Entry zone: `${entry:.6f}`\n"
            f"✅ Target: `${target:.6f}` (+{tgt_pct:.1f}%)\n"
            f"🛑 Stop: `${stop:.6f}` (-{stp_pct:.1f}%)\n"
            f"⏱ Hold: `{hold}`\n"
            f"{risk_emoji} Risk: `{risk}`\n\n"
            f"📋 *Reasoning:*\n{reasoning}\n\n"
            f"📡 Data quality: `{quality}` ({scan_age_minutes:.1f} min history)"
        )
        if data_note:
            msg += f"\n⚠️ _{data_note}_"
        msg += "\n\n_This is advisory only — not auto-executed by the bot._"

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_check(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Add a coin to the extra scan list. Usage: /check SOL or /check PEPE"""
        if not self._is_authorized(update): await self._deny(update); return
        if not ctx.args:
            await update.message.reply_text(
                "Usage: `/check TOKEN`\nExample: `/check PEPE` or `/check WIF`\n\n"
                "Adds the coin to the scan list alongside the automatic top-25.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        if not self._extra_markets:
            await update.message.reply_text("❌ Extra markets store not initialised.")
            return
        symbol = ctx.args[0]
        ok, msg = self._extra_markets.add(symbol)
        current = self._extra_markets.get()
        reply = msg
        if ok and current:
            reply += f"\n\n📋 Current extra list ({len(current)}/10): {', '.join(f'`{m}USDT`' for m in current)}"
        await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_listpriority(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current extra scan list."""
        if not self._is_authorized(update): await self._deny(update); return
        if not self._extra_markets:
            await update.message.reply_text("Extra markets store not initialised.")
            return
        await update.message.reply_text(
            self._extra_markets.format_telegram(),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cmd_removepriority(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Remove a coin from extra scan list. Usage: /removepriority SOL"""
        if not self._is_authorized(update): await self._deny(update); return
        if not ctx.args:
            await update.message.reply_text("Usage: `/removepriority TOKEN`\nExample: `/removepriority PEPE`", parse_mode=ParseMode.MARKDOWN)
            return
        if not self._extra_markets:
            await update.message.reply_text("❌ Extra markets store not initialised.")
            return
        ok, msg = self._extra_markets.remove(ctx.args[0])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_stop_exchange(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        if not self._is_owner(update): await self._deny_owner_only(update); return
        exchange = ctx.args[0].lower() if ctx.args else ""
        if exchange not in ("binance", "polymarket", "hyperliquid", "all"):
            await update.message.reply_text("Usage: /stop binance|polymarket|hyperliquid"); return
        # TODO Phase 4: implement per-exchange disable flag
        await update.message.reply_text(
            f"⚠️ Per-exchange disable for {exchange} — Phase 4 feature. "
            f"Use /pause to stop all trading immediately."
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update): await self._deny(update); return
        msg = (
            "🤖 *NANORCA Commands*\n\n"
            "*Read-only (all members):*\n"
            "/status — State, real balances, plan\n"
            "/capital — Per-exchange balance detail\n"
            "/markets — Market suggestions (50–64 conf) + live prices\n"
            "/readmarkets — Same as /markets\n"
            "/suggestion TOKEN — On-demand analysis: direction, confidence, entry/exit\n"
            "/suggest TOKEN — Same as /suggestion\n"
            "/check TOKEN — Add a coin to your extra scan list (e.g. /check PEPE)\n"
            "/listpriority — Show your extra scan list\n"
            "/removepriority TOKEN — Remove a coin from extra scan list\n"
            "/positions — Open paper/live trades\n"
            "/report [7d] — Trade P&L breakdown\n"
            "/history [n] — Last N trades\n"
            "/learning — Weekly AI analysis & signal weights\n"
            "/help — This message\n\n"
            "*Owner only:*\n"
            "/pause — Stop all trading\n"
            "/resume — Restart trading\n"
            "/setmode — View/switch trading plan\n"
            "/setfloor <pct> — Change capital floor %\n"
            "/stop binance|polymarket|hyperliquid — Disable one exchange"
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

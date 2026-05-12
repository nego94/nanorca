"""
bot/alerts/daily_report.py — Midnight daily summary generator and sender.

Called by APScheduler every day at 00:00 owner's timezone (Asia/Makassar).
Assembles a full trade summary and sends via Telegram.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("nanorca.alerts.daily_report")


async def send_daily_report(db, telegram, capital_tracker) -> None:
    """
    Assemble and send the midnight daily report.

    Format mirrors the spec exactly:
      📊 NANORCA Daily Report — {date}
      ━━━━━━━━━━━━━━━━━━━━━━
      💰 Capital: ${current} ({+/-pct}% from start)
      ...
    """
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    try:
        trades = await db.get_trades_in_range(day_ago, now)
    except Exception as e:
        log.error(f"Daily report: failed to fetch trades — {e}")
        await telegram.send_warning(f"⚠️ Daily report failed: {e}")
        return

    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t.get("win")]
    losses = [t for t in closed if not t.get("win")]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    total_pnl = round(sum(t.get("pnl_usd", 0) or 0 for t in closed), 2)

    # Best and worst trades
    best = max(closed, key=lambda t: t.get("pnl_usd") or 0, default=None)
    worst = min(closed, key=lambda t: t.get("pnl_usd") or 0, default=None)
    best_str  = f"{best['market']} +${best['pnl_usd']:.2f}"  if best else "N/A"
    worst_str = f"{worst['market']} -${abs(worst['pnl_usd']):.2f}" if worst else "N/A"

    # Average hold time
    holds = [t.get("hold_minutes", 0) or 0 for t in closed]
    avg_hold = round(sum(holds) / len(holds), 1) if holds else 0

    # Days until Sunday learning
    days_until_sunday = (6 - now.weekday()) % 7 or 7

    pct_str = f"{capital_tracker.pct_from_start:+.1f}%"
    date_str = now.strftime("%b %d")

    msg = (
        f"📊 *NANORCA Daily Report — {date_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital: ${capital_tracker.current_capital:.2f} ({pct_str} from start)\n"
        f"📈 Today: ${total_pnl:+.2f} | {len(wins)}W / {len(losses)}L ({win_rate}%)\n"
        f"🔄 Trades: {len(trades)} | Avg hold: {avg_hold}min\n"
        f"🏆 Best:  {best_str}\n"
        f"💀 Worst: {worst_str}\n"
        f"\n"
        f"⚙️ System: ✅ Running\n"
        f"📅 Weekly learning: {days_until_sunday} days away\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    await telegram.send_info(msg)
    log.info(f"Daily report sent: {len(trades)} trades, P&L=${total_pnl}")

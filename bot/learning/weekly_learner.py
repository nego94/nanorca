"""
bot/learning/weekly_learner.py — Weekly self-improvement loop.

Runs every Sunday at 00:00 UTC.
1. Pulls 7 days of trade data from DB
2. Sends to Claude (deep model) for analysis
3. Gets recommended signal weight changes
4. Applies automatically if confidence >= 70
5. Alerts owner via Telegram with the analysis
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("nanorca.learning.weekly")

WEEKLY_LEARNING_PROMPT = """You are analyzing the past 7 days of trading performance for NANORCA.

## TRADE DATA (last 7 days)
{trades_json}

## CURRENT SIGNAL WEIGHTS
{current_weights_json}

## YOUR TASK
1. Identify which signal types had the highest win rate
2. Identify which signal types correlated with losses
3. Identify any market conditions that preceded losses (time of day, volatility, etc.)
4. Recommend new signal weights (must sum to 1.0)
5. Suggest any strategy adjustments

## RESPOND IN THIS EXACT JSON FORMAT:
{{
  "analysis": "<3–5 sentence summary of what worked and what didn't>",
  "recommended_weights": {{
    "price_gap_polymarket": <float>,
    "funding_rate_hyperliquid": <float>,
    "binance_momentum": <float>,
    "sentiment_news": <float>,
    "volume_spike": <float>
  }},
  "strategy_notes": "<specific adjustments to make>",
  "markets_to_avoid": ["market1", "market2"],
  "confidence_in_analysis": <integer 0–100>
}}"""


class WeeklyLearner:
    """Runs the weekly self-improvement cycle."""

    def __init__(self, db, claude_brain, telegram, config) -> None:
        self._db = db
        self._brain = claude_brain
        self._telegram = telegram
        self._config = config

    async def run(self) -> None:
        """Entry point called by APScheduler every Sunday at 00:00 UTC."""
        log.info("🧠 Weekly learning cycle starting...")

        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=7)

        try:
            trades = await self._db.get_trades_in_range(period_start, now)
        except Exception as e:
            log.error(f"Failed to fetch trades for learning: {e}")
            await self._telegram.send_warning(f"⚠️ Weekly learner: failed to fetch trades — {e}")
            return

        if len(trades) < 5:
            msg = f"📊 Weekly learner: only {len(trades)} trades this week — not enough data to retrain."
            log.info(msg)
            await self._telegram.send_info(msg)
            return

        current_weights = await self._db.get_signal_weights()
        trades_json = json.dumps(trades, default=str, indent=2)
        weights_json = json.dumps(current_weights, indent=2)

        log.info(f"Sending {len(trades)} trades to Claude for weekly analysis...")
        result = await self._brain.analyze_weekly(trades_json, weights_json)

        if result is None:
            await self._telegram.send_warning("⚠️ Weekly learner: Claude analysis failed — weights unchanged.")
            return

        # Compile report
        total_trades = len(trades)
        closed = [t for t in trades if t.get("status") == "closed"]
        wins = sum(1 for t in closed if t.get("win"))
        win_rate = round(wins / len(closed) * 100, 1) if closed else 0.0
        total_pnl = round(sum(t.get("pnl_usd", 0) or 0 for t in closed), 2)

        confidence = result.get("confidence_in_analysis", 0)
        recommended = result.get("recommended_weights", {})

        # Auto-apply if Claude is confident enough
        applied = False
        if confidence >= 70 and recommended:
            weight_sum = sum(recommended.values())
            if abs(weight_sum - 1.0) < 0.01:  # weights must sum to ~1.0
                await self._db.update_signal_weights(recommended)
                applied = True
                log.info(f"New signal weights applied automatically (confidence={confidence})")
            else:
                log.warning(f"Weights don't sum to 1.0 ({weight_sum:.4f}) — not applying")

        # Save report to DB
        report = {
            "period_start": period_start,
            "period_end": now,
            "total_trades": total_trades,
            "win_rate": win_rate / 100,
            "total_pnl": total_pnl,
            "claude_analysis": result.get("analysis", ""),
            "weight_changes": {"before": current_weights, "after": recommended},
            "applied": applied,
            "confidence_in_analysis": confidence,
        }
        await self._db.save_learning_report(report)

        # Send Telegram summary
        weight_str = "\n".join(f"  • {k}: {v:.2f}" for k, v in recommended.items())
        msg = (
            f"📊 *Weekly Learning Report*\n"
            f"Period: last 7 days\n"
            f"Trades: {total_trades} | Win rate: {win_rate}%\n"
            f"P&L: ${total_pnl}\n\n"
            f"*Analysis:* {result.get('analysis', 'N/A')}\n\n"
            f"*New signal weights:*\n{weight_str}\n\n"
            f"Applied automatically: {'✅ Yes' if applied else f'❌ No (confidence={confidence}% < 70%)'}\n"
            f"*Strategy notes:* {result.get('strategy_notes', 'N/A')}"
        )
        await self._telegram.send_info(msg)
        log.info(f"Weekly learning complete — applied={applied}, confidence={confidence}")

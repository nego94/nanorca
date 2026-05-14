"""
bot/data/suggestion_store.py — In-memory store for market suggestions (confidence 50–64).

When Claude sees a signal with confidence 50–64 it's too uncertain to auto-trade but
worth surfacing to the human via /markets or /readmarkets. This store keeps the top-5
suggestions (by confidence) across scan cycles.

These are NEVER auto-executed — they are advisory only.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any


# Markets always excluded from suggestions regardless of confidence.
# BTC: min futures lot = 0.001 BTC ≈ $100 — too large for small capital (<$200)
# ETH: lower % volatility than altcoins; better alts exist for the same signals
_EXCLUDED_MARKETS = frozenset({
    "BTCUSDT", "BTCBUSD", "BTCFDUSD",
    "ETHUSDT", "ETHBUSD", "ETHFDUSD",
})


class SuggestionStore:
    """Holds up to MAX_SUGGESTIONS market suggestions from the 50-64 confidence band."""

    MAX_SUGGESTIONS = 5
    MAX_AGE_SECONDS = 3600  # drop suggestions older than 1 hour

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, decision: dict[str, Any], entry_price: float) -> None:
        """
        Add or update a suggestion. Keeps top-MAX_SUGGESTIONS by confidence.
        Replaces an existing suggestion for the same market+direction.
        BTC and ETH are excluded — min lot too large / too low % volatility for small capital.
        """
        confidence = decision.get("confidence", 0)
        market     = decision.get("market", "")
        direction  = (decision.get("direction") or "long").upper()
        if not market or confidence < 50 or confidence >= 65:
            return
        if market.upper() in _EXCLUDED_MARKETS:
            return  # skip — min lot too large or low % volatility for small capital

        suggestion = {
            "market":           market,
            "direction":        direction,
            "confidence":       confidence,
            "entry_price":      entry_price,
            "target_profit_pct": decision.get("target_profit_pct", 1.0),
            "stop_loss_pct":    decision.get("stop_loss_pct", 2.0),
            "expected_hold_minutes": decision.get("expected_hold_minutes", 120),
            "reasoning":        decision.get("reasoning", ""),
            "signals_used":     decision.get("signals_used", []),
            "timestamp":        time.time(),
        }

        # Replace existing entry for same market+direction
        self._items = [
            s for s in self._items
            if not (s["market"] == market and s["direction"] == direction)
        ]
        self._items.append(suggestion)

        # Keep top-MAX_SUGGESTIONS by confidence
        self._items.sort(key=lambda s: s["confidence"], reverse=True)
        self._items = self._items[:self.MAX_SUGGESTIONS]

    def get_active(self) -> list[dict[str, Any]]:
        """Return suggestions newer than MAX_AGE_SECONDS, sorted by confidence desc."""
        cutoff = time.time() - self.MAX_AGE_SECONDS
        return [s for s in self._items if s["timestamp"] >= cutoff]

    def format_telegram(self, paper_mode: bool) -> str:
        """Format all active suggestions as a Telegram message."""
        active = self.get_active()
        mode_tag = "📄 PAPER" if paper_mode else "🔴 LIVE"

        if not active:
            return (
                f"📊 *Market Suggestions* [{mode_tag}]\n"
                "─────────────────────\n"
                "No suggestions yet.\n\n"
                "_Suggestions appear when Claude sees a 50–64 confidence signal._\n"
                "_Below 65 → not auto-traded, shown here for your review._"
            )

        lines = [f"📊 *Market Suggestions* [{mode_tag}] — top {len(active)}\n"
                 "_Confidence 50–64: NOT auto-traded. Human review required._\n"
                 "─────────────────────"]

        emojis = {("LONG","BUY"): "📈", ("SHORT","SELL"): "📉"}
        for i, s in enumerate(active, 1):
            age_min = int((time.time() - s["timestamp"]) / 60)
            arrow   = "📈" if s["direction"] in ("LONG","BUY") else "📉"
            entry   = s["entry_price"]
            tgt_pct = s["target_profit_pct"]
            stp_pct = s["stop_loss_pct"]
            hold    = s["expected_hold_minutes"]

            if s["direction"] in ("LONG","BUY"):
                tgt_price = entry * (1 + tgt_pct / 100)
                stp_price = entry * (1 - stp_pct / 100)
            else:
                tgt_price = entry * (1 - tgt_pct / 100)
                stp_price = entry * (1 + stp_pct / 100)

            signals_str = ", ".join(s["signals_used"][:2]) if s["signals_used"] else "—"

            lines.append(
                f"\n*{i}. {arrow} {s['market']} — {s['direction']}*\n"
                f"   🧠 Confidence: {s['confidence']}/100\n"
                f"   📍 Entry zone: ~${entry:.4f}\n"
                f"   🎯 Target: +{tgt_pct:.1f}% → ~${tgt_price:.4f}\n"
                f"   🛑 Stop: -{stp_pct:.1f}% → ~${stp_price:.4f}\n"
                f"   ⏱ Hold: ~{hold} min\n"
                f"   📋 {s['reasoning'][:120]}...\n"
                f"   ⚡ Signals: {signals_str}\n"
                f"   🕐 {age_min}m ago"
            )

        lines.append("\n─────────────────────")
        lines.append("⚠️ *These are suggestions only — the bot will NOT open these.*")
        lines.append("Use your own judgement to open on Binance manually.")
        return "\n".join(lines)

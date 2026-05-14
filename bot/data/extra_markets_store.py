"""
bot/data/extra_markets_store.py — User-requested extra markets via /check TOKEN.

Holds the list of coins the user has manually added for scanning beyond the
automatic top-25 Binance list. Persists for the session (reset on restart).

Usage:
  /check SOL      → adds SOL to extra scan list
  /check PEPE     → adds PEPE to extra scan list
  /listpriority   → shows current extra list
  /removepriority SOL → removes SOL from extra list

Symbols are stored as base symbols (e.g. "SOL" not "SOLUSDT").
The Go executor appends "USDT" when scanning.
"""
from __future__ import annotations

import re


_MAX_EXTRA = 10  # cap user-added markets per session


def _normalise(symbol: str) -> str | None:
    """
    Convert user input to a clean base symbol.
    Accepts: SOL, SOLUSDT, sol/usdt, sol-usdt, $SOL, etc.
    Returns: "SOL" (uppercase base only) or None if invalid.
    """
    s = symbol.upper().strip().lstrip("$")
    # Strip common quote suffixes
    for suffix in ("USDT", "BUSD", "USDC", "/USDT", "-USDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    # Must be 2-10 uppercase letters/numbers
    if not re.match(r"^[A-Z0-9]{2,10}$", s):
        return None
    return s


class ExtraMarketsStore:
    """Holds user-requested scan markets for the current session."""

    def __init__(self) -> None:
        self._markets: list[str] = []  # ordered, deduped base symbols

    def add(self, symbol: str) -> tuple[bool, str]:
        """
        Add a symbol. Returns (success, message).
        """
        base = _normalise(symbol)
        if base is None:
            return False, f"❌ Invalid symbol: `{symbol}`. Use e.g. SOL, PEPE, DOGE"
        if base in self._markets:
            return False, f"ℹ️ `{base}` is already in your extra scan list"
        if len(self._markets) >= _MAX_EXTRA:
            return False, (
                f"❌ Extra scan list is full ({_MAX_EXTRA} max). "
                f"Use /removepriority to remove one first.\n"
                f"Current: {', '.join(self._markets)}"
            )
        self._markets.append(base)
        return True, f"✅ `{base}USDT` added to scan list"

    def remove(self, symbol: str) -> tuple[bool, str]:
        """Remove a symbol. Returns (success, message)."""
        base = _normalise(symbol)
        if base is None or base not in self._markets:
            return False, f"❌ `{symbol}` not in extra scan list"
        self._markets.remove(base)
        return True, f"✅ `{base}` removed from scan list"

    def get(self) -> list[str]:
        """Return current list of base symbols."""
        return list(self._markets)

    def format_telegram(self) -> str:
        if not self._markets:
            return (
                "📋 *Extra scan list is empty*\n"
                "Use `/check TOKEN` to add coins you want to watch.\n"
                "Example: `/check PEPE` or `/check WIF`\n\n"
                "_Top-25 by volume are already scanned automatically._"
            )
        lines = ["📋 *Extra scan markets (your additions):*"]
        for i, m in enumerate(self._markets, 1):
            lines.append(f"  {i}. `{m}USDT`")
        lines.append(f"\n_Top-25 by volume are also scanned automatically._")
        lines.append(f"Use `/removepriority TOKEN` to remove one.")
        return "\n".join(lines)

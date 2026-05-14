"""
bot/data/extra_markets_store.py — User-requested extra markets via /check TOKEN.

Total scan cap: 30 markets max (top-25 by volume + 5 user additions).
This keeps each scan cycle under ~16s, well within the 25s gRPC timeout
and the 30s scan interval.

  Auto-scanned:  top-25 Binance USDT-M pairs by volume (BINANCE_SCAN_TOP_N)
  User-added:    up to 5 extra coins via /check
  Total max:     30 markets per cycle

Symbols stored as base (e.g. "SOL"). Go executor appends "USDT".
"""
from __future__ import annotations

import re

# Hard cap: top-25 auto + 5 user = 30 total.
# At 30 markets, parallel scan takes ~12-16s — fits in 25s gRPC timeout + 30s cycle.
_MAX_EXTRA = 5
_TOTAL_MAX = 30   # shown in warnings so user understands the limit
_AUTO_SCAN = 25   # from BINANCE_SCAN_TOP_N


def _normalise(symbol: str) -> str | None:
    s = symbol.upper().strip().lstrip("$")
    for suffix in ("USDT", "BUSD", "USDC", "/USDT", "-USDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not re.match(r"^[A-Z0-9]{2,10}$", s):
        return None
    return s


class ExtraMarketsStore:
    """Holds user-requested scan markets for the current session (reset on restart)."""

    def __init__(self) -> None:
        self._markets: list[str] = []

    def add(self, symbol: str) -> tuple[bool, str]:
        """
        Add a symbol to the extra scan list.
        Returns (success, message).
        """
        base = _normalise(symbol)
        if base is None:
            return False, f"❌ Invalid symbol `{symbol}` — use e.g. PEPE, WIF, BONK"
        if base in self._markets:
            return False, f"ℹ️ `{base}` is already in your extra scan list"
        if len(self._markets) >= _MAX_EXTRA:
            current = ", ".join(f"`{m}`" for m in self._markets)
            return False, (
                f"⚠️ Extra scan list is full — max {_MAX_EXTRA} additions "
                f"({_AUTO_SCAN} auto + {_MAX_EXTRA} manual = {_TOTAL_MAX} total max)\n\n"
                f"Current extras: {current}\n\n"
                f"Remove one first: `/removepriority TOKEN`"
            )
        self._markets.append(base)
        remaining = _MAX_EXTRA - len(self._markets)
        return True, (
            f"✅ `{base}USDT` added to scan list\n"
            f"Extra slots used: {len(self._markets)}/{_MAX_EXTRA} "
            f"({remaining} remaining | {_AUTO_SCAN + len(self._markets)}/{_TOTAL_MAX} total)"
        )

    def remove(self, symbol: str) -> tuple[bool, str]:
        """Remove a symbol. Returns (success, message)."""
        base = _normalise(symbol)
        if base is None or base not in self._markets:
            return False, f"❌ `{symbol}` is not in your extra scan list"
        self._markets.remove(base)
        return True, (
            f"✅ `{base}` removed\n"
            f"Extra slots: {len(self._markets)}/{_MAX_EXTRA} used"
        )

    def get(self) -> list[str]:
        return list(self._markets)

    def count(self) -> int:
        return len(self._markets)

    def is_full(self) -> bool:
        return len(self._markets) >= _MAX_EXTRA

    def format_telegram(self) -> str:
        used = len(self._markets)
        remaining = _MAX_EXTRA - used
        total_scanning = _AUTO_SCAN + used

        if not self._markets:
            return (
                f"📋 *Extra scan list* — 0/{_MAX_EXTRA} slots used\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Currently scanning: top-{_AUTO_SCAN} by volume (auto)\n\n"
                f"Add coins: `/check PEPE` or `/check WIF`\n"
                f"You have {_MAX_EXTRA} extra slots available "
                f"(max {_TOTAL_MAX} total scans per cycle)"
            )

        lines = [
            f"📋 *Extra scan list* — {used}/{_MAX_EXTRA} slots used",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"Total scanning this cycle: {total_scanning}/{_TOTAL_MAX} markets",
            "",
        ]
        for i, m in enumerate(self._markets, 1):
            lines.append(f"  {i}. `{m}USDT`")

        lines.append("")
        if remaining > 0:
            lines.append(f"_{remaining} slot(s) remaining — add with `/check TOKEN`_")
        else:
            lines.append(f"_List full — remove one with `/removepriority TOKEN` to add another_")

        lines.append(f"_Auto-scan: top-{_AUTO_SCAN} USDT pairs by volume (always on)_")
        return "\n".join(lines)

"""
bot/learning/signal_weights.py — Loads and saves signal weight config from DB.
Provides a cached view so the brain doesn't hit DB every cycle.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger("nanorca.learning.weights")

DEFAULT_WEIGHTS = {
    "price_gap_polymarket":    0.35,
    "funding_rate_hyperliquid":0.25,
    "binance_momentum":        0.20,
    "sentiment_news":          0.15,
    "volume_spike":            0.05,
}

CACHE_TTL_SECONDS = 300  # Re-read from DB every 5 minutes


class SignalWeights:
    """Cached signal weight loader with auto-refresh."""

    def __init__(self, db) -> None:
        self._db = db
        self._cache: dict[str, float] = dict(DEFAULT_WEIGHTS)
        self._last_loaded: datetime | None = None

    async def get(self) -> dict[str, float]:
        """Return current weights. Refreshes from DB every CACHE_TTL_SECONDS."""
        now = datetime.now(timezone.utc)
        if (
            self._last_loaded is None
            or (now - self._last_loaded).total_seconds() > CACHE_TTL_SECONDS
        ):
            try:
                fresh = await self._db.get_signal_weights()
                if fresh:
                    self._cache = fresh
                    self._last_loaded = now
                    log.debug(f"Signal weights refreshed from DB: {self._cache}")
            except Exception as e:
                log.error(f"Failed to load signal weights from DB — using cached: {e}")

        return dict(self._cache)

    async def update(self, new_weights: dict[str, float]) -> None:
        """Persist new weights to DB and update cache."""
        await self._db.update_signal_weights(new_weights)
        self._cache = new_weights
        self._last_loaded = datetime.now(timezone.utc)
        log.info(f"Signal weights updated: {new_weights}")

"""
bot/brain/signal_builder.py — Aggregates raw market data into a structured signal dict.

Receives market snapshots from the Go executor and transforms them into
the normalized signal format that Claude's decision prompt expects.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nanorca.brain.signals")


class SignalBuilder:
    """Transforms raw market snapshots into Claude-ready signal dicts."""

    def __init__(self, config) -> None:
        self._config = config

    async def build(
        self,
        market_snapshots: list[dict[str, Any]],
        signal_weights: dict[str, float],
    ) -> dict[str, Any]:
        """
        Build a structured signal dictionary from market snapshots.

        Returns a dict with one entry per signal type, each containing:
          - raw_value: the actual data point
          - normalized: 0.0–1.0 normalized value
          - weight: current weight from signal_weights table
          - fired: bool — did this signal exceed its threshold?
        """
        signals: dict[str, Any] = {}

        # Group snapshots by exchange
        by_exchange: dict[str, list[dict]] = {}
        for snap in market_snapshots:
            ex = snap.get("exchange", "unknown")
            by_exchange.setdefault(ex, []).append(snap)

        # ── Polymarket: price gap signal ──────────────────────────────────
        poly_snaps = by_exchange.get("polymarket", [])
        if poly_snaps:
            signals["price_gap_polymarket"] = self._build_price_gap_signal(
                poly_snaps, signal_weights.get("price_gap_polymarket", 0.35)
            )

        # ── Hyperliquid: funding rate signal ──────────────────────────────
        hl_snaps = by_exchange.get("hyperliquid", [])
        if hl_snaps:
            signals["funding_rate_hyperliquid"] = self._build_funding_rate_signal(
                hl_snaps, signal_weights.get("funding_rate_hyperliquid", 0.25)
            )

        # ── Binance: momentum signal ──────────────────────────────────────
        bn_snaps = by_exchange.get("binance", [])
        if bn_snaps:
            signals["binance_momentum"] = self._build_momentum_signal(
                bn_snaps, signal_weights.get("binance_momentum", 0.20)
            )

        # ── Volume spike detection (cross-exchange) ───────────────────────
        signals["volume_spike"] = self._build_volume_spike_signal(
            market_snapshots, signal_weights.get("volume_spike", 0.05)
        )

        # ── Raw snapshots (for Claude context) ────────────────────────────
        signals["_raw_snapshots"] = market_snapshots[:10]  # top 10 to keep prompt size sane
        signals["_snapshot_count"] = len(market_snapshots)

        log.debug(f"Built {len(signals)} signal types from {len(market_snapshots)} snapshots")
        return signals

    def _build_price_gap_signal(self, poly_snaps: list[dict], weight: float) -> dict:
        """
        Detect price gaps on Polymarket: markets where YES+NO prices don't sum to 1.0.
        A gap > 0.02 (2%) may indicate an arbitrage opportunity.

        TODO Phase 3: calculate actual gap from real bid/ask data.
        """
        # Find the market with the largest price gap
        best_gap = 0.0
        best_market = None
        for snap in poly_snaps:
            yes = snap.get("price", 0.5)
            no = snap.get("ask", 0.5)
            gap = abs(1.0 - yes - no)
            if gap > best_gap:
                best_gap = gap
                best_market = snap.get("market")

        normalized = min(best_gap / 0.05, 1.0)  # 5% gap = max signal
        return {
            "raw_value": best_gap,
            "normalized": round(normalized, 4),
            "weight": weight,
            "fired": best_gap > 0.02,
            "best_market": best_market,
            "description": "Polymarket YES+NO price deviation from 1.0",
        }

    def _build_funding_rate_signal(self, hl_snaps: list[dict], weight: float) -> dict:
        """
        Detect extreme funding rates on Hyperliquid.
        Very negative rates → longs are being paid → bias long.
        Very positive rates → shorts are being paid → bias short.

        TODO Phase 3: use real funding rate data from executor.
        """
        rates = [s.get("funding_rate", 0.0) for s in hl_snaps if s.get("funding_rate")]
        if not rates:
            return {"raw_value": 0, "normalized": 0.5, "weight": weight, "fired": False}

        avg_rate = sum(rates) / len(rates)
        normalized = (avg_rate + 0.002) / 0.004  # -0.2%→0, 0%→0.5, +0.2%→1.0
        normalized = max(0.0, min(1.0, normalized))

        return {
            "raw_value": avg_rate,
            "normalized": round(normalized, 4),
            "weight": weight,
            "fired": abs(avg_rate) > 0.0005,
            "direction_bias": "long" if avg_rate < -0.0005 else ("short" if avg_rate > 0.0005 else "neutral"),
            "description": "Hyperliquid average funding rate across priority markets",
        }

    def _build_momentum_signal(self, bn_snaps: list[dict], weight: float) -> dict:
        """
        Simple momentum signal from Binance price data.
        TODO Phase 3: use 5-minute price change from real WS feed.
        """
        # Stub: returns neutral until real data is flowing
        return {
            "raw_value": 0.0,
            "normalized": 0.5,
            "weight": weight,
            "fired": False,
            "description": "Binance 5-minute price momentum (stub — Phase 3)",
        }

    def _build_volume_spike_signal(self, all_snaps: list[dict], weight: float) -> dict:
        """
        Detect unusual volume spikes across any exchange.
        TODO Phase 3: compare against 24h rolling average.
        """
        return {
            "raw_value": 0.0,
            "normalized": 0.0,
            "weight": weight,
            "fired": False,
            "description": "Cross-exchange volume spike detector (stub — Phase 3)",
        }

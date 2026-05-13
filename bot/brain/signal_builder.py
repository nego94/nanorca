"""
bot/brain/signal_builder.py — Aggregates raw market data into a structured signal dict.

Receives market snapshots from the Go executor and transforms them into
the normalized signal format that Claude's decision prompt expects.

Phase 2A: momentum uses a rolling price window accumulated across scan cycles.
          volume spike uses real volume_24h data from Binance.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("nanorca.brain.signals")

# Momentum fires when price moves more than this % over the rolling window.
_MOMENTUM_FIRE_PCT = 1.5
# Momentum signal saturates (normalized=1.0) at this % move.
_MOMENTUM_SATURATE_PCT = 3.0
# Minimum seconds of price history before we trust the momentum value.
_MOMENTUM_MIN_AGE_S = 90
# Volume spike fires when current volume exceeds EMA baseline by this ratio.
_VOLUME_SPIKE_RATIO = 1.30   # 30% above baseline
# EMA decay for volume baseline update (slow: ~20 cycles to converge).
_VOLUME_EMA_ALPHA = 0.05


class SignalBuilder:
    """Transforms raw market snapshots into Claude-ready signal dicts."""

    def __init__(self, config) -> None:
        self._config = config
        # Rolling price history: market → [(price, timestamp), ...]
        # Accumulated across scan cycles; pruned to last 10 minutes.
        self._price_history: dict[str, list[tuple[float, float]]] = {}
        # EMA volume baseline per market symbol.
        self._volume_baseline: dict[str, float] = {}

    def _disabled_signal(self, weight: float, name: str) -> dict:
        """Return a neutral zeroed signal for a disabled exchange."""
        return {
            "raw_value": 0.0, "normalized": 0.5, "weight": 0.0,
            "fired": False, "description": f"{name} disabled (not in ENABLED_EXCHANGES)",
        }

    async def build(
        self,
        market_snapshots: list[dict[str, Any]],
        signal_weights: dict[str, float],
    ) -> dict[str, Any]:
        """
        Build a structured signal dictionary from market snapshots.

        Only exchanges listed in config.enabled_exchanges contribute active signals.
        Disabled exchanges return neutral (weight=0, normalized=0.5, fired=False)
        so Claude ignores them without crashing.
        """
        signals: dict[str, Any] = {}
        enabled = self._config.enabled_exchanges  # frozenset of lowercase exchange names

        # Group snapshots by exchange
        by_exchange: dict[str, list[dict]] = {}
        for snap in market_snapshots:
            ex = snap.get("exchange", "unknown")
            by_exchange.setdefault(ex, []).append(snap)

        # ── Polymarket: price gap signal ──────────────────────────────────
        if "polymarket" not in enabled:
            signals["price_gap_polymarket"] = self._disabled_signal(
                signal_weights.get("price_gap_polymarket", 0.35), "Polymarket"
            )
        else:
            poly_snaps = by_exchange.get("polymarket", [])
            signals["price_gap_polymarket"] = self._build_price_gap_signal(
                poly_snaps, signal_weights.get("price_gap_polymarket", 0.35)
            )

        # ── Hyperliquid: funding rate signal ──────────────────────────────
        if "hyperliquid" not in enabled:
            signals["funding_rate_hyperliquid"] = self._disabled_signal(
                signal_weights.get("funding_rate_hyperliquid", 0.25), "Hyperliquid"
            )
        else:
            hl_snaps = by_exchange.get("hyperliquid", [])
            signals["funding_rate_hyperliquid"] = self._build_funding_rate_signal(
                hl_snaps, signal_weights.get("funding_rate_hyperliquid", 0.25)
            )

        # ── Binance: momentum signal ──────────────────────────────────────
        if "binance" not in enabled:
            signals["binance_momentum"] = self._disabled_signal(
                signal_weights.get("binance_momentum", 0.20), "Binance"
            )
        else:
            bn_snaps = by_exchange.get("binance", [])
            signals["binance_momentum"] = self._build_momentum_signal(
                bn_snaps, signal_weights.get("binance_momentum", 0.20)
            )

        # ── Volume spike (Binance only when enabled, else disabled) ───────
        if "binance" not in enabled:
            signals["volume_spike"] = self._disabled_signal(
                signal_weights.get("volume_spike", 0.05), "Volume (Binance)"
            )
        else:
            signals["volume_spike"] = self._build_volume_spike_signal(
                market_snapshots, signal_weights.get("volume_spike", 0.05)
            )

        # ── Raw snapshots for Claude context (only enabled exchanges) ──────
        enabled_snaps = [s for s in market_snapshots if s.get("exchange") in enabled]
        signals["_raw_snapshots"] = enabled_snaps[:15]
        signals["_snapshot_count"] = len(market_snapshots)
        signals["_enabled_exchanges"] = sorted(enabled)

        log.debug("Built %d signal types from %d snapshots (enabled: %s)",
                  len(signals), len(market_snapshots), ", ".join(sorted(enabled)))
        return signals

    def _build_price_gap_signal(self, poly_snaps: list[dict], weight: float) -> dict:
        """
        Detect price gaps on Polymarket: markets where YES+NO prices don't sum to 1.0.
        A gap > 2% may indicate an arbitrage opportunity.
        Real data: YesPrice and NoPrice come from the Polymarket CLOB via Go scanner.
        """
        best_gap = 0.0
        best_market = None
        for snap in poly_snaps:
            yes = snap.get("price", 0.0)   # YesPrice
            no  = snap.get("ask", 0.0)    # NoPrice (mapped to ask in scanner)
            if yes <= 0 or no <= 0:
                continue
            gap = abs(1.0 - yes - no)
            if gap > best_gap:
                best_gap = gap
                best_market = snap.get("market")

        normalized = min(best_gap / 0.05, 1.0)  # 5% gap = max signal
        return {
            "raw_value": round(best_gap, 4),
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
        Real data: funding_rate from GetFundingRates() via Go scanner.
        """
        rates = [s.get("funding_rate", 0.0) for s in hl_snaps if s.get("funding_rate") != 0]
        if not rates:
            return {
                "raw_value": 0,
                "normalized": 0.5,
                "weight": weight,
                "fired": False,
                "description": "Hyperliquid funding rates (no data yet)",
            }

        avg_rate = sum(rates) / len(rates)
        # Normalize: -0.2% → 0.0, 0% → 0.5, +0.2% → 1.0
        normalized = (avg_rate + 0.002) / 0.004
        normalized = max(0.0, min(1.0, normalized))

        extremes = [r for r in rates if abs(r) > 0.001]  # >0.1% is extreme
        return {
            "raw_value": round(avg_rate, 6),
            "normalized": round(normalized, 4),
            "weight": weight,
            "fired": abs(avg_rate) > 0.0005,
            "extreme_count": len(extremes),
            "direction_bias": (
                "long" if avg_rate < -0.0005
                else ("short" if avg_rate > 0.0005 else "neutral")
            ),
            "description": f"Hyperliquid avg funding rate across {len(rates)} perps",
        }

    def _build_momentum_signal(self, bn_snaps: list[dict], weight: float) -> dict:
        """
        Momentum signal from Binance price data accumulated over scan cycles.

        Records each snapshot price into a rolling 10-minute window per symbol.
        Computes % price change (newest vs oldest point in window).
        Returns neutral (0.5) until at least _MOMENTUM_MIN_AGE_S of history exists.
        """
        now = time.monotonic()
        cutoff_prune = now - 600  # prune entries older than 10 minutes

        # Record current prices and prune old ones
        for snap in bn_snaps:
            market = snap.get("market")
            price = snap.get("price", 0.0)
            if not market or price <= 0:
                continue
            history = self._price_history.setdefault(market, [])
            history.append((price, now))
            # Prune stale entries
            self._price_history[market] = [
                (p, t) for p, t in history if t >= cutoff_prune
            ]

        # Find the market with the strongest momentum
        best_momentum_pct = 0.0
        best_market = None
        markets_with_signal = 0

        for snap in bn_snaps:
            market = snap.get("market")
            history = self._price_history.get(market, [])
            if len(history) < 2:
                continue
            oldest_price, oldest_ts = history[0]
            newest_price, _ = history[-1]
            # Require minimum age so we're not just comparing two back-to-back cycles
            if now - oldest_ts < _MOMENTUM_MIN_AGE_S:
                continue
            if oldest_price == 0:
                continue
            momentum_pct = (newest_price - oldest_price) / oldest_price * 100
            markets_with_signal += 1
            if abs(momentum_pct) > abs(best_momentum_pct):
                best_momentum_pct = momentum_pct
                best_market = market

        if markets_with_signal == 0:
            warmup_remaining = max(
                0,
                int(_MOMENTUM_MIN_AGE_S - (now - min(
                    (t for pts in self._price_history.values() for _, t in pts),
                    default=now
                )))
            )
            return {
                "raw_value": 0.0,
                "normalized": 0.5,
                "weight": weight,
                "fired": False,
                "description": (
                    f"Binance momentum warming up ({warmup_remaining}s remaining)"
                    if warmup_remaining > 0
                    else "Binance momentum (no Binance snapshots received)"
                ),
            }

        # Normalize: -3% → 0.0, 0% → 0.5, +3% → 1.0
        normalized = (best_momentum_pct / _MOMENTUM_SATURATE_PCT + 1.0) / 2.0
        normalized = max(0.0, min(1.0, normalized))
        fired = abs(best_momentum_pct) > _MOMENTUM_FIRE_PCT

        return {
            "raw_value": round(best_momentum_pct, 4),
            "normalized": round(normalized, 4),
            "weight": weight,
            "fired": fired,
            "direction_bias": (
                "long" if best_momentum_pct > _MOMENTUM_FIRE_PCT
                else ("short" if best_momentum_pct < -_MOMENTUM_FIRE_PCT else "neutral")
            ),
            "best_market": best_market,
            "markets_tracked": markets_with_signal,
            "description": (
                f"Binance momentum: {best_momentum_pct:+.2f}% on {best_market}"
            ),
        }

    def _build_volume_spike_signal(self, all_snaps: list[dict], weight: float) -> dict:
        """
        Detect unusual volume spikes across Binance markets.

        Uses real volume_24h from each snapshot. Maintains a per-symbol EMA baseline
        updated each cycle. Fires when current volume exceeds baseline by _VOLUME_SPIKE_RATIO.
        Baseline warms up over ~20 cycles (~20 minutes at 60s interval).
        """
        bn_snaps = [s for s in all_snaps if s.get("exchange") == "binance"]
        if not bn_snaps:
            return {
                "raw_value": 0.0,
                "normalized": 0.0,
                "weight": weight,
                "fired": False,
                "description": "Volume spike (no Binance snapshots)",
            }

        best_ratio = 0.0
        best_market = None
        markets_with_volume = 0

        for snap in bn_snaps:
            market = snap.get("market")
            vol = snap.get("volume_24h", 0.0)
            if not market or vol <= 0:
                continue
            markets_with_volume += 1

            if market not in self._volume_baseline:
                # Seed baseline with first observation; no spike on first cycle
                self._volume_baseline[market] = vol
                continue

            baseline = self._volume_baseline[market]
            # Slow EMA update so baseline isn't skewed by a single spike
            self._volume_baseline[market] = baseline * (1 - _VOLUME_EMA_ALPHA) + vol * _VOLUME_EMA_ALPHA

            if baseline > 0:
                ratio = vol / baseline
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_market = market

        if markets_with_volume == 0:
            return {
                "raw_value": 0.0,
                "normalized": 0.0,
                "weight": weight,
                "fired": False,
                "description": "Volume spike (volume_24h not yet populated — scanner warming up)",
            }

        # Normalize: 1.0x = 0.0, 2.0x = 1.0 (linear above baseline)
        normalized = max(0.0, min(1.0, (best_ratio - 1.0)))
        fired = best_ratio >= _VOLUME_SPIKE_RATIO

        return {
            "raw_value": round(best_ratio, 4),
            "normalized": round(normalized, 4),
            "weight": weight,
            "fired": fired,
            "best_market": best_market,
            "spike_ratio": round(best_ratio, 4),
            "description": (
                f"Volume spike: {best_market} at {best_ratio:.2f}x baseline"
                if best_market
                else f"Volume spike: no spike detected across {markets_with_volume} markets"
            ),
        }

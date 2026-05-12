"""
bot/brain/confidence_scorer.py — Weights signals and computes a 0–100 score.

The confidence score is a weighted sum of normalized signal values.
Claude also provides its own confidence — we take the lower of the two
to be conservative (don't override Claude's caution with math).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nanorca.brain.confidence")


class ConfidenceScorer:
    """Computes confidence score from signal dict."""

    def __init__(self, config) -> None:
        self._config = config

    def score(self, signals: dict[str, Any]) -> int:
        """
        Compute weighted confidence score from signal dict.

        Returns an integer 0–100.
        Does not consider Claude's own confidence — that's done in main_loop.
        """
        score = 0.0
        total_weight = 0.0

        signal_types = [
            "price_gap_polymarket",
            "funding_rate_hyperliquid",
            "binance_momentum",
            "sentiment_news",
            "volume_spike",
        ]

        for sig_type in signal_types:
            sig = signals.get(sig_type)
            if not sig or not isinstance(sig, dict):
                continue

            normalized = sig.get("normalized", 0.5)
            weight = sig.get("weight", 0.0)
            fired = sig.get("fired", False)

            # Bonus for fired signals
            signal_score = normalized * (1.2 if fired else 1.0)
            score += signal_score * weight
            total_weight += weight

        if total_weight == 0:
            return 0

        raw_score = score / total_weight
        final = int(raw_score * 100)
        final = max(0, min(100, final))

        log.debug(f"Confidence score: {final} (raw={raw_score:.4f}, total_weight={total_weight:.4f})")
        return final

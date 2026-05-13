"""
bot/risk/risk_manager.py — Position sizing and trade approval.

Hard-coded guards that CANNOT be overridden by Claude.
Uses the selected TradingPlan for dynamic sizing when mode != NANORCA_DECIDE.

Exchange leverage hard caps (never exceed regardless of plan):
  Polymarket  → 1x  (prediction market, no margin)
  Binance     → 3x  (per master plan security rules)
  Hyperliquid → 10x (agreed aggressive ceiling)
"""
from __future__ import annotations

import logging
from typing import Any

from risk.trading_plan import TradingMode, get_plan_params, DrawdownRule

log = logging.getLogger("nanorca.risk.manager")

# Absolute hard limits — never overrideable by any plan or Claude
MAX_OPEN_POSITIONS               = 5
MAX_SINGLE_EXCHANGE_EXPOSURE_PCT = 40
BINANCE_MAX_LEVERAGE             = 3
HYPERLIQUID_MAX_LEVERAGE         = 10
POLYMARKET_LEVERAGE              = 1.0
MAX_HOLD_HOURS                   = 24

NO_LEVERAGE_EXCHANGES = {"polymarket"}


def effective_leverage(exchange: str, requested: float) -> float:
    """
    Apply per-exchange leverage ceiling.
    Polymarket = always 1x (no margin product).
    Binance    = max 3x.
    Hyperliquid= max 10x.
    """
    ex = (exchange or "").lower()
    if ex in NO_LEVERAGE_EXCHANGES:
        return 1.0
    if ex == "binance":
        return min(requested, BINANCE_MAX_LEVERAGE)
    if ex == "hyperliquid":
        return min(requested, HYPERLIQUID_MAX_LEVERAGE)
    return 1.0


class RiskManager:
    """Approves or rejects trades. Calculates position size based on trading plan."""

    def __init__(self, config, circuit_breaker, capital_tracker) -> None:
        self._config = config
        self._cb = circuit_breaker
        self._cap = capital_tracker
        self._trading_mode = TradingMode(getattr(config, "trading_mode", "nanorca_decide"))

    @staticmethod
    def _graduated_size_pct(confidence: int) -> float:
        """
        Maps confidence tier to max position size % (strategy doc Part 6).
        55-64 → 1% (tiny: real learning data at low confidence)
        65-79 → 3% (normal)
        80-89 → 5% (full)
        90+   → 5% (max; high_conviction flag set separately)
        Below 55 is blocked upstream in main.py before reaching here.
        """
        if confidence >= 80:
            return 5.0
        if confidence >= 65:
            return 3.0
        return 1.0  # 55-64 band: tiny position for calibration data

    async def approve(
        self,
        decision: dict[str, Any],
        capital_tracker,
    ) -> tuple[bool, str]:
        """
        Validate and size the trade.
        Injects size_usd, leverage, plan_mode, and high_conviction back into decision dict.
        Returns (approved: bool, reason: str).
        """
        confidence = decision.get("confidence", 0)

        # Safety net — main.py should already block below 55
        if confidence < 55:
            return False, f"Confidence {confidence} < 55 (hard skip)"

        action = decision.get("action")
        if action not in ("buy", "sell"):
            return False, f"Invalid action: {action}"

        exchange = (decision.get("exchange") or "").lower()
        if not exchange:
            return False, "No exchange specified"

        capital = capital_tracker.current_capital
        plan = get_plan_params(self._trading_mode, capital)

        # ── High conviction flag (90+ confidence) ────────────────────────
        if confidence >= 90:
            decision["high_conviction"] = True

        # ── Size calculation ──────────────────────────────────────────────
        if self._trading_mode == TradingMode.NANORCA_DECIDE:
            # Graduated sizing by confidence tier — overrides Claude's suggestion
            tier_max_pct = self._graduated_size_pct(confidence)
            size_pct = min(
                float(decision.get("size_pct", tier_max_pct)),
                tier_max_pct,                          # graduated tier cap
                self._config.max_position_pct,         # config hard cap
            )
            lev = effective_leverage(exchange, 1.0)
            size_usd = capital * (size_pct / 100)
        else:
            # Plan formula: PositionSize = C × R × L (exchange-capped)
            # If today's drawdown triggered leverage_reduced, cut leverage 50%
            requested_lev = plan["leverage"]
            if getattr(capital_tracker, "leverage_reduced", False):
                requested_lev = requested_lev * 0.5
                log.info(f"Leverage halved due to drawdown rule: {requested_lev:.1f}x")
            lev = effective_leverage(exchange, requested_lev)
            size_usd = capital * (plan["risk_pct"] / 100) * lev
            size_pct = size_usd / capital * 100

        # ── Guards ────────────────────────────────────────────────────────
        if size_usd < 0.50:
            return False, f"Trade too small: ${size_usd:.2f} (min $0.50)"

        # Cap at max exposure limit
        max_exposure_usd = capital * (plan["max_exposure_pct"] / 100)
        if size_usd > max_exposure_usd:
            size_usd = max_exposure_usd
            log.warning(f"Position capped by max exposure: ${size_usd:.2f}")

        # Inject calculated values back into decision
        decision["size_usd"]  = round(size_usd, 2)
        decision["leverage"]  = lev
        decision["size_pct"]  = round(size_pct, 2)
        decision["plan_mode"] = plan["mode_name"]

        log.info(
            f"Trade approved: {exchange} {decision.get('market')} {action} "
            f"${size_usd:.2f} lev={lev:.1f}x mode={plan['mode_name']}"
        )
        return True, "approved"

    def get_drawdown_action(self, drawdown_pct: float) -> DrawdownRule | None:
        """
        Return the most severe applicable DrawdownRule for today's drawdown.
        Called by capital_tracker each cycle.
        """
        plan = get_plan_params(self._trading_mode, self._cap.current_capital)
        rules: list[DrawdownRule] = plan["drawdown_rules"]
        triggered = [r for r in rules if drawdown_pct <= r.threshold_pct]
        return max(triggered, key=lambda r: abs(r.threshold_pct)) if triggered else None

    def calculate_size_usd(self, size_pct: float, capital: float) -> float:
        return round(capital * size_pct / 100, 2)

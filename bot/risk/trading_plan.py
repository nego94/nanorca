"""
bot/risk/trading_plan.py — 4 selectable trading plans.

Plans:
  nanorca_decide  — Claude picks size based on confidence (default)
  conservative    — Survive long-term, minimize blowups
  aggressive      — Fast growth, higher drawdowns tolerated (leverage capped 10x)
  hybrid          — Aggressive at small capital, conservative as it grows (RECOMMENDED)

Set via TRADING_MODE in .env
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TradingMode(Enum):
    NANORCA_DECIDE = "nanorca_decide"
    CONSERVATIVE   = "conservative"
    AGGRESSIVE     = "aggressive"
    HYBRID         = "hybrid"


@dataclass(frozen=True)
class DrawdownRule:
    threshold_pct: float   # e.g. -1.0 means -1% drawdown today
    action: str
    description: str


@dataclass(frozen=True)
class CapitalTier:
    min_cap: float
    max_cap: float
    risk_pct: float    # % of capital risked per trade
    leverage: float    # target leverage (1.0 = no leverage)
    daily_goal: float  # target daily % gain


# ── Conservative tiers ────────────────────────────────────────────────────────
CONSERVATIVE_TIERS = [
    CapitalTier(10,       100,     7.5,  5.0,  1.5),
    CapitalTier(100,      1_000,   4.0,  3.0,  1.0),
    CapitalTier(1_000,    10_000,  2.0,  2.0,  0.5),
    CapitalTier(10_000,   100_000, 0.75, 1.5,  0.3),
    CapitalTier(100_000,  1e12,    0.35, 1.0,  0.1),
]

# ── Aggressive tiers (leverage hard-capped at 10x) ────────────────────────────
AGGRESSIVE_TIERS = [
    CapitalTier(10,       100,     17.5, 10.0, 3.5),
    CapitalTier(100,      1_000,   11.0, 7.0,  2.0),
    CapitalTier(1_000,    10_000,  5.0,  5.0,  1.5),
    CapitalTier(10_000,   100_000, 2.0,  3.0,  0.75),
    CapitalTier(100_000,  1e12,    0.75, 1.5,  0.35),
]

# ── Drawdown rules ─────────────────────────────────────────────────────────────
CONSERVATIVE_DRAWDOWN = [
    DrawdownRule(-1.0,  "reduce_target",   "Reduce daily target to 0.3–0.5%"),
    DrawdownRule(-3.0,  "reduce_leverage", "Cut leverage 50%"),
    DrawdownRule(-5.0,  "stop_day",        "Stop trading for today"),
    DrawdownRule(-10.0, "strategy_review", "Reset — full strategy review needed"),
]

AGGRESSIVE_DRAWDOWN = [
    DrawdownRule(-1.0,  "allow_recovery",  "Allow +1% recovery attempt"),
    DrawdownRule(-3.0,  "reduce_leverage", "Reduce leverage slightly"),
    DrawdownRule(-5.0,  "pause_high_risk", "Pause only high-risk setups"),
    DrawdownRule(-10.0, "hard_stop",       "Hard stop for the day"),
    DrawdownRule(-20.0, "emergency",       "Emergency protection mode"),
]


def _get_tier(capital: float, tiers: list[CapitalTier]) -> CapitalTier:
    for tier in tiers:
        if tier.min_cap <= capital < tier.max_cap:
            return tier
    return tiers[-1]


def get_plan_params(mode: TradingMode, capital: float) -> dict:
    """
    Return risk parameters for the given mode and capital level.

    Keys: risk_pct, leverage, position_usd, max_exposure_pct,
          daily_goal_pct, drawdown_rules, mode_name
    """
    if mode == TradingMode.NANORCA_DECIDE:
        return {
            "risk_pct":          None,
            "leverage":          1.0,
            "position_usd":      None,
            "max_exposure_pct":  50.0,
            "daily_goal_pct":    None,
            "drawdown_rules":    CONSERVATIVE_DRAWDOWN,
            "mode_name":         "NANORCA Decide",
        }

    if mode == TradingMode.CONSERVATIVE:
        tier = _get_tier(capital, CONSERVATIVE_TIERS)
        return {
            "risk_pct":         tier.risk_pct,
            "leverage":         tier.leverage,
            "position_usd":     capital * (tier.risk_pct / 100) * tier.leverage,
            "max_exposure_pct": 30.0,
            "daily_goal_pct":   tier.daily_goal,
            "drawdown_rules":   CONSERVATIVE_DRAWDOWN,
            "mode_name":        "Conservative",
        }

    if mode == TradingMode.AGGRESSIVE:
        tier = _get_tier(capital, AGGRESSIVE_TIERS)
        return {
            "risk_pct":         tier.risk_pct,
            "leverage":         tier.leverage,
            "position_usd":     capital * (tier.risk_pct / 100) * tier.leverage,
            "max_exposure_pct": 60.0,
            "daily_goal_pct":   tier.daily_goal,
            "drawdown_rules":   AGGRESSIVE_DRAWDOWN,
            "mode_name":        "Aggressive",
        }

    if mode == TradingMode.HYBRID:
        if capital < 1_000:
            tier = _get_tier(capital, AGGRESSIVE_TIERS)
            rules = AGGRESSIVE_DRAWDOWN
            exposure = 60.0
            label = "Aggressive"
        elif capital >= 10_000:
            tier = _get_tier(capital, CONSERVATIVE_TIERS)
            rules = CONSERVATIVE_DRAWDOWN
            exposure = 30.0
            label = "Conservative"
        else:
            blend = (capital - 1_000) / 9_000
            c = _get_tier(capital, CONSERVATIVE_TIERS)
            a = _get_tier(capital, AGGRESSIVE_TIERS)
            tier = CapitalTier(
                min_cap=c.min_cap, max_cap=c.max_cap,
                risk_pct=a.risk_pct + (c.risk_pct - a.risk_pct) * blend,
                leverage=a.leverage + (c.leverage - a.leverage) * blend,
                daily_goal=a.daily_goal + (c.daily_goal - a.daily_goal) * blend,
            )
            rules = CONSERVATIVE_DRAWDOWN
            exposure = 60.0 - (30.0 * blend)
            label = "Blending"

        return {
            "risk_pct":         tier.risk_pct,
            "leverage":         tier.leverage,
            "position_usd":     capital * (tier.risk_pct / 100) * tier.leverage,
            "max_exposure_pct": exposure,
            "daily_goal_pct":   tier.daily_goal,
            "drawdown_rules":   rules,
            "mode_name":        f"Hybrid ({label})",
        }

    raise ValueError(f"Unknown trading mode: {mode}")


def format_plan_summary(mode: TradingMode, capital: float) -> str:
    """Human-readable plan summary for Telegram /status."""
    p = get_plan_params(mode, capital)
    if mode == TradingMode.NANORCA_DECIDE:
        return (
            f"🤖 Plan: {p['mode_name']}\n"
            f"   Claude sizes each trade from confidence score\n"
            f"   Max exposure: {p['max_exposure_pct']}% capital | No leverage"
        )
    margin = capital * (p['risk_pct'] / 100)   # actual capital at risk per trade
    notional = p['position_usd']               # leveraged position size
    lev = p['leverage']
    lev_str = f" (@{lev:.0f}x → ${notional:.2f} notional)" if lev > 1 else ""
    return (
        f"📐 Plan: {p['mode_name']}\n"
        f"   Risk/trade: {p['risk_pct']:.1f}% = ${margin:.2f} margin{lev_str}\n"
        f"   Leverage: {lev:.1f}x | Daily goal: {p['daily_goal_pct']:.1f}%\n"
        f"   Max exposure: {p['max_exposure_pct']:.0f}% capital"
    )

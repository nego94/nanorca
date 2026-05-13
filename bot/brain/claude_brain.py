"""
bot/brain/claude_brain.py — Claude AI decision engine.

Assembles the decision prompt from live market data, calls the Anthropic API,
parses the structured JSON response, and returns a typed decision dict.

Cost management:
  - Uses claude-haiku-4-5 for routine decisions (~$0.001 per call)
  - Falls back to claude-sonnet-4-6 for weekly learning analysis only
  - Tracks consecutive API failures and alerts via the circuit breaker
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger("nanorca.brain.claude")

DECISION_PROMPT_TEMPLATE = """You are the trading brain for NANORCA, an autonomous trading bot.

Your job: analyze the market data below and decide whether to trade.

## CURRENT MARKET DATA
{market_data_json}

## ACTIVE SIGNAL WEIGHTS (learned from past performance)
{signal_weights_json}

## RECENT PERFORMANCE CONTEXT
- Last 24h win rate: {win_rate_24h}%
- Last 7d win rate: {win_rate_7d}%
- Today's P&L so far: ${daily_pnl}
- Consecutive wins/losses: {streak}
- Capital vs floor: ${current_capital} / ${floor_capital} ({pct_from_floor}% above floor)

## CAPITAL CONTEXT — READ THIS BEFORE CHOOSING A MARKET
- Available USDT (futures margin): ~${current_capital:.2f}
- Max position per trade: {max_position_pct}% = ~${max_position_usd:.2f} USDT margin
- At 3x leverage: ~${max_notional_usd:.2f} notional position size
- THIS IS FUTURES TRADING: You NEVER convert USDT to coins. USDT stays as margin.
  Profit/loss settles back in USDT automatically when a position closes.
- AVOID BTCUSDT: minimum lot = 0.001 BTC ≈ $100–150. Your max notional (${max_notional_usd:.2f}) is too small.
- PREFER altcoins with small lots and high % volatility:
  SOL, BNB, INJ, DOGE, ADA, AVAX, MATIC, LINK, DOT, OP, ARB, SUI, APT
  These offer 1–3% moves on good signal days vs 0.3–0.5% for BTC.
- ETH is acceptable but prefer alts when signals are equal.

## ACTIVE EXCHANGES (only trade on these)
{enabled_exchanges}

## PRIORITY MARKETS TO WATCH
{priority_markets}

## YOUR DECISION RULES
- Only trade on ACTIVE EXCHANGES listed above — ignore signals from disabled ones (weight=0)
- Only recommend a trade if you have strong, specific reasoning from active signals
- Confidence must reflect genuine signal strength, not optimism
- If multiple signals conflict, lower confidence accordingly
- Never recommend a trade size exceeding {max_position_pct}% of capital
- For Binance Futures USDT-M: target 30-min to 4-hour hold, use momentum + volume signals
- Always pick a market from the snapshot data (_raw_snapshots) — the one with the strongest momentum signal
- Set stop_loss_pct to 1.5–2.5% to stay within the max-hold risk budget

## RESPOND WITH ONLY RAW JSON — no markdown, no code fences, no extra text:
{{
  "action": "buy" | "sell" | "skip",
  "exchange": "polymarket" | "hyperliquid" | "binance" | null,
  "market": "<SYMBOL from _raw_snapshots, e.g. SOLUSDT or BNBUSDT>",
  "direction": "long" | "short" | "yes" | "no" | null,
  "size_pct": <float 0.0–5.0, % of capital>,
  "confidence": <integer 0–100>,
  "signals_used": ["signal_name_1", "signal_name_2"],
  "reasoning": "<2–3 sentence explanation of why this trade makes sense>",
  "expected_hold_minutes": <integer>,
  "stop_loss_pct": <float, % below entry to auto-close>
}}"""


class ClaudeBrain:
    """Wraps the Anthropic API and assembles/parses trading decisions."""

    def __init__(self, config, confidence_scorer) -> None:
        self._config = config
        self._scorer = confidence_scorer
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        self._consecutive_failures = 0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def decide(
        self,
        signals: dict[str, Any],
        signal_weights: dict[str, float],
        performance_ctx: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Call Claude with the current market signals and get a trading decision.

        Returns a parsed decision dict, or None if the API call fails.
        Raises on JSON parse error (logs and skips — does not retry same cycle).
        """
        enabled = sorted(getattr(self._config, "enabled_exchanges", {"binance"}))
        current_capital = float(performance_ctx.get("current_capital", self._config.starting_capital_usd))
        max_position_pct = self._config.max_position_pct
        max_position_usd = current_capital * (max_position_pct / 100)
        max_notional_usd = max_position_usd * 3  # Binance hard cap is 3x

        prompt = DECISION_PROMPT_TEMPLATE.format(
            market_data_json=json.dumps(signals, indent=2),
            signal_weights_json=json.dumps(signal_weights, indent=2),
            win_rate_24h=performance_ctx.get("win_rate_24h", 0),
            win_rate_7d=performance_ctx.get("win_rate_7d", 0),
            daily_pnl=performance_ctx.get("daily_pnl", 0),
            streak=performance_ctx.get("streak", "0"),
            current_capital=current_capital,
            floor_capital=performance_ctx.get("floor_capital", 0),
            pct_from_floor=performance_ctx.get("pct_from_floor", 100),
            enabled_exchanges=", ".join(enabled),
            priority_markets=", ".join(self._config.priority_markets),
            max_position_pct=max_position_pct,
            max_position_usd=max_position_usd,
            max_notional_usd=max_notional_usd,
        )

        try:
            response = await self._client.messages.create(
                model=self._config.claude_model_fast,
                max_tokens=self._config.claude_max_tokens,
                temperature=self._config.claude_temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            self._consecutive_failures = 0
        except anthropic.APIError as e:
            self._consecutive_failures += 1
            log.error(f"Claude API error (failure #{self._consecutive_failures}): {e}")
            raise

        raw_text = response.content[0].text.strip()
        log.debug(f"Claude raw response: {raw_text[:200]}...")

        # Strip markdown code fences Claude sometimes adds despite instructions
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

        # Fallback: extract first JSON object if there's surrounding text
        if not raw_text.startswith("{"):
            start = raw_text.find("{")
            end = raw_text.rfind("}") + 1
            if start != -1 and end > start:
                raw_text = raw_text[start:end]

        try:
            decision = json.loads(raw_text)
        except json.JSONDecodeError as e:
            log.error(f"Claude returned malformed JSON: {e}\nRaw: {raw_text[:500]}")
            return None  # Skip this cycle — do not retry

        # Validate required fields
        if "action" not in decision or "confidence" not in decision:
            log.error(f"Claude response missing required fields: {decision}")
            return None

        log.info(
            f"Claude decision: action={decision['action']} "
            f"exchange={decision.get('exchange')} "
            f"confidence={decision.get('confidence')}"
        )
        return decision

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def analyze_weekly(self, trades_json: str, weights_json: str) -> dict[str, Any] | None:
        """
        Weekly learning analysis using the deeper (more expensive) model.
        Called by WeeklyLearner every Sunday at 00:00 UTC.
        """
        from learning.weekly_learner import WEEKLY_LEARNING_PROMPT
        prompt = WEEKLY_LEARNING_PROMPT.format(
            trades_json=trades_json,
            current_weights_json=weights_json,
        )
        try:
            response = await self._client.messages.create(
                model=self._config.claude_model_deep,
                max_tokens=2000,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as e:
            log.error(f"Claude weekly analysis API error: {e}")
            return None

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        if not raw.startswith("{"):
            s, e2 = raw.find("{"), raw.rfind("}") + 1
            if s != -1 and e2 > s:
                raw = raw[s:e2]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            log.error(f"Claude weekly analysis malformed JSON: {e}")
            return None

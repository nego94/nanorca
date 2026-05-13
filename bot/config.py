"""
bot/config.py — NANORCA configuration loader and validator.

Loads all environment variables from .env (via python-dotenv) and exposes
them as typed attributes. Raises clear errors on missing required values
so misconfigured deployments fail fast at startup, not mid-trade.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env file — does nothing if already loaded from environment
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)


def _require(key: str) -> str:
    """Return env var or raise ValueError with a clear message."""
    val = os.getenv(key)
    if not val:
        raise ValueError(
            f"❌ Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _get_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _get_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _get_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class Config:
    # ── Anthropic ──────────────────────────────────────────────────────────
    anthropic_api_key: str      = field(default_factory=lambda: _require("ANTHROPIC_API_KEY"))
    claude_model_fast: str      = field(default_factory=lambda: _get("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"))
    claude_model_deep: str      = field(default_factory=lambda: _get("CLAUDE_MODEL_DEEP", "claude-sonnet-4-6"))
    claude_max_tokens: int      = field(default_factory=lambda: _get_int("CLAUDE_MAX_TOKENS", 1000))
    claude_temperature: float   = field(default_factory=lambda: _get_float("CLAUDE_TEMPERATURE", 0.1))

    # ── Telegram ───────────────────────────────────────────────────────────
    telegram_bot_token: str     = field(default_factory=lambda: _require("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str       = field(default_factory=lambda: _require("TELEGRAM_CHAT_ID"))
    # Comma-separated Telegram user IDs allowed to use the bot.
    # The owner (TELEGRAM_CHAT_ID) is always included automatically.
    # Get any user's ID by having them message @userinfobot.
    # Example: "123456789,987654321"
    telegram_allowed_user_ids: frozenset = field(
        default_factory=lambda: frozenset(
            uid.strip()
            for uid in _get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
            if uid.strip()
        )
    )

    # ── CallMeBot ──────────────────────────────────────────────────────────
    callmebot_phone: str        = field(default_factory=lambda: _get("CALLMEBOT_PHONE", ""))
    callmebot_api_key: str      = field(default_factory=lambda: _get("CALLMEBOT_API_KEY", ""))

    # ── Database ───────────────────────────────────────────────────────────
    postgres_host: str          = field(default_factory=lambda: _get("POSTGRES_HOST", "postgres"))
    postgres_port: int          = field(default_factory=lambda: _get_int("POSTGRES_PORT", 5432))
    postgres_db: str            = field(default_factory=lambda: _get("POSTGRES_DB", "nanorca"))
    postgres_user: str          = field(default_factory=lambda: _get("POSTGRES_USER", "nanorca_user"))
    postgres_password: str      = field(default_factory=lambda: _require("POSTGRES_PASSWORD"))

    # ── Go Executor gRPC ───────────────────────────────────────────────────
    executor_grpc_host: str     = field(default_factory=lambda: _get("EXECUTOR_GRPC_HOST", "executor"))
    executor_grpc_port: int     = field(default_factory=lambda: _get_int("EXECUTOR_GRPC_PORT", 50051))

    # ── Capital & Risk ─────────────────────────────────────────────────────
    starting_capital_usd: float = field(default_factory=lambda: _get_float("STARTING_CAPITAL_USD", 100.0))
    capital_floor_pct: float    = field(default_factory=lambda: _get_float("CAPITAL_FLOOR_PCT", 25.0))
    max_position_pct: float     = field(default_factory=lambda: _get_float("MAX_POSITION_PCT", 5.0))
    max_daily_loss_pct: float   = field(default_factory=lambda: _get_float("MAX_DAILY_LOSS_PCT", 8.0))
    circuit_breaker_n: int      = field(default_factory=lambda: _get_int("CIRCUIT_BREAKER_CONSECUTIVE", 3))
    confidence_threshold: int   = field(default_factory=lambda: _get_int("CONFIDENCE_THRESHOLD", 65))

    # ── Operation ──────────────────────────────────────────────────────────
    paper_trading: bool         = field(default_factory=lambda: _get_bool("PAPER_TRADING", True))
    scan_interval_seconds: int  = field(default_factory=lambda: _get_int("SCAN_INTERVAL_SECONDS", 60))
    bot_timezone: str           = field(default_factory=lambda: _get("BOT_TIMEZONE", "Asia/Makassar"))
    priority_markets: list      = field(default_factory=lambda: _get("PRIORITY_MARKETS", "BTC,ETH,SOL").split(","))
    log_level: str              = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    # Trading plan: nanorca_decide | conservative | aggressive | hybrid
    trading_mode: str           = field(default_factory=lambda: _get("TRADING_MODE", "nanorca_decide"))

    # ── Strategy thresholds ────────────────────────────────────────────────
    # Minimum gross price move to cover futures maker fees + profit target.
    # Futures maker: 0.02%/side × 2 = 0.04% round-trip. Target: 0.05%. Total: 0.09%.
    min_gross_move_pct: float   = field(default_factory=lambda: _get_float("MIN_GROSS_MOVE_PCT", 0.09))

    # ── Exchange focus ─────────────────────────────────────────────────────
    # Comma-separated exchanges whose signals Claude acts on.
    # Unlisted exchanges are still scanned for data but signals are zeroed out.
    enabled_exchanges: frozenset = field(
        default_factory=lambda: frozenset(
            ex.strip().lower()
            for ex in _get("ENABLED_EXCHANGES", "binance").split(",")
            if ex.strip()
        )
    )

    # ── News APIs ──────────────────────────────────────────────────────────
    cmc_api_key: str            = field(default_factory=lambda: _get("CMC_API_KEY", ""))
    twitter_bearer_token: str   = field(default_factory=lambda: _get("TWITTER_BEARER_TOKEN", ""))

    @property
    def db_dsn(self) -> str:
        """asyncpg DSN string."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def executor_grpc_addr(self) -> str:
        return f"{self.executor_grpc_host}:{self.executor_grpc_port}"

    def validate(self) -> None:
        """Run all validations and raise ValueError on first failure."""
        # Risk sanity checks
        assert 0 < self.capital_floor_pct < 100, "CAPITAL_FLOOR_PCT must be 0–100"
        assert 0 < self.max_position_pct <= 50, "MAX_POSITION_PCT must be 0–50"
        valid_modes = ("nanorca_decide", "conservative", "aggressive", "hybrid")
        assert self.trading_mode in valid_modes, f"TRADING_MODE must be one of: {valid_modes}"
        assert 0 < self.max_daily_loss_pct <= 20, "MAX_DAILY_LOSS_PCT must be 0–20"
        assert 0 <= self.confidence_threshold <= 100, "CONFIDENCE_THRESHOLD must be 0–100"
        assert self.starting_capital_usd >= 10.0, "STARTING_CAPITAL_USD must be at least $10"

        if not self.paper_trading:
            # Extra validation for live mode
            if not self.callmebot_phone or not self.callmebot_api_key:
                raise ValueError("CallMeBot credentials required for live trading (critical alerts)")

        print(f"✅ Config validated — paper_mode={self.paper_trading}, "
              f"capital=${self.starting_capital_usd}, "
              f"markets={self.priority_markets}")


# Module-level singleton — import this everywhere
config = Config()

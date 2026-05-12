-- ══════════════════════════════════════════════════════════════════════════════
-- NANORCA — Initial Database Schema
-- PostgreSQL 15 + TimescaleDB extension
-- Run automatically by Docker on first boot via docker-entrypoint-initdb.d
-- ══════════════════════════════════════════════════════════════════════════════

-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ── TRADES ─────────────────────────────────────────────────────────────────
-- Core trade log. Hypertable partitioned by created_at for fast time queries.
CREATE TABLE trades (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exchange            VARCHAR(20)  NOT NULL,           -- 'binance' | 'polymarket' | 'hyperliquid'
    market              VARCHAR(100) NOT NULL,           -- e.g. 'BTC-YES', 'BTCUSDT'
    direction           VARCHAR(10)  NOT NULL,           -- 'buy' | 'sell' | 'long' | 'short'
    entry_price         NUMERIC(20,8),
    exit_price          NUMERIC(20,8),
    size_usd            NUMERIC(12,2),
    pnl_usd             NUMERIC(12,2),
    fees_usd            NUMERIC(10,4),
    status              VARCHAR(20)  DEFAULT 'open',     -- 'open' | 'closed' | 'cancelled'
    opened_at           TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    hold_minutes        NUMERIC(8,2),
    confidence_score    INTEGER,                         -- 0–100 at time of entry
    signal_mix          JSONB,                           -- which signals fired + weights
    claude_reasoning    TEXT,                            -- Claude's reasoning for the trade
    outcome             VARCHAR(10),                     -- 'win' | 'loss' | 'breakeven'
    win                 BOOLEAN,
    paper               BOOLEAN NOT NULL DEFAULT TRUE,   -- true = paper trade (no real money)
    exchange_order_id   VARCHAR(100)                     -- order ID from exchange
);

SELECT create_hypertable('trades', 'created_at');

CREATE INDEX idx_trades_exchange     ON trades (exchange, created_at DESC);
CREATE INDEX idx_trades_market       ON trades (market, created_at DESC);
CREATE INDEX idx_trades_status       ON trades (status) WHERE status = 'open';
CREATE INDEX idx_trades_paper        ON trades (paper, created_at DESC);

-- ── SIGNALS ─────────────────────────────────────────────────────────────────
-- Every signal evaluated each cycle. Hypertable for time-series queries.
CREATE TABLE signals (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market          VARCHAR(100) NOT NULL,
    signal_type     VARCHAR(50)  NOT NULL,               -- 'price_gap' | 'funding_rate' | etc.
    raw_value       NUMERIC(20,8),
    normalized      NUMERIC(5,4),                        -- 0.0–1.0 normalized
    weight          NUMERIC(5,4),                        -- current weight from signal_weights
    fired           BOOLEAN DEFAULT FALSE,               -- did this signal cross threshold?
    trade_id        BIGINT REFERENCES trades(id)
);

SELECT create_hypertable('signals', 'recorded_at');

CREATE INDEX idx_signals_type       ON signals (signal_type, recorded_at DESC);
CREATE INDEX idx_signals_trade      ON signals (trade_id);

-- ── CAPITAL SNAPSHOTS ───────────────────────────────────────────────────────
-- Snapshot taken every scan cycle. Powers the capital chart in Grafana.
CREATE TABLE capital_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_usd       NUMERIC(12,2) NOT NULL,
    starting_usd    NUMERIC(12,2) NOT NULL,
    pct_change      NUMERIC(8,4),
    open_positions  INTEGER DEFAULT 0,
    daily_pnl       NUMERIC(12,2),
    note            TEXT
);

SELECT create_hypertable('capital_snapshots', 'recorded_at');

-- ── SIGNAL WEIGHTS (SELF-LEARNING CONFIG) ───────────────────────────────────
-- Updated every Sunday by the weekly learning loop.
-- The bot reads from this table every cycle.
CREATE TABLE signal_weights (
    id              BIGSERIAL PRIMARY KEY,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_type     VARCHAR(50)  NOT NULL UNIQUE,
    weight          NUMERIC(5,4) NOT NULL DEFAULT 0.5,
    win_rate_7d     NUMERIC(5,4),
    sample_size     INTEGER,
    notes           TEXT
);

-- Seed default weights (must sum to 1.0)
INSERT INTO signal_weights (signal_type, weight) VALUES
    ('price_gap_polymarket',    0.35),
    ('funding_rate_hyperliquid',0.25),
    ('binance_momentum',        0.20),
    ('sentiment_news',          0.15),
    ('volume_spike',            0.05);

-- ── BOT EVENTS (AUDIT LOG) ──────────────────────────────────────────────────
-- Every notable system event: start, stop, circuit breaker, alerts, etc.
CREATE TABLE bot_events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(50)  NOT NULL,               -- 'started' | 'paused' | 'circuit_breaker' | etc.
    severity        VARCHAR(10)  DEFAULT 'info',          -- 'info' | 'warning' | 'critical'
    message         TEXT,
    payload         JSONB
);

CREATE INDEX idx_events_type        ON bot_events (event_type, occurred_at DESC);
CREATE INDEX idx_events_severity    ON bot_events (severity) WHERE severity IN ('warning', 'critical');

-- ── WEEKLY LEARNING REPORTS ─────────────────────────────────────────────────
-- Claude's weekly analysis + recommended weight changes.
CREATE TABLE learning_reports (
    id              BIGSERIAL PRIMARY KEY,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    total_trades    INTEGER,
    win_rate        NUMERIC(5,4),
    total_pnl       NUMERIC(12,2),
    claude_analysis TEXT,
    weight_changes  JSONB,                               -- before/after weight comparison
    applied         BOOLEAN DEFAULT FALSE,               -- did we auto-apply the new weights?
    confidence_in_analysis INTEGER                       -- Claude's self-reported confidence 0–100
);

-- ── NEWS EVENTS ─────────────────────────────────────────────────────────────
-- Critical news events fetched from CMC + Twitter/X that may impact trading.
CREATE TABLE news_events (
    id              BIGSERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          VARCHAR(50)  NOT NULL,               -- 'coinmarketcap' | 'twitter'
    headline        TEXT,
    url             TEXT,
    sentiment       VARCHAR(10),                         -- 'bullish' | 'bearish' | 'neutral'
    impact_score    INTEGER,                             -- 0–100, Claude-assigned
    markets_affected VARCHAR(200),                       -- comma-separated tickers
    alerted         BOOLEAN DEFAULT FALSE
);

SELECT create_hypertable('news_events', 'fetched_at');

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
    id                  BIGSERIAL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exchange            TEXT         NOT NULL,
    market              TEXT         NOT NULL,
    direction           TEXT         NOT NULL,
    entry_price         NUMERIC(20,8),
    exit_price          NUMERIC(20,8),
    size_usd            NUMERIC(12,2),
    pnl_usd             NUMERIC(12,2),
    fees_usd            NUMERIC(10,4),
    status              TEXT         DEFAULT 'open',
    opened_at           TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    hold_minutes        NUMERIC(8,2),
    confidence_score    INTEGER,
    signal_mix          JSONB,
    claude_reasoning    TEXT,
    outcome             TEXT,
    win                 BOOLEAN,
    paper               BOOLEAN NOT NULL DEFAULT TRUE,
    exchange_order_id   TEXT
);

SELECT create_hypertable('trades', 'created_at');
ALTER TABLE trades ADD PRIMARY KEY (id, created_at);


CREATE INDEX idx_trades_exchange     ON trades (exchange, created_at DESC);
CREATE INDEX idx_trades_market       ON trades (market, created_at DESC);
CREATE INDEX idx_trades_status       ON trades (status) WHERE status = 'open';
CREATE INDEX idx_trades_paper        ON trades (paper, created_at DESC);

-- ── SIGNALS ─────────────────────────────────────────────────────────────────
-- Every signal evaluated each cycle. Hypertable for time-series queries.
CREATE TABLE signals (
    id              BIGSERIAL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market          TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    raw_value       NUMERIC(20,8),
    normalized      NUMERIC(5,4),
    weight          NUMERIC(5,4),
    fired           BOOLEAN DEFAULT FALSE,
    trade_id        BIGINT
);

SELECT create_hypertable('signals', 'recorded_at');
ALTER TABLE signals ADD PRIMARY KEY (id, recorded_at);


CREATE INDEX idx_signals_type       ON signals (signal_type, recorded_at DESC);
CREATE INDEX idx_signals_trade      ON signals (trade_id);

-- ── CAPITAL SNAPSHOTS ───────────────────────────────────────────────────────
-- Snapshot taken every scan cycle. Powers the capital chart in Grafana.
CREATE TABLE capital_snapshots (
    id              BIGSERIAL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_usd       NUMERIC(12,2) NOT NULL,
    starting_usd    NUMERIC(12,2) NOT NULL,
    pct_change      NUMERIC(8,4),
    open_positions  INTEGER DEFAULT 0,
    daily_pnl       NUMERIC(12,2),
    note            TEXT
);

SELECT create_hypertable('capital_snapshots', 'recorded_at');
ALTER TABLE capital_snapshots ADD PRIMARY KEY (id, recorded_at);


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
    id              BIGSERIAL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT         NOT NULL,
    headline        TEXT,
    url             TEXT,
    sentiment       TEXT,
    impact_score    INTEGER,
    markets_affected TEXT,
    alerted         BOOLEAN DEFAULT FALSE
);

SELECT create_hypertable('news_events', 'fetched_at');
ALTER TABLE news_events ADD PRIMARY KEY (id, fetched_at);


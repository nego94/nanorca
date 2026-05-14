-- ══════════════════════════════════════════════════════════════════════════════
-- NANORCA — Migration 002: Add missing indexes
-- Run manually on VPS: docker compose exec -T postgres psql -U nanorca_user -d nanorca < migrations/002_add_indexes.sql
-- ══════════════════════════════════════════════════════════════════════════════

-- Index for close_trade_by_order_id() — primary close path for paper trades.
-- Without this, every close does a full scan across ALL TimescaleDB chunks.
CREATE INDEX IF NOT EXISTS idx_trades_order_id
    ON trades (exchange_order_id)
    WHERE exchange_order_id IS NOT NULL AND exchange_order_id != '';

-- Index for status='open' queries used in recovery + reporting.
-- The existing idx_trades_status covers this but only for status='open'.
-- Adding a compound index for the recover_from_db() query pattern.
CREATE INDEX IF NOT EXISTS idx_trades_open_paper
    ON trades (paper, opened_at)
    WHERE status = 'open';

-- Verify indexes created
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'trades'
ORDER BY indexname;

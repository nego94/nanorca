-- ══════════════════════════════════════════════════════════════════════════════
-- NANORCA — Migration 003: Add target_price and stop_price to trades
-- Run manually on VPS:
--   docker compose exec -T postgres psql -U nanorca_user -d nanorca < migrations/003_add_target_stop.sql
-- ══════════════════════════════════════════════════════════════════════════════

-- Store the exact target and stop prices set when a paper order is planned.
-- These are needed to reconstruct PaperOrder objects after a bot restart
-- so open positions continue to be monitored for hits without loss of data.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS target_price NUMERIC(20,8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_price   NUMERIC(20,8);

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'trades'
  AND column_name IN ('target_price', 'stop_price');

import { NextResponse } from "next/server";
import sql from "@/lib/db";

export const revalidate = 15; // cache for 15s

export async function GET() {
  try {
    const [capital] = await sql`
      SELECT total_usd, starting_usd, pct_change, daily_pnl, recorded_at
      FROM capital_snapshots
      ORDER BY recorded_at DESC
      LIMIT 1
    `;

    const openPositions = await sql`
      SELECT id, market, direction, entry_price, size_usd, confidence_score,
             opened_at, paper, exchange_order_id, target_price, stop_price
      FROM trades
      WHERE status = 'open'
      ORDER BY opened_at DESC
    `;

    const [stats] = await sql`
      SELECT
        COUNT(*) FILTER (WHERE status='closed')                                              AS total_closed,
        COUNT(*) FILTER (WHERE status='closed' AND win = true)                              AS total_wins,
        COALESCE(SUM(pnl_usd) FILTER (WHERE status='closed'), 0)                           AS total_pnl,
        COUNT(*) FILTER (WHERE status='closed' AND opened_at > NOW() - INTERVAL '24 hours') AS trades_24h,
        COUNT(*) FILTER (WHERE status='closed' AND win=true AND opened_at > NOW() - INTERVAL '24 hours') AS wins_24h,
        COUNT(*) FILTER (WHERE status='closed' AND opened_at > NOW() - INTERVAL '7 days')  AS trades_7d,
        COUNT(*) FILTER (WHERE status='closed' AND win=true AND opened_at > NOW() - INTERVAL '7 days')   AS wins_7d
      FROM trades
      WHERE paper = true
    `;

    return NextResponse.json({
      capital: capital ?? null,
      openPositions,
      stats,
    });
  } catch (err) {
    console.error("Status API error:", err);
    return NextResponse.json({ error: "DB unavailable" }, { status: 500 });
  }
}

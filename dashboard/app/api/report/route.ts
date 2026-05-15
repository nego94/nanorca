import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";

export const revalidate = 30;

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const period = searchParams.get("period") ?? "all";

  const since =
    period === "24h" ? "NOW() - INTERVAL '24 hours'" :
    period === "7d"  ? "NOW() - INTERVAL '7 days'" :
    period === "30d" ? "NOW() - INTERVAL '30 days'" :
    "'2020-01-01'::timestamptz";

  try {
    const [paper] = await sql.unsafe(`
      SELECT
        COUNT(*)                                          AS total,
        COUNT(*) FILTER (WHERE status='closed')           AS closed,
        COUNT(*) FILTER (WHERE status='open')             AS open,
        COUNT(*) FILTER (WHERE status='closed' AND win)   AS wins,
        COUNT(*) FILTER (WHERE status='closed' AND NOT win AND win IS NOT NULL) AS losses,
        COALESCE(SUM(pnl_usd) FILTER (WHERE status='closed'), 0)   AS pnl,
        COALESCE(SUM(fees_usd) FILTER (WHERE status='closed'), 0)  AS fees,
        COALESCE(AVG(pnl_usd) FILTER (WHERE status='closed' AND win), 0)           AS avg_win,
        COALESCE(AVG(pnl_usd) FILTER (WHERE status='closed' AND NOT win AND win IS NOT NULL), 0) AS avg_loss
      FROM trades
      WHERE paper = true AND opened_at >= ${since}
    `, []);

    const [live] = await sql.unsafe(`
      SELECT
        COUNT(*)                                          AS total,
        COUNT(*) FILTER (WHERE status='closed')           AS closed,
        COUNT(*) FILTER (WHERE status='open')             AS open,
        COUNT(*) FILTER (WHERE status='closed' AND win)   AS wins,
        COUNT(*) FILTER (WHERE status='closed' AND NOT win AND win IS NOT NULL) AS losses,
        COALESCE(SUM(pnl_usd) FILTER (WHERE status='closed'), 0)   AS pnl,
        COALESCE(SUM(fees_usd) FILTER (WHERE status='closed'), 0)  AS fees
      FROM trades
      WHERE paper = false AND opened_at >= ${since}
    `, []);

    // P&L over time for chart (daily buckets)
    const dailyPnl = await sql.unsafe(`
      SELECT
        DATE_TRUNC('day', opened_at) AS day,
        COALESCE(SUM(pnl_usd), 0)   AS pnl,
        COUNT(*) FILTER (WHERE status='closed') AS trades
      FROM trades
      WHERE paper = true AND status = 'closed' AND opened_at >= ${since}
      GROUP BY 1
      ORDER BY 1
    `, []);

    return NextResponse.json({ paper, live, dailyPnl });
  } catch (err) {
    console.error("Report API error:", err);
    return NextResponse.json({ error: "DB unavailable" }, { status: 500 });
  }
}

import { NextRequest, NextResponse } from "next/server";
import sql from "@/lib/db";

export const revalidate = 0;

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const limit  = Math.min(parseInt(searchParams.get("limit") ?? "50"), 200);
  const period = searchParams.get("period") ?? "all"; // all | 24h | 7d | 30d
  const paper  = searchParams.get("paper") !== "false";

  const since =
    period === "24h" ? "NOW() - INTERVAL '24 hours'" :
    period === "7d"  ? "NOW() - INTERVAL '7 days'" :
    period === "30d" ? "NOW() - INTERVAL '30 days'" :
    "'2020-01-01'::timestamptz";

  try {
    const trades = await sql.unsafe(`
      SELECT id, exchange, market, direction, entry_price, exit_price,
             size_usd, pnl_usd, fees_usd, confidence_score,
             status, outcome, win, paper, opened_at, closed_at,
             hold_minutes, claude_reasoning
      FROM trades
      WHERE paper = $1
        AND opened_at >= ${since}
      ORDER BY opened_at DESC
      LIMIT $2
    `, [paper, limit]);

    return NextResponse.json({ trades });
  } catch (err) {
    console.error("Trades API error:", err);
    return NextResponse.json({ error: "DB unavailable" }, { status: 500 });
  }
}

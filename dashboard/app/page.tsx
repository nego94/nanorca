import { StatusResponse } from "@/lib/types";
import { formatDistanceToNow } from "date-fns";

async function getStatus(): Promise<StatusResponse | null> {
  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/status`, {
      next: { revalidate: 15 },
    });
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-brand-card border border-brand-border rounded-lg p-4">
      <p className="text-xs text-brand-muted uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color ?? "text-white"}`}>{value}</p>
      {sub && <p className="text-xs text-brand-muted mt-1">{sub}</p>}
    </div>
  );
}

function PnlBadge({ value }: { value: number }) {
  const pos = value >= 0;
  return (
    <span className={`font-mono font-semibold ${pos ? "text-brand-green" : "text-brand-red"}`}>
      {pos ? "+" : ""}${value.toFixed(2)}
    </span>
  );
}

export default async function DashboardPage() {
  const data = await getStatus();

  if (!data) {
    return (
      <div className="flex items-center justify-center h-64 text-brand-muted">
        ⚠️ Could not reach database. Is the bot running?
      </div>
    );
  }

  const { capital, openPositions, stats } = data;

  const paperCapital  = capital?.total_usd ?? 0;
  const startingCap   = capital?.starting_usd ?? 0;
  const pctChange     = startingCap > 0 ? ((paperCapital - startingCap) / startingCap * 100) : 0;
  const dailyPnl      = capital?.daily_pnl ?? 0;
  const totalClosed   = Number(stats?.total_closed ?? 0);
  const totalWins     = Number(stats?.total_wins ?? 0);
  const totalPnl      = Number(stats?.total_pnl ?? 0);
  const wr24h = Number(stats?.trades_24h) > 0
    ? (Number(stats.wins_24h) / Number(stats.trades_24h) * 100).toFixed(1)
    : "—";
  const wr7d = Number(stats?.trades_7d) > 0
    ? (Number(stats.wins_7d) / Number(stats.trades_7d) * 100).toFixed(1)
    : "—";
  const wrAll = totalClosed > 0 ? (totalWins / totalClosed * 100).toFixed(1) : "—";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Overview</h1>
        <span className="text-xs text-brand-muted">
          {capital ? `Updated ${formatDistanceToNow(new Date(capital.recorded_at))} ago` : "No data"}
        </span>
      </div>

      {/* Capital cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Paper Capital"
          value={`$${paperCapital.toFixed(2)}`}
          sub={`${pctChange >= 0 ? "+" : ""}${pctChange.toFixed(1)}% from $${startingCap.toFixed(2)}`}
          color={pctChange >= 0 ? "text-brand-green" : "text-brand-red"}
        />
        <StatCard
          label="24h P&L"
          value={`${dailyPnl >= 0 ? "+" : ""}$${dailyPnl.toFixed(2)}`}
          sub="from DB (survives restarts)"
          color={dailyPnl >= 0 ? "text-brand-green" : "text-brand-red"}
        />
        <StatCard
          label="All-time P&L"
          value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`}
          sub={`${totalClosed} closed trades`}
          color={totalPnl >= 0 ? "text-brand-green" : "text-brand-red"}
        />
        <StatCard
          label="Win Rate"
          value={`${wrAll}%`}
          sub={`24h: ${wr24h}% | 7d: ${wr7d}%`}
          color="text-brand-blue"
        />
      </div>

      {/* Open positions */}
      <div className="bg-brand-card border border-brand-border rounded-lg">
        <div className="flex items-center justify-between px-4 py-3 border-b border-brand-border">
          <h2 className="font-semibold text-white">Open Positions</h2>
          <span className="text-xs text-brand-muted">{openPositions.length}/3 slots used</span>
        </div>
        {openPositions.length === 0 ? (
          <p className="text-brand-muted text-sm px-4 py-6 text-center">No open positions</p>
        ) : (
          <div className="divide-y divide-brand-border">
            {openPositions.map((p) => {
              const isLong = p.direction === "long";
              return (
                <div key={p.id} className="px-4 py-3 flex items-center gap-4">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded ${isLong ? "bg-green-900/40 text-brand-green" : "bg-red-900/40 text-brand-red"}`}>
                    {p.direction.toUpperCase()}
                  </span>
                  <span className="font-mono text-sm text-white">{p.market}</span>
                  <span className="text-xs text-brand-muted">
                    @${Number(p.entry_price ?? 0).toFixed(4)}
                  </span>
                  {p.target_price && (
                    <span className="text-xs text-brand-green">
                      → ${Number(p.target_price).toFixed(4)}
                    </span>
                  )}
                  {p.stop_price && (
                    <span className="text-xs text-brand-red">
                      stop ${Number(p.stop_price).toFixed(4)}
                    </span>
                  )}
                  <span className="text-xs text-brand-muted ml-auto">
                    {formatDistanceToNow(new Date(p.opened_at))} ago
                  </span>
                  {p.confidence_score && (
                    <span className="text-xs text-brand-muted">conf {p.confidence_score}</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Recent trades */}
      <RecentTrades />
    </div>
  );
}

async function RecentTrades() {
  let trades: any[] = [];
  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/trades?limit=8`, {
      next: { revalidate: 15 },
    });
    if (res.ok) {
      const data = await res.json();
      trades = data.trades ?? [];
    }
  } catch {}

  return (
    <div className="bg-brand-card border border-brand-border rounded-lg">
      <div className="flex items-center justify-between px-4 py-3 border-b border-brand-border">
        <h2 className="font-semibold text-white">Recent Trades</h2>
        <a href="/trades" className="text-xs text-brand-blue hover:underline">View all →</a>
      </div>
      {trades.length === 0 ? (
        <p className="text-brand-muted text-sm px-4 py-6 text-center">No trades yet</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-brand-muted border-b border-brand-border">
              <th className="px-4 py-2 text-left">Market</th>
              <th className="px-4 py-2 text-left">Dir</th>
              <th className="px-4 py-2 text-right">P&L</th>
              <th className="px-4 py-2 text-right">Hold</th>
              <th className="px-4 py-2 text-right">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-border">
            {trades.map((t: any) => (
              <tr key={t.id} className="hover:bg-brand-border/20 transition-colors">
                <td className="px-4 py-2 font-mono text-white">{t.market}</td>
                <td className="px-4 py-2">
                  <span className={`text-xs font-bold ${t.direction === "long" ? "text-brand-green" : "text-brand-red"}`}>
                    {t.direction?.toUpperCase()}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {t.status === "open" ? (
                    <span className="text-brand-yellow text-xs">open</span>
                  ) : (
                    <PnlBadge value={Number(t.pnl_usd ?? 0)} />
                  )}
                </td>
                <td className="px-4 py-2 text-right text-brand-muted">
                  {t.hold_minutes ? `${Math.round(t.hold_minutes)}m` : "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  {t.win === true  && <span className="text-brand-green text-xs">✓ WIN</span>}
                  {t.win === false && <span className="text-brand-red text-xs">✗ LOSS</span>}
                  {t.win === null  && <span className="text-brand-yellow text-xs">open</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

import { formatDistanceToNow } from "date-fns";

async function getTrades(period: string) {
  try {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/trades?limit=100&period=${period}`,
      { next: { revalidate: 0 } }
    );
    if (!res.ok) return [];
    const data = await res.json();
    return data.trades ?? [];
  } catch {
    return [];
  }
}

export default async function TradesPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const params = await searchParams;
  const period = params.period ?? "all";
  const trades = await getTrades(period);

  const periods = [
    { label: "All time", value: "all" },
    { label: "30 days",  value: "30d" },
    { label: "7 days",   value: "7d" },
    { label: "24h",      value: "24h" },
  ];

  const closed = trades.filter((t: any) => t.status === "closed");
  const wins   = closed.filter((t: any) => t.win === true).length;
  const pnl    = closed.reduce((s: number, t: any) => s + Number(t.pnl_usd ?? 0), 0);
  const wr     = closed.length > 0 ? (wins / closed.length * 100).toFixed(1) : "—";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Trade History</h1>
          <p className="text-sm text-brand-muted mt-0.5">
            {closed.length} closed | WR {wr}% | P&L{" "}
            <span className={pnl >= 0 ? "text-brand-green" : "text-brand-red"}>
              {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          {periods.map((p) => (
            <a
              key={p.value}
              href={`/trades?period=${p.value}`}
              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                period === p.value
                  ? "bg-brand-blue border-brand-blue text-white"
                  : "border-brand-border text-brand-muted hover:text-white"
              }`}
            >
              {p.label}
            </a>
          ))}
        </div>
      </div>

      <div className="bg-brand-card border border-brand-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-brand-muted border-b border-brand-border bg-brand-bg/50">
              <th className="px-4 py-2.5 text-left">Market</th>
              <th className="px-4 py-2.5 text-left">Dir</th>
              <th className="px-4 py-2.5 text-right">Entry</th>
              <th className="px-4 py-2.5 text-right">Exit</th>
              <th className="px-4 py-2.5 text-right">P&L</th>
              <th className="px-4 py-2.5 text-right">Fees</th>
              <th className="px-4 py-2.5 text-right">Hold</th>
              <th className="px-4 py-2.5 text-right">Conf</th>
              <th className="px-4 py-2.5 text-right">Result</th>
              <th className="px-4 py-2.5 text-right">When</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-border">
            {trades.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-brand-muted">
                  No trades in this period
                </td>
              </tr>
            )}
            {trades.map((t: any) => {
              const pnl = Number(t.pnl_usd ?? 0);
              const isOpen = t.status === "open";
              return (
                <tr key={t.id} className="hover:bg-brand-border/20 transition-colors">
                  <td className="px-4 py-2.5 font-mono text-white">{t.market}</td>
                  <td className="px-4 py-2.5">
                    <span className={`text-xs font-bold ${t.direction === "long" ? "text-brand-green" : "text-brand-red"}`}>
                      {t.direction?.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-brand-muted">
                    ${Number(t.entry_price ?? 0).toFixed(4)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-brand-muted">
                    {t.exit_price ? `$${Number(t.exit_price).toFixed(4)}` : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {isOpen ? (
                      <span className="text-brand-yellow">open</span>
                    ) : (
                      <span className={pnl >= 0 ? "text-brand-green" : "text-brand-red"}>
                        {pnl >= 0 ? "+" : ""}${pnl.toFixed(4)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right text-brand-muted font-mono">
                    {t.fees_usd ? `$${Number(t.fees_usd).toFixed(4)}` : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right text-brand-muted">
                    {t.hold_minutes ? `${Math.round(t.hold_minutes)}m` : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right text-brand-muted">
                    {t.confidence_score ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {t.win === true  && <span className="text-brand-green text-xs font-semibold">WIN</span>}
                    {t.win === false && <span className="text-brand-red text-xs font-semibold">LOSS</span>}
                    {t.win === null  && <span className="text-brand-yellow text-xs">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right text-xs text-brand-muted">
                    {formatDistanceToNow(new Date(t.opened_at), { addSuffix: true })}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

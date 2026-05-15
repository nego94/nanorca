async function getReport(period: string) {
  try {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/report?period=${period}`,
      { next: { revalidate: 30 } }
    );
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}

function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center py-2 border-b border-brand-border last:border-0">
      <span className="text-brand-muted text-sm">{label}</span>
      <span className={`font-semibold text-sm ${color ?? "text-white"}`}>{value}</span>
    </div>
  );
}

export default async function ReportPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const params = await searchParams;
  const period = params.period ?? "all";
  const data = await getReport(period);

  const periods = [
    { label: "All time", value: "all" },
    { label: "30 days",  value: "30d" },
    { label: "7 days",   value: "7d" },
    { label: "24h",      value: "24h" },
  ];

  const p = data?.paper;
  const closed = Number(p?.closed ?? 0);
  const wins   = Number(p?.wins ?? 0);
  const losses = Number(p?.losses ?? 0);
  const pnl    = Number(p?.pnl ?? 0);
  const fees   = Number(p?.fees ?? 0);
  const wr     = closed > 0 ? (wins / closed * 100).toFixed(1) : "—";
  const avgWin = Number(p?.avg_win ?? 0);
  const avgLoss = Number(p?.avg_loss ?? 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-white">Performance Report</h1>
        <div className="flex gap-2">
          {periods.map((per) => (
            <a
              key={per.value}
              href={`/report?period=${per.value}`}
              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                period === per.value
                  ? "bg-brand-blue border-brand-blue text-white"
                  : "border-brand-border text-brand-muted hover:text-white"
              }`}
            >
              {per.label}
            </a>
          ))}
        </div>
      </div>

      {!data ? (
        <div className="text-brand-muted text-center py-12">Could not load report data.</div>
      ) : (
        <div className="grid md:grid-cols-2 gap-6">
          {/* Paper stats */}
          <div className="bg-brand-card border border-brand-border rounded-lg p-5">
            <h2 className="font-semibold text-white mb-4">📄 Paper Trading</h2>
            <StatRow label="Total trades"    value={String(p?.total ?? 0)} />
            <StatRow label="Closed"          value={String(closed)} />
            <StatRow label="Open"            value={String(p?.open ?? 0)} />
            <StatRow label="Wins / Losses"   value={`${wins}W / ${losses}L`} />
            <StatRow label="Win rate"        value={`${wr}%`} color="text-brand-blue" />
            <StatRow label="Total P&L"       value={`${pnl >= 0 ? "+" : ""}$${pnl.toFixed(4)}`} color={pnl >= 0 ? "text-brand-green" : "text-brand-red"} />
            <StatRow label="Total fees"      value={`$${fees.toFixed(4)}`} color="text-brand-muted" />
            <StatRow label="Avg win"         value={`+$${avgWin.toFixed(4)}`} color="text-brand-green" />
            <StatRow label="Avg loss"        value={`$${avgLoss.toFixed(4)}`} color="text-brand-red" />
          </div>

          {/* Daily P&L chart placeholder — add Recharts here in Phase C */}
          <div className="bg-brand-card border border-brand-border rounded-lg p-5">
            <h2 className="font-semibold text-white mb-4">Daily P&L</h2>
            <div className="space-y-1.5">
              {(data.dailyPnl ?? []).slice(-14).map((d: any) => {
                const dayPnl = Number(d.pnl ?? 0);
                const bar = Math.abs(dayPnl) / 1.0; // scale
                const width = Math.min(100, bar * 100);
                const date = new Date(d.day).toLocaleDateString("en-US", { month: "short", day: "numeric" });
                return (
                  <div key={d.day} className="flex items-center gap-2 text-xs">
                    <span className="w-12 text-brand-muted text-right">{date}</span>
                    <div className="flex-1 flex items-center gap-1">
                      <div
                        className={`h-4 rounded-sm ${dayPnl >= 0 ? "bg-brand-green/70" : "bg-brand-red/70"}`}
                        style={{ width: `${Math.max(4, width)}%` }}
                      />
                      <span className={dayPnl >= 0 ? "text-brand-green" : "text-brand-red"}>
                        {dayPnl >= 0 ? "+" : ""}${dayPnl.toFixed(3)}
                      </span>
                      <span className="text-brand-muted">({d.trades} trades)</span>
                    </div>
                  </div>
                );
              })}
              {(data.dailyPnl ?? []).length === 0 && (
                <p className="text-brand-muted text-sm text-center py-8">No closed trades in this period.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

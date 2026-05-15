export default function SettingsPage() {
  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-xl font-bold text-white">Bot Settings</h1>

      <div className="bg-brand-card border border-brand-border rounded-lg p-5 space-y-4">
        <h2 className="font-semibold text-white">Current Configuration</h2>
        <p className="text-sm text-brand-muted">
          Settings are read from the <code className="text-brand-blue">.env</code> file on the VPS.
          To change settings, edit <code className="text-brand-blue">.env</code> and run:
        </p>
        <pre className="bg-brand-bg border border-brand-border rounded p-3 text-xs text-brand-green overflow-x-auto">
{`cd /root/nanorca
nano .env
docker compose up -d --build bot`}
        </pre>

        <div className="border-t border-brand-border pt-4 space-y-3">
          <Setting label="Scan interval"         value="30s"        note="Increase to 90s after 14-day baseline to cut API costs by 65%" />
          <Setting label="Confidence threshold"  value="65/100"     note="Min confidence to trade; 50–64 = suggestion only" />
          <Setting label="Max open positions"    value="3"          note="Paper: PaperOrderBook limit; Live: risk_manager cap" />
          <Setting label="Capital floor"         value="25%"        note="Emergency stop when paper capital drops below this % of start" />
          <Setting label="Trading mode"          value="hybrid"     note="nanorca_decide | conservative | hybrid | aggressive" />
          <Setting label="Cooldown after close"  value="15 min"     note="Per-market cooldown to prevent rapid re-entry" />
          <Setting label="BTC gate threshold"    value="-0.8%"      note="Skips LONG entries when BTC 10-min momentum below this" />
          <Setting label="Trail activate"        value="50% progress" note="Trailing stop activates when 50% of way to target" />
          <Setting label="Trail drop"            value="0.30%"      note="Close position if price drops 0.30% from peak (after trail activates)" />
        </div>
      </div>

      <div className="bg-brand-card border border-brand-border rounded-lg p-5">
        <h2 className="font-semibold text-white mb-3">⚠️ Security Reminders</h2>
        <ul className="space-y-1.5 text-sm text-brand-muted">
          <li>• <span className="text-white">PAPER_TRADING=true</span> — only set false after 14+ days profitable paper</li>
          <li>• <span className="text-white">Never commit .env to git</span> — all secrets stay on VPS only</li>
          <li>• <span className="text-white">Binance API: Trade only</span> — never enable Withdraw permission</li>
        </ul>
      </div>

      <div className="bg-brand-card border border-brand-border rounded-lg p-5">
        <h2 className="font-semibold text-white mb-3">Useful VPS Commands</h2>
        <div className="space-y-3 text-sm">
          <Cmd label="View live logs" cmd="docker compose logs -f --tail=50 bot" />
          <Cmd label="Restart bot (env change only)" cmd="docker compose restart bot" />
          <Cmd label="Rebuild bot (code change)" cmd="docker compose up -d --build bot" />
          <Cmd label="Stop bot" cmd="docker compose stop bot" />
          <Cmd label="Run migration" cmd="docker compose exec -T postgres psql -U nanorca_user -d nanorca < migrations/004_grid_tables.sql" />
        </div>
      </div>
    </div>
  );
}

function Setting({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm text-white">{label}</p>
        <p className="text-xs text-brand-muted mt-0.5">{note}</p>
      </div>
      <span className="text-sm font-mono text-brand-blue whitespace-nowrap">{value}</span>
    </div>
  );
}

function Cmd({ label, cmd }: { label: string; cmd: string }) {
  return (
    <div>
      <p className="text-brand-muted text-xs mb-1">{label}</p>
      <pre className="bg-brand-bg border border-brand-border rounded px-3 py-2 text-xs text-brand-green overflow-x-auto">
        {cmd}
      </pre>
    </div>
  );
}

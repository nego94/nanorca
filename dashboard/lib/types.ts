export interface Trade {
  id: number;
  exchange: string;
  market: string;
  direction: string;
  entry_price: number | null;
  exit_price: number | null;
  size_usd: number | null;
  pnl_usd: number | null;
  fees_usd: number | null;
  confidence_score: number | null;
  status: string;
  outcome: string | null;
  win: boolean | null;
  paper: boolean;
  opened_at: string;
  closed_at: string | null;
  hold_minutes: number | null;
  claude_reasoning: string | null;
}

export interface CapitalSnapshot {
  total_usd: number;
  starting_usd: number;
  pct_change: number | null;
  daily_pnl: number | null;
  recorded_at: string;
}

export interface BotStats {
  total_closed: number;
  total_wins: number;
  total_pnl: number;
  trades_24h: number;
  wins_24h: number;
  trades_7d: number;
  wins_7d: number;
}

export interface StatusResponse {
  capital: CapitalSnapshot | null;
  openPositions: Trade[];
  stats: BotStats;
}

export interface ReportSection {
  label: string;
  total: number;
  closed: number;
  open: number;
  wins: number;
  losses: number;
  pnl: number;
  fees: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
}

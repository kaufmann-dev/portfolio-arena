export interface Ref {
  id: number;
  slug: string;
  name: string;
}

export interface AuthMe {
  displayName: string;
}

export interface AllocationPolicy {
  min_position_weight_pct: number;
  max_position_weight_pct: number;
  derived_min_positions: number;
  derived_max_positions: number;
}

export interface PromptRef extends Ref {
  allocation_policy: AllocationPolicy;
}

export interface Metrics {
  has_data: boolean;
  start_date?: string;
  end_date?: string;
  itd_return?: number | null;
  spy_return?: number | null;
  vs_spy?: number | null;
  ann_volatility?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  cost_drag_pct?: number | null;
  turnover_pct?: number | null;
  r1m?: number | null;
  r3m?: number | null;
  r6m?: number | null;
  r1y?: number | null;
}

export interface PortfolioSummary {
  id: number;
  slug: string;
  name: string;
  agent: Ref;
  prompt: PromptRef | null;
  is_benchmark: boolean;
  status: "active" | "archived";
  cost_bps: number;
  inception: string | null;
  age_days: number | null;
  too_early: boolean;
  allocation_count: number;
  metrics: Metrics;
  sparkline: number[];
  stale_data: boolean;
  frozen_symbols: string[];
  error: string | null;
}

export interface SeriesPoint {
  date: string;
  nav: number;
}

export interface PositionOut {
  symbol: string;
  weight_pct: number;
  note?: string;
}

export interface AllocationOut {
  id: number;
  portfolio_id: number;
  entered_at: string;
  effective_date: string;
  applied_date: string | null;
  locked: boolean;
  note: string;
  turnover_pct: number | null;
  cost: number | null;
  positions: PositionOut[];
}

export interface Holding {
  symbol: string;
  weight_pct: number;
  target_weight_pct: number;
  entry_price?: number | null;
  current_price?: number | null;
  note?: string;
}

export interface PortfolioDetail extends PortfolioSummary {
  execution_prompt: string | null;
  series: SeriesPoint[];
  spy_series: SeriesPoint[];
  holdings: Holding[];
  stale_days: Record<string, string[]>;
  allocations: AllocationOut[];
}

export interface LeaderboardResponse {
  as_of: string | null;
  portfolios: PortfolioSummary[];
}

export interface PromptOut {
  id: number;
  slug: string;
  name: string;
  text: string;
  notes: string;
  allocation_policy: AllocationPolicy;
  updated_at?: string;
  portfolio_count?: number;
}

export interface AgentOut {
  id: number;
  slug: string;
  name: string;
  notes: string;
  portfolios?: { id: number; slug: string; name: string; status: string }[];
}

export interface CompareEntry {
  slug: string;
  name: string;
  is_benchmark: boolean;
  series: SeriesPoint[];
}

export interface CompareResponse {
  as_of: string | null;
  start: string | null;
  series: CompareEntry[];
  spy_series?: SeriesPoint[];
}

export interface ResolvedSymbol {
  symbol: string;
  security_type: "equity" | "etf";
  name: string;
  currency: string | null;
  exchange: string | null;
}

export interface ApiKeyOut {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

/** Returned only from POST /api/keys — `key` is the plaintext, shown once. */
export interface ApiKeyCreated extends ApiKeyOut {
  key: string;
}

export interface ApiKeysResponse {
  keys: ApiKeyOut[];
}

export type EvaluationRunStatus = "running" | "succeeded" | "failed" | "skipped";

export interface EvaluationRun {
  id: number;
  portfolio: Ref;
  agent: Ref;
  scheduled_for: string;
  model: string;
  codex_version: string;
  status: EvaluationRunStatus;
  attempt_count: number;
  lease_expires_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  allocation_id: number | null;
  report: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationRunsResponse {
  items: EvaluationRun[];
  next_cursor: string | null;
}

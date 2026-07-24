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

export interface PromptRef {
  id: number | null;
  slug: string;
  name: string;
  configurable: boolean;
  allocation_policy: AllocationPolicy;
}

export type PromptMode = "managed" | "rebuilt";

export interface AppSettings {
  default_cost_bps: number;
  managed_wrapper_prompt: string;
  rebuilt_wrapper_prompt: string;
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
  agent: AgentRef;
  prompt: PromptRef | null;
  prompt_mode: PromptMode | null;
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

export interface PortfolioResetResult {
  ok: true;
  deleted_allocations: number;
  cancelled_queued_runs: number;
  cancellation_requested_runs: number;
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

export interface AgentRef {
  id: number | null;
  slug: string;
  name: string;
  model: Ref | null;
  harness: HarnessRef | null;
  execution_model_id: string | null;
  reasoning_effort: string | null;
}

export interface AgentOut {
  id: number;
  slug: string;
  name: string;
  notes: string;
  model: Ref;
  harness: HarnessRef | null;
  execution_model_id: string | null;
  reasoning_effort: string | null;
  portfolio_count?: number;
  portfolios?: { id: number; slug: string; name: string; status: string }[];
}

export interface HarnessRef {
  id: string;
  name: string;
}

export interface ReasoningEffortDefinition {
  id: string;
  name: string;
}

export interface HarnessDefinition extends HarnessRef {
  automation_supported: boolean;
  reasoning_efforts: ReasoningEffortDefinition[];
}

export interface HarnessesResponse {
  harnesses: HarnessDefinition[];
}

export interface ModelHarnessCapability {
  harness: string;
  harness_name: string;
  execution_model_id: string;
  reasoning_efforts: string[];
}

export interface ModelDefinition extends Ref {
  notes: string;
  capabilities: ModelHarnessCapability[];
  agent_count: number;
  created_at: string;
  updated_at: string;
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

export type EvaluationRunStatus =
  "queued" | "running" | "cancel_requested" | "cancelled" | "succeeded" | "failed" | "skipped";

export type EvaluationTriggerKind = "scheduled" | "manual" | "retry";
export interface EvaluatorSettings {
  enabled: boolean;
  max_concurrency: number;
  poll_seconds: number;
  attempt_timeout_seconds: number;
  max_attempts: number;
  start_before_close_minutes: number;
  cutoff_before_close_minutes: number;
  updated_at: string;
}

export interface EvaluatorPortfolioRef extends Ref {
  status: "active" | "archived";
}

export interface PortfolioEvaluatorConfig {
  portfolio: EvaluatorPortfolioRef;
  agent: AgentOut;
  enabled: boolean;
  weekdays: number[];
  updated_at: string | null;
}

export interface EvaluatorRuntime {
  online: boolean;
  status: string;
  authenticated: boolean;
  harness: string;
  harness_version: string | null;
  active_run_count: number;
  last_heartbeat_at: string | null;
  last_error: string | null;
  instance_count: number;
}

export interface EvaluatorDashboard {
  settings: EvaluatorSettings;
  portfolios: PortfolioEvaluatorConfig[];
  runtime: EvaluatorRuntime;
}

export interface EvaluationRun {
  id: number;
  portfolio: Ref;
  agent: AgentOut;
  model: Ref;
  trigger_kind: EvaluationTriggerKind;
  retry_of_run_id: number | null;
  scheduled_for: string | null;
  deadline_at: string | null;
  harness: string;
  execution_model_id: string;
  reasoning_effort: string | null;
  timeout_seconds: number;
  max_attempts: number;
  harness_version: string | null;
  worker_id: string | null;
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

export interface EvaluationQueueItem {
  portfolio_id: number;
  action: "queued" | "existing" | "rejected";
  reason: string | null;
  run: EvaluationRun | null;
}

export interface EvaluationQueueResponse {
  items: EvaluationQueueItem[];
}

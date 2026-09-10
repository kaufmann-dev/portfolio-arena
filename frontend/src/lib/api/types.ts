export type ExecutionBoundary = "open" | "close";
export interface Boundary {
  timestamp: string;
  phase: ExecutionBoundary;
}
export interface ArenaVersion {
  id: number;
  name: string;
  evaluation_enabled: boolean;
  created_at: string;
}
export interface VersionsResponse {
  versions: ArenaVersion[];
}

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

export interface ModeAllocationPolicies {
  managed: AllocationPolicy | null;
  rebuilt: AllocationPolicy | null;
}

export interface PromptRef {
  id: number;
  slug: string;
  name: string;
  mode: PromptAvailability;
  direction: DirectionAvailability;
  configurable: boolean;
  allocation_policy: AllocationPolicy;
}

export type PromptMode = "managed" | "rebuilt";
export type PromptAvailability = PromptMode | "both";
export type Direction = "long" | "short";
export type DirectionAvailability = Direction | "both";
export type MarketDataStatus = "fresh" | "updating" | "stale" | "unavailable";
export type ArenaTrack = PromptMode;
export type EvidenceState = "pending" | "inconclusive" | "positive" | "negative";

export interface MarketDataSnapshot {
  as_of: Boundary | null;
  target_as_of: Boundary;
  market_data_status: MarketDataStatus;
}

export interface AppSettings {
  managed_allocation_policy: AllocationPolicy;
  rebuilt_allocation_policy: AllocationPolicy;
  managed_wrapper_prompt: string;
  rebuilt_wrapper_prompt: string;
  long_direction_instructions: string;
  short_direction_instructions: string;
}

export interface Metrics {
  has_data: boolean;
  start_at?: Boundary | null;
  end_at?: Boundary | null;
  itd_return?: number | null;
  spy_return?: number | null;
  ann_volatility?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  turnover_pct?: number | null;
  r1m?: number | null;
  r3m?: number | null;
  r6m?: number | null;
  r1y?: number | null;
}

export interface SeriesPoint {
  timestamp: string;
  phase: ExecutionBoundary;
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
  effective_at: Boundary;
  applied_at: Boundary | null;
  locked: boolean;
  note: string;
  turnover_pct: number | null;
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

export interface PortfolioResetResult {
  ok: true;
  deleted_allocations: number;
  deleted_signals: number;
  cancelled_queued_runs: number;
  cancellation_requested_runs: number;
}

export interface AdminPortfolio extends Ref {
  execution_locked: boolean;
  timing_editable: boolean;
  version_id: number;
  version: ArenaVersion;
  execution_boundary: ExecutionBoundary;
  prompt_mode: PromptMode;
  direction: Direction;
  agent: AgentRef;
  prompt: Ref;
  allocation_count: number;
  signal_count: number;
  evaluation_run_count: number;
  active_run_count: number;
  structure_editable: boolean;
  structure_blocker: string | null;
  prompt_editable: boolean;
}

export interface AdminPortfoliosResponse {
  portfolios: AdminPortfolio[];
}

export interface SignalOut {
  id: number;
  portfolio_id: number;
  entered_at: string;
  effective_at: Boundary;
  locked: boolean;
  note: string;
  provenance?: "integrated" | "browser_admin" | "mcp";
  positions: PositionOut[];
}

export interface AlphaMetrics {
  has_data: boolean;
  start_at?: Boundary | null;
  end_at?: Boundary | null;
  itd_return?: number | null;
  spy_return?: number | null;
  mean_daily_alpha?: number | null;
  median_daily_alpha?: number | null;
  cumulative_excess?: number | null;
  hit_rate?: number | null;
  ci_lower?: number | null;
  ci_upper?: number | null;
  ann_volatility?: number | null;
  sharpe?: number | null;
  information_ratio?: number | null;
  max_drawdown?: number | null;
  turnover_pct?: number | null;
  complete_count?: number;
  open_count?: number;
  completion_ratio?: number | null;
  eligible?: boolean;
  observation_count?: number;
  hac_lag?: number | null;
  hac_standard_error?: number | null;
  family_size?: number | null;
  evidence?: EvidenceState;
}

export interface ArenaPortfolioBase {
  version_id: number;
  version: ArenaVersion;
  execution_boundary: ExecutionBoundary;
  id: number;
  kind: PromptMode;
  slug: string;
  name: string;
  direction: Direction;
  agent: AgentRef;
  prompt: PromptRef;
  is_liquidated: boolean;
  liquidated_at: Boundary | null;
}

export interface ManagedArenaPortfolio extends ArenaPortfolioBase {
  kind: "managed";
  prompt_mode: "managed";
  inception: Boundary | null;
  age_days: number | null;
  allocation_count: number;
  metrics: Metrics & AlphaMetrics;
  rank: number | null;
  evidence: EvidenceState;
  rank_score: number | null;
  sparkline: number[];
  stale_data: boolean;
  frozen_symbols: string[];
  error: string | null;
}

export interface RebuiltPolicy {
  horizon: number;
}

export interface SignalHorizon {
  horizon: number;
  mean_daily_alpha: number | null;
  ci_lower: number | null;
  ci_upper: number | null;
  evidence: EvidenceState;
  complete_count: number;
  open_count: number;
  invalid_count?: number;
  completion_ratio: number | null;
  eligible: boolean;
  observation_count?: number;
  median_daily_alpha?: number | null;
  hit_rate?: number | null;
  hac_lag?: number | null;
  hac_standard_error?: number | null;
  family_size?: number | null;
}

export interface RebuiltCompletion {
  complete_count: number;
  open_count: number;
  completion_ratio: number | null;
  eligible: boolean;
}

export interface RebuiltArenaPortfolio extends ArenaPortfolioBase {
  inception: Boundary | null;
  kind: "rebuilt";
  prompt_mode: "rebuilt";
  rank: number | null;
  selected_policy: RebuiltPolicy | null;
  evidence: EvidenceState;
  rank_score: number | null;
  metrics: AlphaMetrics;
  completion: RebuiltCompletion;
  signal_horizons: SignalHorizon[];
  sparkline: number[];
  stale_data: boolean;
  frozen_symbols: string[];
  error: string | null;
}

export interface BenchmarkArenaPortfolio {
  kind: "benchmark";
  id: null;
  slug: "spy";
  name: string;
  direction: Direction;
  is_liquidated: boolean;
  liquidated_at: Boundary | null;
  rank: null;
  evidence: EvidenceState;
  rank_score: null;
  metrics: Metrics & AlphaMetrics;
  sparkline: number[];
}

export interface ManagedArenaResponse {
  track: "managed";
  direction: Direction;
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  ranking: Record<string, unknown>;
  portfolios: (ManagedArenaPortfolio | BenchmarkArenaPortfolio)[];
}

export interface RebuiltArenaResponse {
  track: "rebuilt";
  direction: Direction;
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  ranking: Record<string, unknown>;
  portfolios: (RebuiltArenaPortfolio | BenchmarkArenaPortfolio)[];
}

export interface ManagedPortfolioDetail extends ManagedArenaPortfolio {
  execution_prompt: string | null;
  execution_context_notice?: string | null;
  series: SeriesPoint[];
  spy_series: SeriesPoint[];
  holdings: Holding[];
  stale_days: Record<string, string[]>;
  allocations: AllocationOut[];
}

export interface ManagedPortfolioDetailResponse {
  track: "managed";
  direction: Direction;
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  portfolio: ManagedPortfolioDetail;
}

export interface ActiveCohort {
  signal_id: number;
  start_at: Boundary;
  end_at: Boundary | null;
  age_sessions: number;
  positions: PositionOut[];
}

export interface RebuiltPortfolioDetail extends RebuiltArenaPortfolio {
  execution_prompt: string | null;
  execution_context_notice?: string | null;
  series: SeriesPoint[];
  spy_series: SeriesPoint[];
  holdings: AggregateHolding[];
  active_cohorts: ActiveCohort[];
  signals: SignalOut[];
  signals_next_cursor: number | null;
  signal_horizons: SignalHorizon[];
  error: string | null;
}

export interface RebuiltPortfolioDetailResponse {
  track: "rebuilt";
  direction: Direction;
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  portfolio: RebuiltPortfolioDetail;
}

export type PortfolioAnalysisResponse = ManagedPortfolioDetailResponse | RebuiltPortfolioDetailResponse;

export interface AdminPortfolioDetailResponse {
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  portfolio: ManagedPortfolioDetail | RebuiltPortfolioDetail;
}

export type ArenaPortfolio = ManagedArenaPortfolio | RebuiltArenaPortfolio;

export interface PortfolioRefOut {
  version_id: number;
  version: ArenaVersion;
  execution_boundary: ExecutionBoundary;
  id: number;
  slug: string;
  name: string;
  direction: Direction;
  prompt_mode: PromptMode;
  is_liquidated?: boolean;
  liquidated_at?: Boundary | null;
}

export interface AggregateHolding {
  symbol: string;
  weight_pct: number;
}

export interface SignalsPage {
  signals: SignalOut[];
  next_cursor: number | null;
}

export interface PromptOut {
  id: number;
  slug: string;
  name: string;
  mode: PromptAvailability;
  direction: DirectionAvailability;
  managed_long_text: string | null;
  managed_short_text: string | null;
  rebuilt_long_text: string | null;
  rebuilt_short_text: string | null;
  notes: string;
  allocation_policies: ModeAllocationPolicies;
  updated_at?: string;
  portfolio_count?: number;
}

export interface AdminPrompt extends PromptOut {
  evaluation_run_count: number;
  can_delete: boolean;
  delete_blocker: string | null;
  created_at: string;
  updated_at: string;
  current_version: number;
  version_count: number;
  portfolio_count: number;
}

export interface PromptVersion {
  version: number;
  name: string;
  mode: PromptAvailability;
  direction: DirectionAvailability;
  managed_long_text: string | null;
  managed_short_text: string | null;
  rebuilt_long_text: string | null;
  rebuilt_short_text: string | null;
  notes: string;
  created_at: string;
  restored_from_version: number | null;
}

export interface AdminPromptsResponse {
  prompts: AdminPrompt[];
}

export interface PromptVersionsResponse {
  prompt_id: number;
  versions: PromptVersion[];
}

export interface AgentRef {
  id: number;
  slug: string;
  name: string;
  model: Ref;
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
  portfolios?: PortfolioRefOut[];
}

export interface AdminAgent extends AgentOut {
  portfolio_count: number;
  evaluation_run_count: number;
  can_delete: boolean;
  delete_blocker: string | null;
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
  evaluation_run_count: number;
  can_delete: boolean;
  delete_blocker: string | null;
  created_at: string;
  updated_at: string;
}

export interface CompareEntry {
  execution_boundary: ExecutionBoundary;
  slug: string;
  name: string;
  kind: "managed" | "rebuilt" | "benchmark";
  series: SeriesPoint[];
}

export interface CompareResponse {
  track: ArenaTrack;
  direction: Direction;
  as_of: Boundary | null;
  market_data_status: MarketDataStatus;
  start: Boundary | null;
  series: CompareEntry[];
  spy_series: SeriesPoint[];
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
  queue_before_close_minutes: number;
  queue_before_open_minutes: number;
  updated_at: string;
}

export interface EvaluatorPortfolioRef extends Ref {
  version_id: number;
  version: ArenaVersion;
  execution_boundary: ExecutionBoundary;
  prompt_mode: PromptMode;
  direction: Direction;
  is_liquidated: boolean;
  liquidated_at: Boundary | null;
}

export interface PortfolioEvaluatorConfig {
  portfolio: EvaluatorPortfolioRef;
  agent: AgentOut;
  enabled: boolean;
  weekdays: number[];
  updated_at: string | null;
}

export interface EvaluatorHarnessRuntime {
  online: boolean;
  status: string;
  authenticated: boolean;
  harness: string;
  harness_name: string;
  harness_version: string | null;
  active_run_count: number;
  last_heartbeat_at: string | null;
  last_error: string | null;
  instance_count: number;
}

export interface EvaluatorRuntime {
  online: boolean;
  status: string;
  active_run_count: number;
  last_heartbeat_at: string | null;
  instance_count: number;
  harnesses: EvaluatorHarnessRuntime[];
}

export interface EvaluatorDashboard {
  settings: EvaluatorSettings;
  portfolios: PortfolioEvaluatorConfig[];
  runtime: EvaluatorRuntime;
}

export interface EvaluationRun {
  id: number;
  portfolio: Ref & { direction: Direction; version_id: number; execution_boundary: ExecutionBoundary };
  execution_boundary: ExecutionBoundary;
  prompt_version_id: number | null;
  scheduled_boundary: Boundary | null;
  agent: AgentOut;
  model: Ref;
  trigger_kind: EvaluationTriggerKind;
  retry_of_run_id: number | null;
  scheduled_for: string | null;
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
  result: { kind: "allocation" | "signal"; id: number } | null;
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

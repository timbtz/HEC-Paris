// Backend types — snake_case literally as returned from the API.
// Money is integer cents (EUR). Cost is integer micro-USD.

export type EntryStatus = "posted" | "review" | "draft" | "reversed";
export type AccountingBasis = "accrual" | "cash";
export type TraceSource = "rule" | "agent" | "cache" | "human";

export interface JournalEntrySummary {
  id: number;
  basis: AccountingBasis;
  entry_date: string; // YYYY-MM-DD
  description: string;
  status: EntryStatus;
  source_pipeline: string | null;
  source_run_id: number | null;
  accrual_link_id: number | null;
  reversal_of_id: number | null;
  created_at: string; // ISO
  total_cents: number;
  line_count: number;
  // augmented client-side for the dashboard pulse
  employee_first_name?: string | null;
  confidence?: number | null;
  review_reason?: string | null;
}

export interface JournalEntriesResponse {
  items: JournalEntrySummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface Envelope {
  id: number;
  scope_kind: "employee" | "team" | "category";
  scope_id: number;
  category: string;
  period: string; // YYYY-MM
  cap_cents: number;
  soft_threshold_pct: number;
  used_cents: number;
  allocation_count: number;
  // optional augment
  employee_first_name?: string;
}

export interface EnvelopesResponse {
  items: Envelope[];
}

export interface JournalLine {
  id: number;
  entry_id: number;
  account_code: string;
  account_name?: string;
  debit_cents: number;
  credit_cents: number;
  counterparty_id: number | null;
  counterparty_name?: string;
  swan_transaction_id: number | null;
  document_id: number | null;
  description: string;
}

export interface DecisionTrace {
  id: number;
  line_id: number;
  source: TraceSource;
  rule_id: string | null;
  confidence: number;
  agent_decision_id_logical: string | null;
  approver_id: number | null;
  approved_at: string | null;
}

export interface AgentDecision {
  id: number;
  run_id_logical: number;
  node_id: string;
  source: TraceSource;
  runner: string;
  model: string;
  response_id: string;
  prompt_hash: string;
  alternatives_json: string;
  confidence: number;
  line_id_logical: number;
  latency_ms: number;
  finish_reason: string;
  temperature?: number;
}

export interface AgentCost {
  decision_id: number;
  employee_id: number | null;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_micro_usd: number;
}

export interface SourceRun {
  id: number;
  pipeline_name: string;
  status: "success" | "running" | "failed";
  started_at: string;
  completed_at?: string;
  elapsed_ms?: number;
}

export interface SwanTransaction {
  id: number;
  amount_cents: number;
  side: "debit" | "credit";
  counterparty_label: string;
  posted_at: string;
  raw_payload: Record<string, unknown>;
}

export interface DocumentMeta {
  id: number;
  sha256: string;
  kind: string;
  amount_cents: number | null;
  blob_path: string;
  filename?: string;
}

export interface EntryTrace {
  entry: JournalEntrySummary;
  lines: JournalLine[];
  traces: DecisionTrace[];
  agent_decisions: AgentDecision[];
  agent_costs: AgentCost[];
  source_run: SourceRun | null;
  swan_transactions: SwanTransaction[];
  documents: DocumentMeta[];
}

export interface Employee {
  id: number;
  full_name: string;
  first_name: string;
  department: string;
  email: string;
}

export interface RunSummary {
  id: number;
  pipeline_name: string;
  status: "success" | "running" | "failed";
  started_at: string;
  elapsed_ms: number | null;
  agent_cost_micro_usd: number;
  review_count: number;
}

export interface AiSpendTodayPoint {
  date: string;
  cost_micro_usd: number;
}

export interface AiSpendToday {
  total_today_micro_usd: number;
  series_14d: AiSpendTodayPoint[];
}

export type AccountingPeriodStatus = "open" | "closing" | "closed";

export interface AccountingPeriod {
  id: number;
  code: string; // YYYY-Qn
  start_date: string; // YYYY-MM-DD
  end_date: string; // YYYY-MM-DD
  status: AccountingPeriodStatus;
  closed_at: string | null;
  closed_by: number | null;
}

// Gamification (Phase 4.B)
export interface GamificationTask {
  id: number;
  title: string;
  description: string | null;
  department: string;
  coin_value: number;
  is_active: boolean;
  created_by_employee_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface GamificationCompletion {
  id: number;
  task_id: number | null;
  task_title: string | null;
  employee_id: number;
  employee_full_name: string | null;
  note: string | null;
  status: "pending" | "approved" | "rejected";
  coins_awarded: number;
  source: "manual" | "auto";
  agent_decision_id: number | null;
  reviewed_by_employee_id: number | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface GamificationReward {
  id: number;
  name: string;
  description: string | null;
  emoji: string | null;
  coin_cost: number;
  is_active: boolean;
}

export interface GamificationLeaderboardRow {
  employee_id: number;
  email: string;
  full_name: string | null;
  department: string | null;
  earned: number;
  earned_auto: number;
  earned_manual: number;
  adjustments: number;
  coins: number;
  call_count: number;
}

export interface GamificationLeaderboard {
  period: string;
  since: string | null;
  items: GamificationLeaderboardRow[];
  auto_coin_reward: number;
}

export interface GamificationToday {
  employee_id: number;
  coins_today: number;
  completions_today: number;
  daily_target: number;
  streak_days: number;
  daily_history: Array<{ date: string; completions: number }>;
  coins_balance: number;
}

export interface GamificationCoinAdjustment {
  id: number;
  employee_id: number;
  employee_full_name: string | null;
  adjusted_by_employee_id: number | null;
  adjusted_by_full_name: string | null;
  amount: number; // signed
  reason: string | null;
  created_at: string;
}

// AI cost pivot — /reports/ai-costs
export type AiCostsGroupKey =
  | "employee"
  | "department"
  | "provider"
  | "model"
  | "pipeline"
  | "node"
  | "day";

export interface AiCostsRow {
  // Whatever group_by keys were requested appear as string fields here.
  // Plus the four numeric aggregates below.
  [key: string]: string | number | null;
  cost_micro_usd: number;
  calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface AiCostsResponse {
  start: string;
  end: string;
  group_by: AiCostsGroupKey[];
  rows: AiCostsRow[];
  totals: {
    cost_micro_usd: number;
    calls: number;
    input_tokens: number;
    output_tokens: number;
  };
}

// SSE event types
export type SseEvent =
  | { event_type: "pipeline_started"; run_id: number; pipeline_name: string; version: string }
  | { event_type: "node_started"; run_id: number; node_id: string }
  | { event_type: "node_completed"; run_id: number; node_id: string; elapsed_ms: number; output?: unknown }
  | { event_type: "node_skipped"; run_id: number; node_id: string; reason: string }
  | { event_type: "node_failed"; run_id: number; node_id: string; error: string }
  | { event_type: "cache_hit"; run_id: number; node_id: string; cache_key: string }
  | { event_type: "pipeline_completed"; run_id: number }
  | { event_type: "pipeline_failed"; run_id: number; error: string; traceback?: string }
  | { event_type: "ledger.entry_posted"; entry_id: number; approver_id?: number; approved_at?: string }
  | { event_type: "envelope.decremented"; envelope_id: number; amount_cents: number; line_id: number }
  | { event_type: "review.enqueued"; entry_id: number; kind: string; confidence: number; reason: string }
  | { event_type: "report.rendered"; run_id: number; period_code: string; report_type: string; blob_path: string };

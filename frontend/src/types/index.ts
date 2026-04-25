// Single source of truth for backend response and event shapes.
// Anchored in comments to producing endpoints / event sources.

// ===== REST response shapes =====

// GET /journal_entries  →  { items: JournalEntryListItem[], total, limit, offset }
export interface JournalEntryListItem {
  id: number
  basis: 'cash' | 'accrual'
  entry_date: string                  // ISO date YYYY-MM-DD
  description: string | null
  status: 'draft' | 'posted' | 'review' | 'reversed'
  source_pipeline: string
  source_run_id: number
  accrual_link_id: number | null
  reversal_of_id: number | null
  created_at: string                  // ISO timestamp
  total_cents: number
  line_count: number
}
export interface JournalEntryListResponse {
  items: JournalEntryListItem[]
  total: number
  limit: number
  offset: number
}

// GET /envelopes  →  { items: EnvelopeRow[] }
export type EnvelopeCategory = 'food' | 'travel' | 'saas' | 'ai_tokens' | 'leasing'

export interface EnvelopeRow {
  id: number
  scope_kind: 'employee' | 'team' | 'company'
  scope_id: number | null
  category: EnvelopeCategory | string
  period: string                       // YYYY-MM
  cap_cents: number
  soft_threshold_pct: number
  used_cents: number
  allocation_count: number
}
export interface EnvelopeListResponse { items: EnvelopeRow[] }

// GET /journal_entries/{id}/trace
export interface TraceLine {
  id: number
  entry_id: number
  account_code: string
  debit_cents: number
  credit_cents: number
  counterparty_id: number | null
  swan_transaction_id: string | null
  document_id: number | null
  description: string | null
}
export interface TraceDecision {
  id: number
  run_id_logical: number
  node_id: string
  source: string
  runner: string
  model: string | null
  confidence: number | null
  line_id_logical: string | null
  latency_ms: number | null
  finish_reason: string | null
  alternatives_json: string | null
  started_at: string
  completed_at: string | null
}
export interface TraceCost {
  decision_id: number
  employee_id: number | null
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  reasoning_tokens: number
  cost_micro_usd: number
  created_at: string
}
export interface TraceMeta {
  id: number
  line_id: number
  source: string
  rule_id: string | null
  confidence: number | null
  agent_decision_id_logical: string | null
  parent_event_id: string | null
  approver_id: number | null
  approved_at: string | null
  created_at: string
}
export interface TraceResponse {
  entry: JournalEntryListItem & Record<string, unknown>
  lines: TraceLine[]
  traces: TraceMeta[]
  agent_decisions: TraceDecision[]
  agent_costs: TraceCost[]
  source_run: unknown | null
  swan_transactions: unknown[]
  documents: unknown[]
}

// ===== Dashboard SSE event shapes (from backend/orchestration/tools/*.py) =====

// gl_poster.py:142–154 — auto-post path (nested data, has ts)
export type LedgerEntryPostedEvent = {
  event_type: 'ledger.entry_posted'
  ts: string
  data: {
    entry_id: number
    basis: 'cash' | 'accrual'
    entry_date: string
    total_cents: number
    lines: number
    run_id: number
    employee_id: number | null
  }
}

// runs.py:317–324 — APPROVED variant; flat shape, no `data` key, no ts
export type LedgerEntryApprovedEvent = {
  event_type: 'ledger.entry_posted'
  entry_id: number
  approver_id: number
  approved_at: string
}

// budget_envelope.py:183–196
export type EnvelopeDecrementedEvent = {
  event_type: 'envelope.decremented'
  ts: string
  data: {
    envelope_id: number
    employee_id: number | null
    category: EnvelopeCategory | string
    period: string
    used_cents: number
    cap_cents: number
    soft_threshold_pct: number
    ledger_entry_id: number
  }
}
// budget_envelope.py:120–128
export type EnvelopeSkippedEvent = {
  event_type: 'envelope.skipped'
  ts: string
  data: { entry_id: number; reason: 'uncategorized'; employee_id: number | null }
}
// budget_envelope.py:138–147
export type EnvelopeNoEnvelopeEvent = {
  event_type: 'envelope.no_envelope'
  ts: string
  data: { entry_id: number; category: string; period: string; employee_id: number | null }
}
// review_queue.py:77–87
export type ReviewEnqueuedEvent = {
  event_type: 'review.enqueued'
  ts: string
  data: {
    review_id: number
    entry_id: number | null
    kind: string
    confidence: number | null
    reason: string
  }
}

export type DashboardEvent =
  | LedgerEntryPostedEvent
  | LedgerEntryApprovedEvent
  | EnvelopeDecrementedEvent
  | EnvelopeSkippedEvent
  | EnvelopeNoEnvelopeEvent
  | ReviewEnqueuedEvent

// ===== Per-run SSE event shape (from runs.py:127–159) =====

export interface RunEvent {
  run_id: number
  event_type:
    | 'pipeline_started'
    | 'pipeline_completed'
    | 'pipeline_failed'
    | 'node_started'
    | 'node_completed'
    | 'node_skipped'
    | 'node_failed'
    | 'cache_hit'
  node_id: string | null
  data: Record<string, unknown>
  ts: string
}

// ===== Run + employees =====

export interface RunSummary {
  id: number
  pipeline_name: string
  pipeline_version: number
  trigger_source: string
  trigger_payload: string
  employee_id_logical: string | null
  status: 'running' | 'completed' | 'failed'
  error: string | null
  started_at: string
  completed_at: string | null
}

export interface Employee {
  id: number
  full_name: string
  email: string
}

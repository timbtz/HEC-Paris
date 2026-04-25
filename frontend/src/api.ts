import type {
  JournalEntryListResponse,
  EnvelopeListResponse,
  TraceResponse,
  RunSummary,
} from '@/types'
import type {
  TrialBalanceResponse,
  BalanceSheetResponse,
  IncomeStatementResponse,
  CashflowResponse,
  BudgetVsActualsResponse,
  VatReturnResponse,
  ReportBasis,
} from '@/types/reports'

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    signal: init?.signal ?? AbortSignal.timeout(15_000),
  })
  if (!r.ok) {
    const text = await r.text().catch(() => '')
    throw new Error(`${r.status} ${r.statusText} on ${path}: ${text}`)
  }
  return r.json() as Promise<T>
}

export const api = {
  healthz: () => j<{ status: string }>('/healthz'),

  // List endpoints
  listJournalEntries: (
    params: { limit?: number; offset?: number; status?: string } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.limit !== undefined) qs.set('limit', String(params.limit))
    if (params.offset !== undefined) qs.set('offset', String(params.offset))
    if (params.status !== undefined) qs.set('status', params.status)
    const q = qs.toString()
    return j<JournalEntryListResponse>(
      `/journal_entries${q ? '?' + q : ''}`,
    )
  },
  listEnvelopes: (
    params: { employee_id?: number; period?: string; scope_kind?: string } = {},
  ) => {
    const qs = new URLSearchParams()
    if (params.employee_id !== undefined) qs.set('employee_id', String(params.employee_id))
    if (params.period !== undefined) qs.set('period', params.period)
    if (params.scope_kind !== undefined) qs.set('scope_kind', params.scope_kind)
    const q = qs.toString()
    return j<EnvelopeListResponse>(`/envelopes${q ? '?' + q : ''}`)
  },

  // Existing endpoints
  getRun: (runId: number) =>
    j<{ run: RunSummary; events: unknown[]; agent_decisions: unknown[] }>(
      `/runs/${runId}`,
    ),
  getEntryTrace: (entryId: number) =>
    j<TraceResponse>(`/journal_entries/${entryId}/trace`),
  approveEntry: (entryId: number, approverId: number) =>
    j<{ entry_id: number; approver_id: number; status: string }>(
      `/review/${entryId}/approve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approver_id: approverId }),
      },
    ),
  uploadDocument: (file: File, employeeId?: number) => {
    const fd = new FormData()
    fd.append('file', file)
    if (employeeId !== undefined) fd.append('employee_id', String(employeeId))
    return j<{ document_id: number; sha256: string; run_id: number; stream_url: string }>(
      '/documents/upload',
      { method: 'POST', body: fd, signal: AbortSignal.timeout(60_000) },
    )
  },

  // ---- Reports (Phase 3 Slice C) ----
  fetchTrialBalance: (params: { as_of: string; basis?: ReportBasis }) => {
    const qs = new URLSearchParams({ as_of: params.as_of })
    if (params.basis) qs.set('basis', params.basis)
    return j<TrialBalanceResponse>(`/reports/trial_balance?${qs}`)
  },
  fetchBalanceSheet: (params: { as_of: string; basis?: ReportBasis }) => {
    const qs = new URLSearchParams({ as_of: params.as_of })
    if (params.basis) qs.set('basis', params.basis)
    return j<BalanceSheetResponse>(`/reports/balance_sheet?${qs}`)
  },
  fetchIncomeStatement: (params: {
    from: string
    to: string
    basis?: ReportBasis
  }) => {
    const qs = new URLSearchParams({ from: params.from, to: params.to })
    if (params.basis) qs.set('basis', params.basis)
    return j<IncomeStatementResponse>(`/reports/income_statement?${qs}`)
  },
  fetchCashflow: (params: { from: string; to: string }) =>
    j<CashflowResponse>(
      `/reports/cashflow?from=${encodeURIComponent(params.from)}&to=${encodeURIComponent(params.to)}`,
    ),
  fetchBudgetVsActuals: (params: {
    period: string
    employee_id?: number
    category?: string
  }) => {
    const qs = new URLSearchParams({ period: params.period })
    if (params.employee_id !== undefined) qs.set('employee_id', String(params.employee_id))
    if (params.category) qs.set('category', params.category)
    return j<BudgetVsActualsResponse>(`/reports/budget_vs_actuals?${qs}`)
  },
  fetchVatReturn: (params: { period: string }) =>
    j<VatReturnResponse>(
      `/reports/vat_return?period=${encodeURIComponent(params.period)}`,
    ),

  // Trigger the period_close pipeline (Phase 3 Slice D).
  runPeriodClose: (period_code: string) =>
    j<{ run_id: number; stream_url: string }>(
      '/pipelines/run/period_close',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger_payload: { period_code } }),
      },
    ),
}

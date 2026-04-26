import { apiFetch, ApiError, type ApiResult } from "@/lib/api";
import * as mocks from "@/lib/mocks";
import type {
  AccountingPeriod,
  AiCostsGroupKey,
  AiCostsResponse,
  AiSpendToday,
  Employee,
  EntryTrace,
  EnvelopesResponse,
  GamificationCoinAdjustment,
  GamificationCompletion,
  GamificationLeaderboard,
  GamificationReward,
  GamificationTask,
  GamificationToday,
  JournalEntriesResponse,
  RunSummary,
} from "@/lib/types";

// Wired endpoints (with fallback)
export const fetchJournalEntries = (params: {
  limit?: number;
  offset?: number;
  status?: string;
} = {}): Promise<ApiResult<JournalEntriesResponse>> => {
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.status && params.status !== "all") qs.set("status", params.status);
  return apiFetch(`/journal_entries?${qs.toString()}`, {}, () => mocks.listJournalEntries(params));
};

export const fetchEnvelopes = (params: { period?: string; employee_id?: number } = {}): Promise<
  ApiResult<EnvelopesResponse>
> => {
  const qs = new URLSearchParams();
  if (params.period) qs.set("period", params.period);
  if (params.employee_id) qs.set("employee_id", String(params.employee_id));
  return apiFetch(`/envelopes?${qs.toString()}`, {}, () => mocks.listEnvelopes(params));
};

// Spending → trace clicks pass through SPEND_TX mock ids that the real backend
// doesn't know about. Falling through to the mock on 404 keeps the demo coherent
// while still showing real traces when the entry exists.
export const fetchEntryTrace = async (
  entryId: number,
): Promise<ApiResult<EntryTrace>> => {
  try {
    return await apiFetch<EntryTrace>(`/journal_entries/${entryId}/trace`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      const data = await mocks.entryTrace(entryId);
      return { data, mocked: true };
    }
    throw err;
  }
};

export const approveEntry = (entryId: number, approverId: number) =>
  apiFetch<{ entry_id: number; approver_id: number; status: string }>(
    `/review/${entryId}/approve`,
    { method: "POST", body: { approver_id: approverId } },
    () => ({ entry_id: entryId, approver_id: approverId, status: "approved" }),
  );

// Mocked endpoints
export const fetchEmployees = (): Promise<ApiResult<{ items: Employee[] }>> =>
  apiFetch(`/employees`, {}, () => ({ items: mocks.EMPLOYEES }));

export const fetchAiSpendToday = (): Promise<ApiResult<AiSpendToday>> =>
  apiFetch(`/ai-spend/today`, {}, () => mocks.aiSpendToday());

// /reports/ai-costs — pivots audit.agent_costs by any subset of
// {employee, provider, model, pipeline, node}. start/end are inclusive
// YYYY-MM-DD. Defaults: start = first of current month, end = today.
// Mock fallback ships an empty response so the page renders empty-state
// when the backend is offline rather than crashing.
export const fetchAiCosts = (params: {
  start?: string;
  end?: string;
  groupBy?: AiCostsGroupKey[];
} = {}): Promise<ApiResult<AiCostsResponse>> => {
  const qs = new URLSearchParams();
  if (params.start) qs.set("start", params.start);
  if (params.end) qs.set("end", params.end);
  if (params.groupBy && params.groupBy.length > 0) {
    qs.set("group_by", params.groupBy.join(","));
  }
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = today.slice(0, 7) + "-01";
  return apiFetch(
    `/reports/ai-costs?${qs.toString()}`,
    {},
    () => ({
      start: params.start ?? monthStart,
      end: params.end ?? today,
      group_by: params.groupBy ?? ["employee", "provider"],
      rows: [],
      totals: { cost_micro_usd: 0, calls: 0, input_tokens: 0, output_tokens: 0 },
    }),
  );
};

export const fetchRecentRuns = (
  limit = 8,
): Promise<ApiResult<{ items: RunSummary[]; total: number }>> =>
  apiFetch(`/runs?limit=${limit}`, {}, () => mocks.listRuns(limit));

// Accounting periods — used to populate the period_code picker before
// triggering reporting pipelines (period_close / vat_return / year_end_close).
// Returns periods sorted by start_date DESC. Mock fallback yields an empty
// list so the picker degrades to a free-text fallback when offline.
export const fetchAccountingPeriods = (): Promise<ApiResult<AccountingPeriod[]>> =>
  apiFetch(`/accounting_periods`, {}, () => [] as AccountingPeriod[]);

// ─────────────────────────────────────────────────────────────────────────
// Reports (Phase 3 SQL-only — backend defaults to basis=cash since the
// demo seed posts cash-basis Swan transactions)
// ─────────────────────────────────────────────────────────────────────────

export interface TrialBalanceLine {
  code: string;
  name: string;
  type: string;
  total_debit_cents: number;
  total_credit_cents: number;
  balance_cents: number;
}
export interface TrialBalanceResponse {
  as_of: string;
  basis: string;
  currency: string;
  lines: TrialBalanceLine[];
  totals: { total_debit_cents: number; total_credit_cents: number; balanced: boolean };
}

export interface BalanceSheetSection {
  code: string;
  name: string;
  type: string;
  balance_cents: number;
}
export interface BalanceSheetResponse {
  as_of: string;
  basis: string;
  currency: string;
  sections: {
    assets: BalanceSheetSection[];
    liabilities: BalanceSheetSection[];
    equity: BalanceSheetSection[];
  };
  totals: { total_assets_cents: number; total_liabilities_equity_cents: number; balanced: boolean };
  provisional?: boolean;
}

export interface IncomeStatementSection {
  code: string;
  name: string;
  amount_cents: number;
}
export interface IncomeStatementResponse {
  from: string;
  to: string;
  basis: string;
  currency: string;
  sections: { revenue: IncomeStatementSection[]; expenses: IncomeStatementSection[] };
  totals: { revenue_cents: number; expenses_cents: number; net_income_cents: number };
}

export interface VatRow {
  rate_bp: number;
  rate_pct: number;
  collected_cents: number;
  deductible_cents: number;
  net_cents: number;
}
export interface VatReturnResponse {
  period: string;
  currency: string;
  rows: VatRow[];
  totals: { collected_cents: number; deductible_cents: number; net_cents: number };
}

const todayIso = () => new Date().toISOString().slice(0, 10);
const monthStart = () => todayIso().slice(0, 7) + "-01";

export const fetchTrialBalance = (
  asOf?: string,
): Promise<ApiResult<TrialBalanceResponse>> =>
  apiFetch(
    `/reports/trial_balance?as_of=${asOf ?? todayIso()}`,
    {},
    () => ({
      as_of: asOf ?? todayIso(), basis: "cash", currency: "EUR",
      lines: [], totals: { total_debit_cents: 0, total_credit_cents: 0, balanced: true },
    }),
  );

export const fetchBalanceSheet = (
  asOf?: string,
): Promise<ApiResult<BalanceSheetResponse>> =>
  apiFetch(
    `/reports/balance_sheet?as_of=${asOf ?? todayIso()}`,
    {},
    () => ({
      as_of: asOf ?? todayIso(), basis: "cash", currency: "EUR",
      sections: { assets: [], liabilities: [], equity: [] },
      totals: { total_assets_cents: 0, total_liabilities_equity_cents: 0, balanced: true },
    }),
  );

export const fetchIncomeStatement = (
  from?: string, to?: string,
): Promise<ApiResult<IncomeStatementResponse>> =>
  apiFetch(
    `/reports/income_statement?from=${from ?? monthStart()}&to=${to ?? todayIso()}`,
    {},
    () => ({
      from: from ?? monthStart(), to: to ?? todayIso(), basis: "cash", currency: "EUR",
      sections: { revenue: [], expenses: [] },
      totals: { revenue_cents: 0, expenses_cents: 0, net_income_cents: 0 },
    }),
  );

export const fetchVatReturn = (period: string): Promise<ApiResult<VatReturnResponse>> =>
  apiFetch(
    `/reports/vat_return?period=${period}`,
    {},
    () => ({
      period, currency: "EUR", rows: [],
      totals: { collected_cents: 0, deductible_cents: 0, net_cents: 0 },
    }),
  );

// Gamification (Phase 4.B) — port of TACL-GROUP/pulse-ai-grow.
// `x-agnes-author` is the auth seam; we let the user set it via a small
// header picker on the Adoption page (defaults to tim@hec.example so the
// demo manager view works out of the box).
const ga = (author: string) => ({ "x-agnes-author": author });

export const fetchGamificationTasks = (): Promise<ApiResult<{ items: GamificationTask[] }>> =>
  apiFetch(`/gamification/tasks?active=true`, {}, () => ({ items: [] }));

export const fetchGamificationCompletions = (
  params: { status?: string; employee_id?: number; source?: string } = {},
): Promise<ApiResult<{ items: GamificationCompletion[] }>> => {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.employee_id) qs.set("employee_id", String(params.employee_id));
  if (params.source) qs.set("source", params.source);
  return apiFetch(`/gamification/completions?${qs.toString()}`, {}, () => ({ items: [] }));
};

export const fetchGamificationRewards = (): Promise<ApiResult<{ items: GamificationReward[] }>> =>
  apiFetch(`/gamification/rewards`, {}, () => ({ items: [] }));

export const fetchGamificationLeaderboard = (
  period: "week" | "month" | "all" = "month",
): Promise<ApiResult<GamificationLeaderboard>> =>
  apiFetch(`/gamification/leaderboard?period=${period}`, {}, () => ({
    period, since: null, items: [], auto_coin_reward: 5,
  }));

export const fetchGamificationToday = (
  employeeId: number,
): Promise<ApiResult<GamificationToday>> =>
  apiFetch(`/gamification/today/${employeeId}`, {}, () => ({
    employee_id: employeeId, coins_today: 0, completions_today: 0,
    daily_target: 100, streak_days: 0, daily_history: [], coins_balance: 0,
  }));

export const submitGamificationCompletion = (
  taskId: number, note: string | undefined, author: string,
) =>
  apiFetch<{ id: number; status: string }>(
    `/gamification/completions`,
    { method: "POST", body: { task_id: taskId, note }, headers: ga(author) },
  );

export const approveGamificationCompletion = (id: number, author: string) =>
  apiFetch<{ id: number; status: string; coins_awarded: number }>(
    `/gamification/completions/${id}/approve`,
    { method: "POST", headers: ga(author) },
  );

export const rejectGamificationCompletion = (id: number, author: string) =>
  apiFetch<{ id: number; status: string }>(
    `/gamification/completions/${id}/reject`,
    { method: "POST", headers: ga(author) },
  );

export const submitGamificationRedemption = (
  rewardId: number, author: string,
) =>
  apiFetch<{ id: number; status: string; coin_cost: number }>(
    `/gamification/redemptions`,
    { method: "POST", body: { reward_id: rewardId }, headers: ga(author) },
  );

export const fetchGamificationCoinAdjustments = (
  params: { employee_id?: number; limit?: number } = {},
): Promise<ApiResult<{ items: GamificationCoinAdjustment[] }>> => {
  const qs = new URLSearchParams();
  if (params.employee_id) qs.set("employee_id", String(params.employee_id));
  if (params.limit) qs.set("limit", String(params.limit));
  return apiFetch(`/gamification/coin_adjustments?${qs.toString()}`, {}, () => ({ items: [] }));
};

export const submitGamificationCoinAdjustment = (
  body: { employee_id: number; amount: number; reason?: string },
  author: string,
) =>
  apiFetch<{ id: number; new_balance: number }>(
    `/gamification/coin_adjustments`,
    { method: "POST", body, headers: ga(author) },
  );

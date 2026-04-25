import { useEffect, useState } from 'react'
import { api } from '@/api'
import type {
  AnyReportResponse,
  BalanceSheetResponse,
  BudgetVsActualsResponse,
  CashflowResponse,
  IncomeStatementResponse,
  ReportBasis,
  ReportType,
  TrialBalanceResponse,
  VatReturnResponse,
} from '@/types/reports'
import { ReportTypeSelect } from './ReportTypeSelect'
import { PeriodPicker, defaultFilter, type PeriodFilter } from './PeriodPicker'
import { ReportTable, type ReportRow } from './ReportTable'

export function ReportsTab() {
  const [reportType, setReportType] = useState<ReportType>('trial_balance')
  const [filter, setFilter] = useState<PeriodFilter>(() =>
    defaultFilter('trial_balance'),
  )
  // Basis applies only to trial_balance / balance_sheet / income_statement.
  // Demo seed posts cash-basis only, so default to 'cash' to surface data.
  const [basis, setBasis] = useState<ReportBasis>('cash')
  const basisApplies =
    reportType === 'trial_balance' ||
    reportType === 'balance_sheet' ||
    reportType === 'income_statement'
  const [data, setData] = useState<{
    type: ReportType
    payload: AnyReportResponse
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState<{ runId: number } | null>(null)

  // Re-fetch on type, filter, or basis change.
  useEffect(() => {
    let stale = false
    setData(null)
    setError(null)
    fetchOne(reportType, filter, basis)
      .then((payload) => {
        if (!stale) setData({ type: reportType, payload })
      })
      .catch((e) => { if (!stale) setError(String(e)) })
    return () => { stale = true }
  }, [reportType, filter, basis])

  // Reset filter when report-type switches between picker shapes.
  function changeType(next: ReportType) {
    setReportType(next)
    setFilter(defaultFilter(next))
  }

  async function runClose() {
    if (filter.kind !== 'period') return
    try {
      const r = await api.runPeriodClose(filter.period)
      setClosing({ runId: r.run_id })
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="px-6 py-6 space-y-4">
      <div className="flex flex-wrap gap-4 items-end">
        <ReportTypeSelect value={reportType} onChange={changeType} />
        <PeriodPicker value={filter} onChange={setFilter} />
        {basisApplies && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-zinc-600 font-medium">Basis</span>
            <select
              className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm
                         focus:border-zinc-900 focus:outline-none"
              value={basis}
              onChange={(e) => setBasis(e.target.value as ReportBasis)}
            >
              <option value="cash">Cash</option>
              <option value="accrual">Accrual</option>
            </select>
          </label>
        )}
        {reportType === 'budget_vs_actuals' && filter.kind === 'period' && (
          <button
            onClick={runClose}
            className="bg-zinc-900 text-white text-sm font-medium px-4 py-2 rounded-md
                       hover:bg-zinc-700 transition"
          >
            Run period close
          </button>
        )}
      </div>

      {closing && (
        <div className="rounded-md bg-emerald-50 border border-emerald-200 px-3 py-2 text-sm text-emerald-800">
          Period close kicked off — run #{closing.runId}.
          <button
            className="ml-3 underline"
            onClick={() => setClosing(null)}
          >
            dismiss
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-md bg-rose-50 border border-rose-200 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {!data && !error && (
        <div className="text-sm text-zinc-500">Loading…</div>
      )}

      {data && data.type === reportType && (
        <RenderReport reportType={data.type} data={data.payload} />
      )}
    </div>
  )
}

async function fetchOne(
  t: ReportType,
  f: PeriodFilter,
  basis: ReportBasis,
): Promise<AnyReportResponse> {
  switch (t) {
    case 'trial_balance':
      if (f.kind !== 'as_of') throw new Error('trial_balance needs as_of')
      return api.fetchTrialBalance({ as_of: f.as_of, basis })
    case 'balance_sheet':
      if (f.kind !== 'as_of') throw new Error('balance_sheet needs as_of')
      return api.fetchBalanceSheet({ as_of: f.as_of, basis })
    case 'income_statement':
      if (f.kind !== 'range') throw new Error('income_statement needs range')
      return api.fetchIncomeStatement({ from: f.from, to: f.to, basis })
    case 'cashflow':
      if (f.kind !== 'range') throw new Error('cashflow needs range')
      return api.fetchCashflow({ from: f.from, to: f.to })
    case 'budget_vs_actuals':
      if (f.kind !== 'period') throw new Error('budget_vs_actuals needs period')
      return api.fetchBudgetVsActuals({ period: f.period })
    case 'vat_return':
      if (f.kind !== 'period') throw new Error('vat_return needs period')
      return api.fetchVatReturn({ period: f.period })
  }
}

function RenderReport({
  reportType,
  data,
}: {
  reportType: ReportType
  data: AnyReportResponse
}) {
  switch (reportType) {
    case 'trial_balance':
      return <TrialBalanceView data={data as TrialBalanceResponse} />
    case 'balance_sheet':
      return <BalanceSheetView data={data as BalanceSheetResponse} />
    case 'income_statement':
      return <IncomeStatementView data={data as IncomeStatementResponse} />
    case 'cashflow':
      return <CashflowView data={data as CashflowResponse} />
    case 'budget_vs_actuals':
      return <BudgetVsActualsView data={data as BudgetVsActualsResponse} />
    case 'vat_return':
      return <VatReturnView data={data as VatReturnResponse} />
  }
}

function TrialBalanceView({ data }: { data: TrialBalanceResponse }) {
  const rows: ReportRow[] = data.lines
    .filter((l) => l.total_debit_cents !== 0 || l.total_credit_cents !== 0)
    .map((l) => ({
      label: `${l.code}  ${l.name}`,
      sublabel: l.type,
      cents: l.balance_cents,
    }))
  return (
    <>
      <div className="text-xs text-zinc-500">
        as of {data.as_of} · basis {data.basis} · {data.totals.balanced ? '✓ balanced' : '✗ unbalanced'}
      </div>
      <ReportTable
        rows={rows}
        totalLabel="Total balance"
        totalCents={data.totals.total_debit_cents - data.totals.total_credit_cents}
      />
    </>
  )
}

function BalanceSheetView({ data }: { data: BalanceSheetResponse }) {
  const rows: ReportRow[] = [
    ...data.sections.assets.map((l) => ({
      label: `${l.code}  ${l.name}`,
      cents: l.balance_cents,
      group: 'Assets',
    })),
    ...data.sections.liabilities.map((l) => ({
      label: `${l.code}  ${l.name}`,
      cents: l.balance_cents,
      group: 'Liabilities',
    })),
    ...data.sections.equity.map((l) => ({
      label: `${l.code}  ${l.name}`,
      cents: l.balance_cents,
      group: 'Equity',
    })),
  ]
  return (
    <>
      <div className="text-xs text-zinc-500">
        as of {data.as_of} · basis {data.basis}
        {data.provisional && ' · provisional retained earnings (no year-end close)'}
        {data.totals.balanced ? ' · ✓ balanced' : ' · ✗ unbalanced'}
      </div>
      <ReportTable
        rows={rows}
        totalLabel="Total assets"
        totalCents={data.totals.total_assets_cents}
      />
    </>
  )
}

function IncomeStatementView({ data }: { data: IncomeStatementResponse }) {
  const rows: ReportRow[] = [
    ...data.sections.revenue.map((l) => ({
      label: `${l.code}  ${l.name}`,
      cents: l.balance_cents,
      group: 'Revenue',
    })),
    ...data.sections.expense.map((l) => ({
      label: `${l.code}  ${l.name}`,
      cents: l.balance_cents,
      group: 'Expense',
    })),
  ]
  return (
    <>
      <div className="text-xs text-zinc-500">
        {data.from} → {data.to} · basis {data.basis}
      </div>
      <ReportTable
        rows={rows}
        totalLabel="Net income"
        totalCents={data.totals.net_income_cents}
      />
    </>
  )
}

function CashflowView({ data }: { data: CashflowResponse }) {
  const rows: ReportRow[] = [
    { label: 'Operating', cents: data.sections.operating_cents, group: 'Sections' },
    { label: 'Investing', cents: data.sections.investing_cents, group: 'Sections' },
    { label: 'Financing', cents: data.sections.financing_cents, group: 'Sections' },
    {
      label: 'Opening cash balance',
      cents: data.totals.opening_balance_cents,
      group: 'Balances',
    },
    {
      label: 'Closing cash balance',
      cents: data.totals.closing_balance_cents,
      group: 'Balances',
    },
  ]
  return (
    <>
      <div className="text-xs text-zinc-500">
        {data.from} → {data.to}
      </div>
      <ReportTable
        rows={rows}
        totalLabel="Net change"
        totalCents={data.totals.net_change_cents}
      />
    </>
  )
}

function BudgetVsActualsView({ data }: { data: BudgetVsActualsResponse }) {
  const rows: ReportRow[] = data.lines.map((l) => ({
    label: `${l.scope_kind} ${l.scope_id ?? '∅'} · ${l.category}`,
    sublabel: `cap ${(l.cap_cents / 100).toFixed(2)} · ${l.pct_used.toFixed(0)}% used`,
    cents: l.used_cents,
  }))
  return (
    <>
      <div className="text-xs text-zinc-500">period {data.period}</div>
      <ReportTable
        rows={rows}
        totalLabel="Total used"
        totalCents={data.totals.total_used_cents}
      />
    </>
  )
}

function VatReturnView({ data }: { data: VatReturnResponse }) {
  const rows: ReportRow[] = data.lines.map((l) => ({
    label: `${l.gl_account} · ${l.rate_bp / 100}%`,
    cents: l.vat_cents,
  }))
  return (
    <>
      <div className="text-xs text-zinc-500">
        period {data.period} · collected {(data.totals.collected_cents / 100).toFixed(2)} · deductible {(data.totals.deductible_cents / 100).toFixed(2)}
      </div>
      <ReportTable
        rows={rows}
        totalLabel="Net VAT due"
        totalCents={data.totals.net_due_cents}
      />
    </>
  )
}

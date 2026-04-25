import type { ReportType } from '@/types/reports'

const _OPTIONS: { id: ReportType; label: string }[] = [
  { id: 'trial_balance',     label: 'Trial Balance' },
  { id: 'balance_sheet',     label: 'Balance Sheet' },
  { id: 'income_statement',  label: 'P&L (Income Statement)' },
  { id: 'cashflow',          label: 'Cashflow' },
  { id: 'budget_vs_actuals', label: 'Budget vs Actuals' },
  { id: 'vat_return',        label: 'VAT Return' },
]

export function ReportTypeSelect({
  value,
  onChange,
}: {
  value: ReportType
  onChange: (next: ReportType) => void
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-zinc-600 font-medium">Report</span>
      <select
        className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm
                   focus:border-zinc-900 focus:outline-none"
        value={value}
        onChange={(e) => onChange(e.target.value as ReportType)}
      >
        {_OPTIONS.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

import type { ReportType } from '@/types/reports'

export type PeriodFilter =
  | { kind: 'as_of'; as_of: string }
  | { kind: 'range'; from: string; to: string }
  | { kind: 'period'; period: string }     // YYYY-MM

const _RANGE_TYPES: ReportType[] = ['income_statement', 'cashflow']
const _PERIOD_TYPES: ReportType[] = ['budget_vs_actuals', 'vat_return']

export function pickerKindFor(t: ReportType): PeriodFilter['kind'] {
  if (_RANGE_TYPES.includes(t)) return 'range'
  if (_PERIOD_TYPES.includes(t)) return 'period'
  return 'as_of'
}

export function defaultFilter(t: ReportType): PeriodFilter {
  // Today's date in ISO; defaults span enough history that demo data is
  // visible at first paint (the seed runs 2025-04 → 2026-04).
  const today = new Date().toISOString().slice(0, 10)
  const oneYearAgo = (() => {
    const d = new Date(today)
    d.setFullYear(d.getFullYear() - 1)
    return d.toISOString().slice(0, 10)
  })()
  switch (pickerKindFor(t)) {
    case 'range':
      return { kind: 'range', from: oneYearAgo, to: today }
    case 'period':
      return { kind: 'period', period: today.slice(0, 7) }
    case 'as_of':
      return { kind: 'as_of', as_of: today }
  }
}

export function PeriodPicker({
  value,
  onChange,
}: {
  value: PeriodFilter
  onChange: (next: PeriodFilter) => void
}) {
  if (value.kind === 'as_of') {
    return (
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-zinc-600 font-medium">As of</span>
        <input
          type="date"
          className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm"
          value={value.as_of}
          onChange={(e) =>
            onChange({ kind: 'as_of', as_of: e.target.value })
          }
        />
      </label>
    )
  }
  if (value.kind === 'range') {
    return (
      <div className="flex gap-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 font-medium">From</span>
          <input
            type="date"
            className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm"
            value={value.from}
            onChange={(e) =>
              onChange({ kind: 'range', from: e.target.value, to: value.to })
            }
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 font-medium">To</span>
          <input
            type="date"
            className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm"
            value={value.to}
            onChange={(e) =>
              onChange({ kind: 'range', from: value.from, to: e.target.value })
            }
          />
        </label>
      </div>
    )
  }
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-zinc-600 font-medium">Period (YYYY-MM)</span>
      <input
        type="month"
        className="bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm"
        value={value.period}
        onChange={(e) => onChange({ kind: 'period', period: e.target.value })}
      />
    </label>
  )
}

import { centsToEuros } from './formatters'

export interface ReportRow {
  label: string
  sublabel?: string
  cents: number
  group?: string  // optional section header
}

/**
 * Generic two-column report table. Rows can be grouped by `group`; lines
 * with the same `group` value are rendered under a single header row.
 */
export function ReportTable({
  rows,
  totalLabel,
  totalCents,
}: {
  rows: ReportRow[]
  totalLabel?: string
  totalCents?: number
}) {
  // Group rows preserving insertion order.
  const groups: { name: string | undefined; rows: ReportRow[] }[] = []
  for (const r of rows) {
    const last = groups[groups.length - 1]
    if (last && last.name === r.group) {
      last.rows.push(r)
    } else {
      groups.push({ name: r.group, rows: [r] })
    }
  }

  return (
    <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
      <table className="w-full text-sm">
        <tbody>
          {groups.map((g, gi) => (
            <ReportGroup key={`g-${gi}`} group={g} />
          ))}
          {totalLabel !== undefined && totalCents !== undefined && (
            <tr className="border-t-2 border-zinc-300 bg-zinc-50">
              <td className="p-3 font-semibold text-zinc-800">{totalLabel}</td>
              <td className="p-3 text-right tabular-nums font-semibold">
                {centsToEuros(totalCents)}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function ReportGroup({
  group,
}: {
  group: { name: string | undefined; rows: ReportRow[] }
}) {
  return (
    <>
      {group.name && (
        <tr className="bg-zinc-100 text-zinc-700">
          <td colSpan={2} className="px-3 py-2 font-medium uppercase text-xs tracking-wide">
            {group.name}
          </td>
        </tr>
      )}
      {group.rows.map((r, i) => (
        <tr key={`${group.name ?? '_'}-${i}`} className="border-t border-zinc-100">
          <td className="p-3">
            <div className="text-zinc-800">{r.label}</div>
            {r.sublabel && (
              <div className="text-xs text-zinc-500">{r.sublabel}</div>
            )}
          </td>
          <td className="p-3 text-right tabular-nums">
            {centsToEuros(r.cents)}
          </td>
        </tr>
      ))}
    </>
  )
}

import { useEffect } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { useLedger, useDashboard } from '@/store/dashboard'
import { api } from '@/api'
import { centsToEuros, shortDate } from './formatters'

export function Ledger({ onRowClick }: { onRowClick: (entryId: number) => void }) {
  const ledger = useLedger()
  const hydrate = useDashboard((s) => s.hydrate)

  useEffect(() => {
    api
      .listJournalEntries({ limit: 50 })
      .then((r) =>
        hydrate({
          ledger: r.items,
          envelopes: Object.values(useDashboard.getState().envelopes),
        }),
      )
      .catch((err) => console.error('[Ledger] hydrate', err))
  }, [hydrate])

  return (
    <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-zinc-50 text-zinc-600">
          <tr>
            <th className="text-left p-3 font-medium">Date</th>
            <th className="text-left p-3 font-medium">Source</th>
            <th className="text-left p-3 font-medium">Status</th>
            <th className="text-right p-3 font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          <AnimatePresence initial={false}>
            {ledger.slice(0, 50).map((e) => (
              <motion.tr
                key={e.id}
                layout
                initial={
                  e._new
                    ? { opacity: 0, y: -8, backgroundColor: 'rgb(236 253 245)' }
                    : false
                }
                animate={{ opacity: 1, y: 0, backgroundColor: 'rgb(255 255 255)' }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25 }}
                className="border-t border-zinc-100 cursor-pointer hover:bg-zinc-50"
                onClick={() => onRowClick(e.id)}
              >
                <td className="p-3 text-zinc-700">{shortDate(e.entry_date)}</td>
                <td className="p-3 text-zinc-600">{e.source_pipeline}</td>
                <td className="p-3">
                  <StatusPill status={e.status} />
                </td>
                <td className="p-3 text-right tabular-nums font-medium">
                  {centsToEuros(e.total_cents)}
                </td>
              </motion.tr>
            ))}
          </AnimatePresence>
        </tbody>
      </table>
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const cls =
    {
      posted: 'bg-emerald-100 text-emerald-700',
      review: 'bg-amber-100 text-amber-700',
      reversed: 'bg-zinc-200 text-zinc-700',
      draft: 'bg-zinc-100 text-zinc-600',
    }[status] ?? 'bg-zinc-100 text-zinc-600'
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}
    >
      {status}
    </span>
  )
}

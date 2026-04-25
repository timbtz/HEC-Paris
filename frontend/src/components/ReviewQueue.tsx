import { useEffect, useState } from 'react'
import { useDashboard } from '@/store/dashboard'
import { api } from '@/api'
import type { JournalEntryListItem } from '@/types'
import { centsToEuros, shortDate } from './formatters'

export function ReviewQueue() {
  const [items, setItems] = useState<JournalEntryListItem[]>([])
  const reviewIdsSize = useDashboard((s) => s.reviewIds.size)

  const refresh = () =>
    api
      .listJournalEntries({ status: 'review', limit: 50 })
      .then((r) => setItems(r.items))
      .catch((err) => console.error('[ReviewQueue]', err))

  useEffect(() => {
    refresh()
  }, [reviewIdsSize])

  const approve = async (id: number) => {
    try {
      await api.approveEntry(id, 1) // demo: Tim approves
      await refresh()
    } catch (err) {
      alert(`Approve failed: ${(err as Error).message}`)
    }
  }

  return (
    <div className="p-6">
      <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-50 text-zinc-600">
            <tr>
              <th className="text-left p-3 font-medium">Date</th>
              <th className="text-left p-3 font-medium">Source</th>
              <th className="text-right p-3 font-medium">Amount</th>
              <th className="text-right p-3 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id} className="border-t border-zinc-100">
                <td className="p-3">{shortDate(e.entry_date)}</td>
                <td className="p-3 text-zinc-600">{e.source_pipeline}</td>
                <td className="p-3 text-right tabular-nums font-medium">
                  {centsToEuros(e.total_cents)}
                </td>
                <td className="p-3 text-right">
                  <button
                    onClick={() => approve(e.id)}
                    className="bg-emerald-600 text-white px-3 py-1 rounded text-xs font-medium hover:bg-emerald-700"
                  >
                    Approve
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td
                  colSpan={4}
                  className="p-6 text-center text-zinc-500"
                >
                  No items in review.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

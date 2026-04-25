import { useEffect } from 'react'
import { useEnvelopes, useDashboard } from '@/store/dashboard'
import { api } from '@/api'
import { EnvelopeRing } from './EnvelopeRing'

// Hardcoded 3 employees for demo (matches audit/0002_seed_employees.py).
const EMPLOYEES = [
  { id: 1, name: 'Tim' },
  { id: 2, name: 'Marie' },
  { id: 3, name: 'Paul' },
]
const PERIOD = new Date().toISOString().slice(0, 7) // 'YYYY-MM'

export function EnvelopeRings() {
  const envelopes = useEnvelopes()
  const hydrate = useDashboard((s) => s.hydrate)

  useEffect(() => {
    Promise.all(
      EMPLOYEES.map((e) =>
        api.listEnvelopes({ employee_id: e.id, period: PERIOD }),
      ),
    )
      .then((responses) => {
        const all = responses.flatMap((r) => r.items)
        hydrate({ ledger: useDashboard.getState().ledger, envelopes: all })
      })
      .catch((err) => console.error('[EnvelopeRings] hydrate', err))
  }, [hydrate])

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 p-6">
      {EMPLOYEES.map((emp) => {
        const empRings = envelopes.filter(
          (e) =>
            e.scope_kind === 'employee' &&
            e.scope_id === emp.id &&
            e.period === PERIOD,
        )
        return (
          <div
            key={emp.id}
            className="rounded-xl border border-zinc-200 p-4 bg-white"
          >
            <div className="text-lg font-semibold text-zinc-900 mb-3">
              {emp.name}
            </div>
            <div className="grid grid-cols-5 gap-2">
              {empRings.map((env) => (
                <EnvelopeRing
                  key={env.id}
                  used={env.used_cents}
                  cap={env.cap_cents}
                  category={env.category}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

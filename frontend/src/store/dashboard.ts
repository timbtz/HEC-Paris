import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import type {
  JournalEntryListItem,
  EnvelopeRow,
  DashboardEvent,
} from '@/types'

type LedgerRow = JournalEntryListItem & { _new?: boolean }

const envelopeKey = (e: {
  scope_kind: string
  scope_id: number | null
  category: string
  period: string
}) => `${e.scope_kind}|${e.scope_id ?? 'null'}|${e.category}|${e.period}`

interface DashState {
  ledger: LedgerRow[]                    // newest first, capped at 200
  envelopes: Record<string, EnvelopeRow>
  reviewIds: Set<number>                 // ids of entries pending review
  connected: boolean
  hydrate: (p: {
    ledger: JournalEntryListItem[]
    envelopes: EnvelopeRow[]
  }) => void
  apply: (ev: DashboardEvent) => void
  setConnected: (b: boolean) => void
}

export const useDashboard = create<DashState>()((set, get) => ({
  ledger: [],
  envelopes: {},
  reviewIds: new Set(),
  connected: false,

  hydrate: ({ ledger, envelopes }) =>
    set({
      ledger,
      envelopes: Object.fromEntries(envelopes.map((e) => [envelopeKey(e), e])),
    }),

  setConnected: (connected) => set({ connected }),

  apply: (ev) => {
    switch (ev.event_type) {
      case 'ledger.entry_posted': {
        // Two shapes: nested `data: {...}` from gl_poster, or flat from approve_entry.
        const isNested = 'data' in ev
        const entryId = isNested ? ev.data.entry_id : ev.entry_id
        const s = get()

        if (s.ledger.some((r) => r.id === entryId)) {
          // Already in list (possibly in review) — promote status to 'posted'.
          set({
            ledger: s.ledger.map((r) =>
              r.id === entryId ? { ...r, status: 'posted' as const, _new: true } : r,
            ),
            reviewIds: new Set([...s.reviewIds].filter((id) => id !== entryId)),
          })
          return
        }

        if (!isNested) {
          // Flat shape from approve — REST refresh on tab switch will fix it.
          return
        }

        const stub: LedgerRow = {
          id: ev.data.entry_id,
          basis: ev.data.basis,
          entry_date: ev.data.entry_date,
          description: null,
          status: 'posted',
          source_pipeline: '?',
          source_run_id: ev.data.run_id,
          accrual_link_id: null,
          reversal_of_id: null,
          created_at: ev.ts,
          total_cents: ev.data.total_cents,
          line_count: ev.data.lines,
          _new: true,
        }
        set({ ledger: [stub, ...s.ledger].slice(0, 200) })
        return
      }

      case 'envelope.decremented': {
        const e = ev.data
        const fakeKey = envelopeKey({
          scope_kind: e.employee_id != null ? 'employee' : 'company',
          scope_id: e.employee_id,
          category: e.category,
          period: e.period,
        })
        const existing = get().envelopes[fakeKey]
        const merged: EnvelopeRow = {
          ...(existing ?? {
            id: e.envelope_id,
            scope_kind: e.employee_id != null ? 'employee' : 'company',
            scope_id: e.employee_id,
            category: e.category,
            period: e.period,
            cap_cents: e.cap_cents,
            soft_threshold_pct: e.soft_threshold_pct,
            used_cents: 0,
            allocation_count: 0,
          }),
          used_cents: e.used_cents,
          cap_cents: e.cap_cents,
          soft_threshold_pct: e.soft_threshold_pct,
        }
        set({ envelopes: { ...get().envelopes, [fakeKey]: merged } })
        return
      }

      case 'envelope.skipped':
      case 'envelope.no_envelope':
        // Toast in UI; no state change.
        console.info('[envelope]', ev.event_type, ev.data)
        return

      case 'review.enqueued':
        if (ev.data.entry_id != null) {
          const s = get()
          const next = new Set(s.reviewIds)
          next.add(ev.data.entry_id)
          set({ reviewIds: next })
        }
        return
    }
  },
}))

export const useLedger = () => useDashboard((s) => s.ledger)
export const useEnvelopes = () =>
  useDashboard(useShallow((s) => Object.values(s.envelopes)))
export const useConnected = () => useDashboard((s) => s.connected)

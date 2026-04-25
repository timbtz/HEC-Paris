import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { api } from '@/api'
import type { TraceResponse } from '@/types'
import { centsToEuros } from './formatters'

export function TraceDrawer({
  entryId,
  onClose,
}: {
  entryId: number | null
  onClose: () => void
}) {
  const [data, setData] = useState<TraceResponse | null>(null)
  useEffect(() => {
    if (entryId == null) {
      setData(null)
      return
    }
    api
      .getEntryTrace(entryId)
      .then(setData)
      .catch((err) => console.error('[TraceDrawer]', err))
  }, [entryId])

  return (
    <AnimatePresence>
      {entryId != null && (
        <motion.div
          className="fixed inset-y-0 right-0 w-[480px] bg-white border-l border-zinc-200 shadow-2xl z-40 overflow-y-auto"
          initial={{ x: 480 }}
          animate={{ x: 0 }}
          exit={{ x: 480 }}
          transition={{ type: 'tween', duration: 0.2 }}
        >
          <div className="p-4 flex items-center justify-between border-b border-zinc-200">
            <div className="font-semibold">Entry #{entryId}</div>
            <button
              onClick={onClose}
              className="text-zinc-500 hover:text-zinc-800"
              aria-label="Close trace drawer"
            >
              ×
            </button>
          </div>
          {data ? (
            <TraceContent data={data} />
          ) : (
            <div className="p-4 text-zinc-500">Loading…</div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function TraceContent({ data }: { data: TraceResponse }) {
  return (
    <div className="p-4 space-y-4 text-sm">
      <Section title="Lines">
        <table className="w-full">
          <tbody>
            {data.lines.map((l) => (
              <tr key={l.id} className="border-b border-zinc-100">
                <td className="py-1 font-mono text-xs">{l.account_code}</td>
                <td className="py-1 text-right tabular-nums">
                  {l.debit_cents > 0 ? centsToEuros(l.debit_cents) : ''}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {l.credit_cents > 0 ? centsToEuros(l.credit_cents) : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Decision traces">
        {data.traces.map((t) => (
          <div
            key={t.id}
            className="border-l-2 border-zinc-200 pl-3 mb-2"
          >
            <div className="text-xs text-zinc-500">
              {t.source}
              {t.confidence != null
                ? ` · conf ${(t.confidence * 100).toFixed(0)}%`
                : ''}
            </div>
            <div className="text-zinc-700">
              {t.rule_id ?? t.parent_event_id ?? '—'}
            </div>
          </div>
        ))}
      </Section>

      <Section title="Agent decisions">
        {data.agent_decisions.map((d) => (
          <div
            key={d.id}
            className="text-xs border-l-2 border-zinc-200 pl-3 mb-2"
          >
            <div className="font-mono">{d.node_id}</div>
            <div className="text-zinc-500">
              {d.runner}/{d.model ?? '?'} · {d.latency_ms ?? '?'}ms
              {d.confidence != null
                ? ` · conf ${(d.confidence * 100).toFixed(0)}%`
                : ''}
            </div>
          </div>
        ))}
      </Section>

      <Section title="Cost">
        {data.agent_costs.map((c) => (
          <div key={c.decision_id} className="text-xs">
            {c.provider}/{c.model} · in {c.input_tokens} / out {c.output_tokens}{' '}
            · ${(c.cost_micro_usd / 1_000_000).toFixed(6)}
          </div>
        ))}
      </Section>
    </div>
  )
}

function Section({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div>
      <div className="font-semibold text-zinc-800 mb-2">{title}</div>
      {children}
    </div>
  )
}

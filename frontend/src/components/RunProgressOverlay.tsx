import { useEffect } from 'react'
import { useRunProgress } from '@/store/runProgress'
import { useSSE } from '@/hooks/useSSE'
import type { RunEvent } from '@/types'

export function RunProgressOverlay() {
  const activeRunId = useRunProgress((s) => s.activeRunId)
  const apply = useRunProgress((s) => s.apply)
  const status = useRunProgress((s) => s.pipelineStatus)
  const reset = useRunProgress((s) => s.reset)
  const nodes = useRunProgress((s) => s.nodes)

  // Subscribe only when there is an active run; useSSE is a no-op for empty url.
  const url = activeRunId != null ? `/runs/${activeRunId}/stream` : ''
  useSSE<RunEvent>(url, apply)

  // Auto-close 4s after terminal.
  useEffect(() => {
    if (status === 'completed' || status === 'failed') {
      const t = setTimeout(reset, 4000)
      return () => clearTimeout(t)
    }
  }, [status, reset])

  if (activeRunId == null) return null

  const nodeIds = Object.keys(nodes)
  const statusCls =
    status === 'completed'
      ? 'bg-emerald-100 text-emerald-700'
      : status === 'failed'
      ? 'bg-rose-100 text-rose-700'
      : 'bg-blue-100 text-blue-700'

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl p-6 w-[480px] shadow-xl">
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold">Processing run #{activeRunId}</div>
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${statusCls}`}
          >
            {status ?? 'running'}
          </span>
        </div>
        <ul className="space-y-1 text-sm">
          {nodeIds.length === 0 && (
            <li className="text-zinc-500">Waiting for first event…</li>
          )}
          {nodeIds.map((id) => {
            const n = nodes[id]
            const dotCls =
              n.status === 'completed'
                ? 'bg-emerald-500'
                : n.status === 'failed'
                ? 'bg-rose-500'
                : n.status === 'skipped'
                ? 'bg-zinc-300'
                : n.status === 'running'
                ? 'bg-blue-500'
                : 'bg-zinc-400'
            return (
              <li key={id} className="flex items-center gap-2">
                <span
                  className={`inline-block w-2.5 h-2.5 rounded-full ${dotCls}`}
                />
                <span className="font-mono text-xs">{id}</span>
                <span className="text-zinc-500 text-xs">
                  {n.elapsed_ms != null ? `${n.elapsed_ms}ms` : ''}
                  {n.error ? ` ${n.error}` : ''}
                </span>
              </li>
            )
          })}
        </ul>
        <button
          onClick={reset}
          className="mt-4 text-sm text-zinc-500 hover:text-zinc-700"
        >
          Close
        </button>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { useDashboard } from '@/store/dashboard'

export function InfraTab() {
  const [healthz, setHealthz] = useState<{ status: string } | null>(null)
  const connected = useDashboard((s) => s.connected)
  useEffect(() => {
    fetch('/healthz')
      .then((r) => r.json())
      .then(setHealthz)
      .catch(() => setHealthz(null))
  }, [])
  return (
    <div className="p-6 space-y-4">
      <div className="rounded-xl border border-zinc-200 bg-white p-4">
        <div className="font-semibold mb-2">Backend</div>
        <div className="text-sm text-zinc-700 space-y-1">
          <div>healthz: {healthz?.status ?? '—'}</div>
          <div className="flex items-center gap-2">
            dashboard SSE:
            <span
              className={`inline-block w-2.5 h-2.5 rounded-full ${
                connected ? 'bg-emerald-500' : 'bg-rose-500'
              }`}
            />
            <span>{connected ? 'connected' : 'disconnected'}</span>
          </div>
        </div>
      </div>
      <div className="rounded-xl border border-zinc-200 bg-white p-4">
        <div className="font-semibold mb-2">Storage</div>
        <div className="text-sm text-zinc-600">
          Three SQLite databases: <code>accounting.db</code>,{' '}
          <code>orchestration.db</code>, <code>audit.db</code>.
        </div>
      </div>
      <div className="rounded-xl border border-zinc-200 bg-white p-4">
        <div className="font-semibold mb-2">Recent runs</div>
        <div className="text-sm text-zinc-500">
          (Listing endpoint not yet wired — open a per-run trace from the
          Dashboard tab.)
        </div>
      </div>
    </div>
  )
}

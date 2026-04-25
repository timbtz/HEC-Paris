import { useState } from 'react'
import { Tabs } from '@/components/Tabs'
import type { TabId } from '@/components/Tabs'
import { Ledger } from '@/components/Ledger'
import { EnvelopeRings } from '@/components/EnvelopeRings'
import { UploadZone } from '@/components/UploadZone'
import { TraceDrawer } from '@/components/TraceDrawer'
import { ReviewQueue } from '@/components/ReviewQueue'
import { ReportsTab } from '@/components/ReportsTab'
import { InfraTab } from '@/components/InfraTab'
import { useSSE } from '@/hooks/useSSE'
import { useDashboard } from '@/store/dashboard'
import type { DashboardEvent } from '@/types'

export default function App() {
  const [tab, setTab] = useState<TabId>('dashboard')
  const [traceEntry, setTraceEntry] = useState<number | null>(null)
  const apply = useDashboard((s) => s.apply)
  const setConnected = useDashboard((s) => s.setConnected)

  // Single global subscription to /dashboard/stream
  useSSE<DashboardEvent>('/dashboard/stream', apply, setConnected)

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900">
      <header className="bg-white border-b border-zinc-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="font-bold text-xl">Agnes</div>
          <div className="text-sm text-zinc-500">
            YAML-driven DAG executor · live demo
          </div>
        </div>
      </header>
      <Tabs value={tab} onChange={setTab} />
      {tab === 'dashboard' && (
        <div className="space-y-6">
          <EnvelopeRings />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 px-6 pb-6">
            <Ledger onRowClick={setTraceEntry} />
            <UploadZone />
          </div>
        </div>
      )}
      {tab === 'review' && <ReviewQueue />}
      {tab === 'reports' && <ReportsTab />}
      {tab === 'infra' && <InfraTab />}
      <TraceDrawer entryId={traceEntry} onClose={() => setTraceEntry(null)} />
    </div>
  )
}

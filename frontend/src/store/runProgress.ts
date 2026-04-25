import { create } from 'zustand'
import type { RunEvent } from '@/types'

export type NodeStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'failed'

interface NodeState {
  status: NodeStatus
  elapsed_ms?: number
  error?: string
}

interface RunProgressState {
  activeRunId: number | null
  nodes: Record<string, NodeState>
  pipelineStatus: 'running' | 'completed' | 'failed' | null
  setActiveRun: (id: number | null) => void
  apply: (ev: RunEvent) => void
  reset: () => void
}

export const useRunProgress = create<RunProgressState>()((set, get) => ({
  activeRunId: null,
  nodes: {},
  pipelineStatus: null,

  setActiveRun: (id) =>
    set({
      activeRunId: id,
      nodes: {},
      pipelineStatus: id ? 'running' : null,
    }),

  apply: (ev) => {
    switch (ev.event_type) {
      case 'pipeline_started':
        set({ pipelineStatus: 'running' })
        return
      case 'pipeline_completed':
        set({ pipelineStatus: 'completed' })
        return
      case 'pipeline_failed':
        set({ pipelineStatus: 'failed' })
        return
      case 'node_started':
        if (ev.node_id) {
          set({ nodes: { ...get().nodes, [ev.node_id]: { status: 'running' } } })
        }
        return
      case 'node_completed':
        if (ev.node_id) {
          const elapsed = (ev.data as { elapsed_ms?: number })?.elapsed_ms
          set({
            nodes: {
              ...get().nodes,
              [ev.node_id]: { status: 'completed', elapsed_ms: elapsed },
            },
          })
        }
        return
      case 'node_skipped':
        if (ev.node_id) {
          set({ nodes: { ...get().nodes, [ev.node_id]: { status: 'skipped' } } })
        }
        return
      case 'node_failed':
        if (ev.node_id) {
          const error = String((ev.data as { error?: unknown })?.error ?? '')
          set({
            nodes: { ...get().nodes, [ev.node_id]: { status: 'failed', error } },
          })
        }
        return
      case 'cache_hit':
        return
    }
  },

  reset: () => set({ activeRunId: null, nodes: {}, pipelineStatus: null }),
}))

/**
 * useDagRun — fuses the static pipeline topology (`/pipelines/{name}/dag`)
 * with the live SSE stream (`/runs/{id}/stream`) into a single shape the
 * DagViewer + NodeTraceDrawer can paint without doing any math.
 *
 * PRD-AutonomousCFO §7.4: status colors come from `pipeline_events`,
 * agent reasoning fields come from the new `agent.decision` event (see
 * `backend/orchestration/executor.py::_dispatch_agent`). Wiki citations
 * are surfaced as-is — an empty array until the wiki agent ships.
 */
import { useEffect, useMemo, useReducer } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { useSSE } from "@/hooks/useSSE";

export type NodeKind = "tool" | "agent" | "condition";
export type NodeStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "cached";
export type PipelineStatus = "pending" | "running" | "completed" | "failed";

export interface DagNodeMeta {
  id: string;
  kind: NodeKind;
  ref: string;
  runner: string | null;
  depends_on: string[];
  when: string | null;
  cacheable: boolean;
  layer_index: number;
}

export interface DagPipelineDoc {
  name: string;
  version: number;
  kind: "event" | "manual";
  trigger: string | null;
  nodes: DagNodeMeta[];
  layers: DagNodeMeta[][];
}

export interface WikiCitation {
  page_id: number | null;
  revision_id: number | null;
  revision_number: number | null;
  path: string | null;
  title: string | null;
}

export interface AgentDecisionPayload {
  decision_id: number;
  model: string;
  runner: string;
  provider: string;
  prompt_hash: string;
  finish_reason: string | null;
  confidence: number | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_micro_usd: number;
  wiki_citations: WikiCitation[];
}

export interface NodeState {
  meta: DagNodeMeta;
  status: NodeStatus;
  elapsed_ms: number | null;
  error: string | null;
  output: unknown | null;
  decision: AgentDecisionPayload | null;
}

export interface DagRunState {
  layers: DagNodeMeta[][];
  nodes: Record<string, NodeState>;
  totalCostMicroUsd: number;
  totalElapsedMs: number;
  pipelineStatus: PipelineStatus;
  pipelineError: string | null;
  sseStatus: "connecting" | "connected" | "reconnecting" | "offline";
}

// --- backend SSE envelope ----------------------------------------------------
// `runs.py::stream_run` emits `{run_id, event_type, node_id, data, ts}` —
// the per-event payload lives in `data`.
interface SseEnvelope {
  run_id: number;
  event_type: string;
  node_id: string | null;
  data: Record<string, unknown> | null;
  ts: string;
}

// --- reducer -----------------------------------------------------------------
type Action =
  | { type: "topology"; doc: DagPipelineDoc }
  | { type: "sse"; event: SseEnvelope }
  | { type: "sse_status"; status: DagRunState["sseStatus"] }
  | { type: "reset" };

const EMPTY: DagRunState = {
  layers: [],
  nodes: {},
  totalCostMicroUsd: 0,
  totalElapsedMs: 0,
  pipelineStatus: "pending",
  pipelineError: null,
  sseStatus: "connecting",
};

function buildInitialNodes(doc: DagPipelineDoc): Record<string, NodeState> {
  const out: Record<string, NodeState> = {};
  for (const n of doc.nodes) {
    out[n.id] = {
      meta: n,
      status: "pending",
      elapsed_ms: null,
      error: null,
      output: null,
      decision: null,
    };
  }
  return out;
}

function reduce(state: DagRunState, action: Action): DagRunState {
  switch (action.type) {
    case "reset":
      return { ...EMPTY };
    case "sse_status":
      return { ...state, sseStatus: action.status };
    case "topology": {
      return {
        ...state,
        layers: action.doc.layers,
        nodes: buildInitialNodes(action.doc),
      };
    }
    case "sse": {
      const { event_type, node_id, data } = action.event;
      // Pipeline-level events
      if (event_type === "pipeline_started") {
        return { ...state, pipelineStatus: "running", pipelineError: null };
      }
      if (event_type === "pipeline_completed") {
        return { ...state, pipelineStatus: "completed" };
      }
      if (event_type === "pipeline_failed") {
        return {
          ...state,
          pipelineStatus: "failed",
          pipelineError: (data?.error as string | undefined) ?? "failed",
        };
      }

      // Node-level events
      if (!node_id || !state.nodes[node_id]) return state;
      const prev = state.nodes[node_id];
      let next = prev;

      if (event_type === "node_started") {
        next = { ...prev, status: "running" };
      } else if (event_type === "node_completed") {
        const cacheHit = Boolean(data?.cache_hit);
        next = {
          ...prev,
          status: cacheHit ? "cached" : "completed",
          elapsed_ms: (data?.elapsed_ms as number | undefined) ?? null,
          output: data?.node_output ?? null,
        };
      } else if (event_type === "node_skipped") {
        next = {
          ...prev,
          status: "skipped",
          elapsed_ms: (data?.elapsed_ms as number | undefined) ?? null,
        };
      } else if (event_type === "node_failed") {
        next = {
          ...prev,
          status: "failed",
          error: (data?.error as string | undefined) ?? "failed",
        };
      } else if (event_type === "cache_hit") {
        next = { ...prev, status: "cached" };
      } else if (event_type === "agent.decision") {
        next = {
          ...prev,
          decision: data as unknown as AgentDecisionPayload,
        };
      } else {
        return state;
      }

      const nodes = { ...state.nodes, [node_id]: next };

      // Recompute aggregates lazily.
      let totalCost = 0;
      let totalElapsed = 0;
      for (const n of Object.values(nodes)) {
        if (n.decision) totalCost += n.decision.cost_micro_usd ?? 0;
        if (n.elapsed_ms) totalElapsed += n.elapsed_ms;
      }
      return {
        ...state,
        nodes,
        totalCostMicroUsd: totalCost,
        totalElapsedMs: totalElapsed,
      };
    }
  }
}

// --- hook --------------------------------------------------------------------
export interface UseDagRunArgs {
  runId: number | null;
  pipelineName: string | null;
}

export interface UseDagRunResult extends DagRunState {
  topologyLoading: boolean;
  topologyError: Error | null;
}

interface HistoricalRunDoc {
  run: { id: number; pipeline_name: string; status: string };
  events: Array<{
    id: number;
    run_id: number;
    event_type: string;
    node_id: string | null;
    data: string | Record<string, unknown> | null;
    elapsed_ms: number | null;
    created_at: string;
  }>;
  agent_decisions: Array<Record<string, unknown>>;
}

export function useDagRun({ runId, pipelineName }: UseDagRunArgs): UseDagRunResult {
  const [state, dispatch] = useReducer(reduce, EMPTY);

  // Fetch the static topology once per (pipelineName).
  const topologyQuery = useQuery({
    queryKey: ["pipeline-dag", pipelineName],
    enabled: Boolean(pipelineName),
    staleTime: 60_000,
    queryFn: async () => {
      const res = await apiFetch<DagPipelineDoc>(`/pipelines/${pipelineName}/dag`);
      return res.data;
    },
  });

  // Reset + apply topology on every doc change.
  useEffect(() => {
    if (!topologyQuery.data) {
      dispatch({ type: "reset" });
      return;
    }
    dispatch({ type: "reset" });
    dispatch({ type: "topology", doc: topologyQuery.data });
  }, [topologyQuery.data]);

  // Hydrate from the persisted event log once the topology is in place.
  // This handles the "run completed before the page loaded" case — the
  // SSE stream alone would emit nothing for an already-finished run.
  // We replay every persisted event through the same reducer so the UI
  // ends up in the exact same state as if it had watched the run live.
  // Live SSE events that arrive after the hydrate are merged on top.
  const historyQuery = useQuery({
    queryKey: ["run-history", runId],
    enabled: runId !== null && Boolean(topologyQuery.data),
    staleTime: 5_000,
    queryFn: async () => {
      const res = await apiFetch<HistoricalRunDoc>(`/runs/${runId}`);
      return res.data;
    },
  });

  useEffect(() => {
    if (!historyQuery.data) return;
    const evs = historyQuery.data.events ?? [];
    for (const e of evs) {
      let data: Record<string, unknown> | null = null;
      if (typeof e.data === "string") {
        try {
          data = JSON.parse(e.data) as Record<string, unknown>;
        } catch {
          data = null;
        }
      } else if (e.data && typeof e.data === "object") {
        data = e.data as Record<string, unknown>;
      }
      dispatch({
        type: "sse",
        event: {
          run_id: e.run_id,
          event_type: e.event_type,
          node_id: e.node_id ?? null,
          data,
          ts: e.created_at,
        },
      });
    }
  }, [historyQuery.data]);

  // SSE: subscribe to the per-run stream. If the run is already finished
  // (history shows pipeline_completed/failed), skip the subscription —
  // the backend stream would just close immediately.
  const runFinished =
    historyQuery.data?.run?.status === "completed" ||
    historyQuery.data?.run?.status === "failed";

  const { status: sseStatus } = useSSE({
    url: runId !== null ? `/runs/${runId}/stream` : "",
    enabled:
      runId !== null && Boolean(topologyQuery.data) && !runFinished,
    onEvent: (raw) => {
      // The backend per-run stream uses an envelope shape; the SseEvent
      // union in lib/types.ts is for the (flat) dashboard stream, so we
      // accept either by reading the envelope first.
      const env = raw as unknown as Partial<SseEnvelope>;
      if (typeof env.event_type !== "string") return;
      dispatch({
        type: "sse",
        event: {
          run_id: env.run_id ?? runId ?? 0,
          event_type: env.event_type,
          node_id: env.node_id ?? null,
          data: (env.data as Record<string, unknown> | undefined) ?? null,
          ts: env.ts ?? "",
        },
      });
    },
  });

  useEffect(() => {
    dispatch({ type: "sse_status", status: sseStatus });
  }, [sseStatus]);

  // Memoized return so reactflow doesn't re-layout on identity churn.
  return useMemo(
    () => ({
      ...state,
      sseStatus,
      topologyLoading: topologyQuery.isLoading,
      topologyError: (topologyQuery.error as Error | null) ?? null,
    }),
    [state, sseStatus, topologyQuery.isLoading, topologyQuery.error],
  );
}

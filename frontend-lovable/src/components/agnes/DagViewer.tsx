/**
 * DagViewer — live reactflow rendering of a pipeline run.
 *
 * Layout source of truth: backend `/pipelines/{name}/dag` returns Kahn-layer
 * topology with `layer_index` per node. We honour that — no client-side
 * dagre / elkjs — so the viewer is deterministic and matches the executor.
 *
 * PRD-AutonomousCFO §7.4. Status colors map directly from `NodeState.status`.
 */
import { useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { Cog, Brain, GitBranch, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMicroUsd } from "@/lib/format";
import type {
  DagNodeMeta,
  NodeState,
  NodeStatus,
} from "@/hooks/useDagRun";
import { NodeTraceDrawer } from "./NodeTraceDrawer";

const HORIZONTAL_GAP = 220; // px between nodes in a layer
const VERTICAL_GAP = 140;   // px between layers
const NODE_WIDTH = 200;
const NODE_HEIGHT = 84;

const STATUS_STYLE: Record<NodeStatus, { fill: string; ring: string; label: string }> = {
  pending:   { fill: "bg-card",                ring: "ring-border",          label: "pending"   },
  running:   { fill: "bg-blue-500/15",         ring: "ring-blue-500/60",     label: "running"   },
  completed: { fill: "bg-emerald-500/15",      ring: "ring-emerald-500/60",  label: "completed" },
  failed:    { fill: "bg-red-500/15",          ring: "ring-red-500/60",      label: "failed"    },
  skipped:   { fill: "bg-zinc-400/15",         ring: "ring-zinc-400/40",     label: "skipped"   },
  cached:    { fill: "bg-violet-500/15",       ring: "ring-violet-500/60",   label: "cached"    },
};

function KindIcon({ kind }: { kind: DagNodeMeta["kind"] }) {
  const cls = "h-3.5 w-3.5";
  if (kind === "agent") return <Brain className={cls} />;
  if (kind === "condition") return <GitBranch className={cls} />;
  return <Cog className={cls} />;
}

function StatusGlyph({ status }: { status: NodeStatus }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
  if (status === "completed" || status === "cached")
    return <CheckCircle2 className="h-3.5 w-3.5" />;
  if (status === "failed") return <XCircle className="h-3.5 w-3.5" />;
  return null;
}

interface DagNodeData {
  state: NodeState;
}

function DagFlowNode({ data }: NodeProps<DagNodeData>) {
  const { state } = data;
  const style = STATUS_STYLE[state.status];
  const cost = state.decision?.cost_micro_usd;
  // Click handling lives on ReactFlow's `onNodeClick` (see DagViewer below);
  // we intentionally don't put a button/onClick here because reactflow's
  // internal pointer handlers race with custom-node clicks.
  return (
    <div
      className={cn(
        "group flex w-full flex-col gap-1 rounded-lg border border-border px-3 py-2 text-left cursor-pointer",
        "ring-1 ring-inset transition-colors hover:bg-accent/40",
        style.fill,
        style.ring,
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
    >
      <Handle type="target" position={Position.Top} className="!bg-muted-foreground/40" />
      <div className="flex items-center justify-between gap-2 text-meta uppercase tracking-wide text-muted-foreground">
        <span className="flex items-center gap-1">
          <KindIcon kind={state.meta.kind} />
          {state.meta.kind}
        </span>
        <span className="flex items-center gap-1">
          <StatusGlyph status={state.status} />
          {style.label}
        </span>
      </div>
      <div className="truncate text-sec font-medium">{state.meta.id}</div>
      <div className="flex items-center justify-between gap-2 font-mono text-meta text-muted-foreground">
        <span className="truncate" title={state.meta.ref}>{state.meta.ref}</span>
        {cost !== undefined && cost > 0 ? (
          <span className="shrink-0 rounded-sm bg-foreground/5 px-1.5 py-0.5 tabular-nums">
            {formatMicroUsd(cost)}
          </span>
        ) : null}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground/40" />
    </div>
  );
}

const NODE_TYPES = { dag: DagFlowNode };

export interface DagViewerProps {
  layers: DagNodeMeta[][];
  nodes: Record<string, NodeState>;
  className?: string;
}

export function DagViewer({ layers, nodes, className }: DagViewerProps) {
  const [openNodeId, setOpenNodeId] = useState<string | null>(null);

  const flowNodes: Node<DagNodeData>[] = useMemo(() => {
    const out: Node<DagNodeData>[] = [];
    for (let li = 0; li < layers.length; li += 1) {
      const layer = layers[li];
      const layerWidth = layer.length * NODE_WIDTH + (layer.length - 1) * (HORIZONTAL_GAP - NODE_WIDTH);
      const startX = -layerWidth / 2;
      for (let i = 0; i < layer.length; i += 1) {
        const meta = layer[i];
        const state = nodes[meta.id];
        if (!state) continue;
        out.push({
          id: meta.id,
          type: "dag",
          position: { x: startX + i * HORIZONTAL_GAP, y: li * VERTICAL_GAP },
          data: { state },
          draggable: false,
          selectable: true,
        });
      }
    }
    return out;
  }, [layers, nodes]);

  const flowEdges: Edge[] = useMemo(() => {
    const edges: Edge[] = [];
    for (const layer of layers) {
      for (const n of layer) {
        for (const dep of n.depends_on) {
          edges.push({
            id: `${dep}->${n.id}`,
            source: dep,
            target: n.id,
            type: "smoothstep",
            animated: nodes[n.id]?.status === "running",
            style: { stroke: "hsl(var(--border))", strokeWidth: 1.5 },
          });
        }
      }
    }
    return edges;
  }, [layers, nodes]);

  const openNode = openNodeId ? nodes[openNodeId] ?? null : null;

  return (
    <div className={cn("relative h-full w-full", className)}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        onNodeClick={(_, n) => setOpenNodeId(n.id)}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      <NodeTraceDrawer
        open={openNode !== null}
        node={openNode}
        onClose={() => setOpenNodeId(null)}
      />
    </div>
  );
}

/**
 * RunsPage — paginated list of pipeline runs with a "View DAG" link.
 *
 * The Today dashboard shows the latest pulse; this page is the entry point
 * for the live DagViewer (PRD-AutonomousCFO §7.4).
 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, Eye, Play, Zap } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { fetchRecentRuns } from "@/lib/endpoints";
import { MicroUsd, RelTime } from "@/components/agnes/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useToast } from "@/hooks/use-toast";

interface PipelineSummary {
  name: string;
  version: number;
  kind: "event" | "manual";
  trigger: string | null;
  node_count: number;
}

interface SwanScenarioNext {
  id: string;
  swan_event_id: string;
  amount_cents: number;
  currency: string;
  side: string;
  counterparty_label: string | null;
  execution_date: string;
  type: string;
}

interface SwanScenario {
  key: string;
  title: string;
  description: string;
  next: SwanScenarioNext | null;
  remaining: number;
  total: number;
}

function fmtAmount(cents: number, currency: string): string {
  return (cents / 100).toLocaleString(undefined, {
    style: "currency",
    currency: currency || "EUR",
  });
}

export default function RunsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [triggering, setTriggering] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);

  const runsQuery = useQuery({
    queryKey: ["runs", "list", 50],
    queryFn: async () => (await fetchRecentRuns(50)).data,
    staleTime: 5_000,
  });

  const pipelinesQuery = useQuery({
    queryKey: ["pipelines", "catalog"],
    queryFn: async () =>
      (await apiFetch<{ items: PipelineSummary[] }>(`/pipelines`)).data,
    staleTime: 60_000,
  });

  const demoPipelines = (pipelinesQuery.data?.items ?? []).filter(
    (p) => p.name === "noop_demo" || p.name === "period_close",
  );

  const scenariosQuery = useQuery({
    queryKey: ["demo", "swan", "scenarios"],
    queryFn: async () =>
      (await apiFetch<{ scenarios: SwanScenario[] }>(`/demo/swan/scenarios`)).data,
    staleTime: 2_000,
  });

  async function simulateSwanEvent(txId?: string) {
    setSimulating(true);
    try {
      const res = await apiFetch<{
        status: string;
        run_ids: number[];
        event_type: string;
        amount_cents: number;
        currency: string;
        side: string;
        label: string | null;
      }>(`/demo/swan/simulate`, {
        method: "POST",
        body: txId ? { tx_id: txId } : {},
      });
      const { status, run_ids, amount_cents, currency, side, label } = res.data;
      const amount = fmtAmount(amount_cents, currency);
      if (status === "duplicate") {
        toast({
          title: "Already fired",
          description: "That seeded event was already replayed. Pick another.",
        });
      } else {
        toast({
          title: `Swan event fired — ${run_ids.length} run(s)`,
          description: `${side} ${amount} · ${label ?? "no label"}`,
        });
      }
      queryClient.invalidateQueries({ queryKey: ["runs", "list"] });
      queryClient.invalidateQueries({ queryKey: ["demo", "swan", "scenarios"] });
      if (run_ids.length > 0) {
        navigate(`/runs/${run_ids[0]}/dag?pipeline=transaction_booked`);
      }
    } catch (err) {
      toast({
        title: "Simulate failed",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setSimulating(false);
    }
  }

  async function triggerDemo(name: string) {
    setTriggering(name);
    try {
      // Demo payload defaults: period_close needs an explicit period and
      // the cash basis (the demo seed posts cash-basis entries; the
      // production accrual default sees zero rows). Other pipelines just
      // get an empty payload.
      const trigger_payload: Record<string, unknown> =
        name === "period_close"
          ? { period_code: "2025-Q4", basis: "cash" }
          : {};
      const res = await apiFetch<{ run_id: number; stream_url: string }>(
        `/pipelines/run/${name}`,
        { method: "POST", body: { trigger_payload } },
      );
      const runId = res.data.run_id;
      toast({
        title: `Run #${runId} started`,
        description: `Pipeline ${name} — opening live DAG.`,
      });
      // Refresh list in background.
      queryClient.invalidateQueries({ queryKey: ["runs", "list"] });
      navigate(`/runs/${runId}/dag?pipeline=${encodeURIComponent(name)}`);
    } catch (err) {
      toast({
        title: "Failed to start run",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setTriggering(null);
    }
  }

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Runs</h1>
          <p className="text-sec text-muted-foreground">
            Every pipeline execution. Click a row to open the live DAG.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex">
            <Button
              size="sm"
              variant="default"
              disabled={simulating}
              onClick={() => simulateSwanEvent()}
              className="rounded-r-none border-r border-primary-foreground/20"
            >
              <Zap className="mr-1.5 h-3.5 w-3.5" />
              {simulating ? "Firing…" : "Simulate Swan event"}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant="default"
                  disabled={simulating || !scenariosQuery.data}
                  className="rounded-l-none px-2"
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-80">
                <DropdownMenuLabel>Pick a scenario</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {(scenariosQuery.data?.scenarios ?? []).map((s) => {
                  const exhausted = s.next === null;
                  return (
                    <DropdownMenuItem
                      key={s.key}
                      disabled={exhausted}
                      onSelect={() => {
                        if (s.next) simulateSwanEvent(s.next.id);
                      }}
                      className="flex flex-col items-start gap-0.5 py-2"
                    >
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="font-medium">{s.title}</span>
                        {s.next ? (
                          <span className="font-mono text-xs tabular-nums">
                            {fmtAmount(s.next.amount_cents, s.next.currency)}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">exhausted</span>
                        )}
                      </div>
                      <span className="text-meta text-muted-foreground">
                        {s.description}
                        {s.next ? ` · ${s.remaining} left` : ""}
                      </span>
                    </DropdownMenuItem>
                  );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => simulateSwanEvent()}
                  className="text-meta text-muted-foreground"
                >
                  Or fire next chronological event
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {demoPipelines.map((p) => (
            <Button
              key={p.name}
              size="sm"
              variant="default"
              disabled={triggering === p.name}
              onClick={() => triggerDemo(p.name)}
            >
              <Play className="mr-1.5 h-3.5 w-3.5" />
              {triggering === p.name ? "Starting…" : `Run ${p.name}`}
            </Button>
          ))}
        </div>
      </header>

      {runsQuery.isLoading ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sec text-muted-foreground">
          Loading…
        </div>
      ) : null}

      {runsQuery.data ? (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <table className="w-full text-sec">
            <thead className="bg-muted/40 text-meta uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">Pipeline</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-right">Cost</th>
                <th className="px-3 py-2 text-right">Elapsed</th>
                <th className="px-3 py-2 text-right">Reviews</th>
                <th className="px-3 py-2 text-left">Started</th>
                <th className="px-3 py-2 text-right" />
              </tr>
            </thead>
            <tbody>
              {runsQuery.data.items.map((r) => (
                <tr key={r.id} className="border-t border-border hover:bg-accent/30">
                  <td className="px-3 py-2 font-mono">#{r.id}</td>
                  <td className="px-3 py-2 font-mono">{r.pipeline_name}</td>
                  <td className="px-3 py-2">
                    <Badge variant="outline" className="font-mono">
                      {r.status}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <MicroUsd value={r.agent_cost_micro_usd} />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.elapsed_ms !== null ? `${r.elapsed_ms} ms` : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {r.review_count > 0 ? (
                      <Link
                        to="/review"
                        className="inline-flex items-center gap-1 rounded-sm bg-warning/10 px-1.5 py-0.5 text-meta font-medium text-warning hover:bg-warning/15"
                        title="Open review queue"
                      >
                        <Eye className="h-3 w-3" />
                        {r.review_count}
                      </Link>
                    ) : (
                      <span className="text-meta text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <RelTime iso={r.started_at} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button asChild size="sm" variant="ghost">
                      <Link to={`/runs/${r.id}/dag?pipeline=${encodeURIComponent(r.pipeline_name)}`}>
                        DAG <ArrowRight className="ml-1 h-3.5 w-3.5" />
                      </Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

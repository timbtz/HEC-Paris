/**
 * RunDagPage — live DAG visualizer for a single pipeline run.
 *
 * URL: `/runs/:id/dag` — `pipeline` query string supplies the topology
 * lookup (a fetch of `/runs/:id` would also work but costs an extra round
 * trip and the runs list already has the pipeline name).
 *
 * PRD-AutonomousCFO §7.4 — every pipeline run renders as a live graph;
 * clicking a node opens the trace drawer with reasoning + cost + citations.
 */
import { useEffect, useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FileText } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useDagRun } from "@/hooks/useDagRun";
import { DagViewer } from "@/components/fingent/DagViewer";
import { LiveDot, MicroUsd } from "@/components/fingent/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface RunDoc {
  run: { id: number; pipeline_name: string; status: string };
}

interface PeriodReportSummary {
  id: number;
  period_code: string;
  report_type: string;
  status: string;
  source_run_id: number | null;
}

const REPORT_PIPELINES = new Set([
  "period_close",
  "vat_return",
  "year_end_close",
]);

export default function RunDagPage() {
  const params = useParams();
  const [search] = useSearchParams();
  const runId = params.id ? Number(params.id) : null;
  const pipelineHint = search.get("pipeline");

  // If pipeline name wasn't passed via query, hydrate from /runs/:id.
  const runQuery = useQuery({
    queryKey: ["run", runId],
    enabled: runId !== null && !pipelineHint,
    queryFn: async () => {
      const res = await apiFetch<RunDoc>(`/runs/${runId}`);
      return res.data;
    },
  });

  const pipelineName = pipelineHint ?? runQuery.data?.run.pipeline_name ?? null;

  const dag = useDagRun({ runId, pipelineName });

  // Period_close / vat_return / year_end_close runs produce a downloadable
  // markdown artifact via /period_reports/{id}/artifact. Find the report tied
  // to this run so we can surface a "View output" button next to the DAG.
  const reportQuery = useQuery({
    queryKey: ["period-report-for-run", runId],
    enabled: runId !== null && pipelineName !== null && REPORT_PIPELINES.has(pipelineName),
    staleTime: 30_000,
    queryFn: async () => {
      const res = await apiFetch<{ items: PeriodReportSummary[] }>(
        `/period_reports?limit=200`,
      );
      return res.data.items.find((r) => r.source_run_id === runId) ?? null;
    },
  });
  const report = reportQuery.data ?? null;

  useEffect(() => {
    document.title = pipelineName
      ? `Run #${runId} · ${pipelineName} · Fingent`
      : `Run #${runId} · Fingent`;
  }, [runId, pipelineName]);

  const title = useMemo(() => pipelineName ?? "loading…", [pipelineName]);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      <header className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <Button asChild size="sm" variant="ghost">
            <Link to="/runs">
              <ArrowLeft className="mr-1 h-3.5 w-3.5" />
              Runs
            </Link>
          </Button>
          <div className="leading-tight">
            <div className="text-meta uppercase tracking-wide text-muted-foreground">Run #{runId}</div>
            <div className="font-mono text-sec">{title}</div>
          </div>
        </div>
        <div className="flex items-center gap-3 text-sec">
          {report ? (
            <Button asChild size="sm" variant="outline">
              <a href={`/period_reports/${report.id}/artifact?format=md`} target="_blank" rel="noreferrer">
                <FileText className="mr-1 h-3.5 w-3.5" />
                View output ({report.period_code})
              </a>
            </Button>
          ) : null}
          <Badge variant="outline" className="font-mono">
            {dag.pipelineStatus}
          </Badge>
          <span className="text-muted-foreground">cost</span>
          <MicroUsd value={dag.totalCostMicroUsd} />
          <span className="text-muted-foreground">·</span>
          <span className="tabular-nums">{dag.totalElapsedMs} ms</span>
          <span className="ml-2 flex items-center gap-1.5">
            <LiveDot status={dag.sseStatus} />
            <span className="text-meta uppercase text-muted-foreground">{dag.sseStatus}</span>
          </span>
        </div>
      </header>

      <div className="relative flex-1">
        {dag.topologyLoading ? (
          <div className="flex h-full items-center justify-center text-sec text-muted-foreground">
            Loading topology…
          </div>
        ) : dag.topologyError ? (
          <div className="flex h-full items-center justify-center text-sec text-destructive">
            Failed to load DAG: {dag.topologyError.message}
          </div>
        ) : (
          <DagViewer layers={dag.layers} nodes={dag.nodes} />
        )}
        {dag.pipelineError ? (
          <div className="absolute bottom-3 left-3 right-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sec text-destructive">
            {dag.pipelineError}
          </div>
        ) : null}
      </div>
    </div>
  );
}

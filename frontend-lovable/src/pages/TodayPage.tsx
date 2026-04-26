import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  Receipt,
  ListChecks,
  Workflow,
  Cpu,
  ArrowUpRight,
  CircleAlert,
} from "lucide-react";
import { useDashboard } from "@/store/dashboard";
import { useTraceDrawer } from "@/components/agnes/TraceDrawerContext";
import { ConfidenceBar, EmptyState, LiveDot, MicroUsd, MockedChip, Money, RelTime, StatusBadge } from "@/components/agnes/primitives";
import { fetchAiSpendToday, fetchRecentRuns } from "@/lib/endpoints";
import type { AiSpendToday, RunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ResponsiveContainer, AreaChart, Area } from "recharts";

export default function TodayPage() {
  const entries = useDashboard((s) => s.entries);
  const envelopes = useDashboard((s) => s.envelopes);
  const reviewIds = useDashboard((s) => s.reviewIds);
  const conn = useDashboard((s) => s.conn);
  const mocked = useDashboard((s) => s.mocked);
  const { open } = useTraceDrawer();
  const navigate = useNavigate();

  const recentEntries = entries.slice(0, 12);
  const reviewEntries = entries.filter((e) => e.status === "review").slice(0, 5);
  const burningEnvelopes = Object.values(envelopes)
    .map((e) => ({ ...e, pct: e.cap_cents > 0 ? (e.used_cents / e.cap_cents) * 100 : 0 }))
    .sort((a, b) => b.pct - a.pct)
    .slice(0, 6);

  const [spend, setSpend] = useState<AiSpendToday | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    fetchAiSpendToday().then((r) => setSpend(r.data));
    fetchRecentRuns(8).then((r) => setRuns(r.data.items));
  }, []);

  return (
    <div className="mx-auto max-w-[1400px] space-y-6 px-6 py-6">
      {/* Title row */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Today</h1>
          <p className="text-sec text-muted-foreground">
            What changed in your company since you last looked.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {mocked && <MockedChip />}
          <LiveDot status={conn} />
          <button className="ml-2 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sec font-medium text-primary-foreground hover:bg-primary/90">
            <Upload className="h-3.5 w-3.5" />
            Upload document
          </button>
        </div>
      </div>

      {/* Top row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Live ledger pulse */}
        <Card title="Live ledger pulse" subtitle={`${recentEntries.length} entries`} accent={<LiveDot status={conn} />}>
          {recentEntries.length === 0 ? (
            <EmptyState
              icon={Receipt}
              title="No journal entries yet"
              hint="Connect a Swan account or upload an invoice to get started."
            />
          ) : (
            <ul className="-mx-2">
              {recentEntries.map((e) => (
                <li
                  key={e.id}
                  onClick={() => open(e.id)}
                  className="row-hover flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5"
                >
                  <RelTime iso={e.created_at} className="w-12 shrink-0 text-meta" />
                  <span className="min-w-0 flex-1 truncate text-sec">{e.description}</span>
                  {e.employee_first_name && (
                    <span className="hidden rounded-sm bg-muted px-1.5 py-0.5 text-meta text-muted-foreground sm:inline">
                      {e.employee_first_name}
                    </span>
                  )}
                  <span className="shrink-0 text-sec font-medium tabular-nums">
                    <Money cents={e.total_cents} />
                  </span>
                  <StatusBadge status={e.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Envelopes burning */}
        <Card title="Envelopes burning" subtitle="Top 6 by % used">
          {burningEnvelopes.length === 0 ? (
            <EmptyState icon={CircleAlert} title="No envelopes" hint="Configure budget envelopes in Onboarding." />
          ) : (
            <div className="grid grid-cols-3 gap-3">
              {burningEnvelopes.map((env) => (
                <EnvelopeRing key={env.id} env={env} />
              ))}
            </div>
          )}
        </Card>

        {/* Review queue */}
        <Card
          title="Review queue"
          subtitle={`${reviewIds.size} pending`}
          action={
            <button onClick={() => navigate("/review")} className="text-meta text-muted-foreground hover:text-foreground">
              View all →
            </button>
          }
        >
          {reviewEntries.length === 0 ? (
            <EmptyState icon={ListChecks} title="Inbox zero" hint="No entries flagged for review." />
          ) : (
            <ul className="space-y-2">
              {reviewEntries.map((e) => (
                <li
                  key={e.id}
                  onClick={() => open(e.id)}
                  className="row-hover cursor-pointer rounded-md border border-border bg-card p-2.5"
                >
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sec font-medium">{e.description}</div>
                      <div className="mt-0.5 flex items-center gap-2 text-meta text-muted-foreground">
                        <RelTime iso={e.created_at} />
                        <span>·</span>
                        <span className="font-medium tabular-nums text-foreground">
                          <Money cents={e.total_cents} />
                        </span>
                      </div>
                    </div>
                    <ConfidenceBar value={e.confidence ?? 0} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Bottom row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* AI spend today */}
        <Card
          title="AI spend today"
          subtitle="Anthropic + OpenAI · last 14 days"
          action={
            <button onClick={() => navigate("/ai-spend")} className="text-meta text-muted-foreground hover:text-foreground">
              Drill in →
            </button>
          }
          accent={<MockedChip />}
        >
          {spend ? (
            <div>
              <div className="flex items-baseline gap-3">
                <div className="text-2xl font-semibold tabular-nums">
                  <MicroUsd value={spend.total_today_micro_usd} />
                </div>
                <div className="text-meta text-muted-foreground">today</div>
              </div>
              <div className="mt-3 h-16">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={spend.series_14d}>
                    <defs>
                      <linearGradient id="spendGrad" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.4} />
                        <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <Area
                      dataKey="cost_micro_usd"
                      type="monotone"
                      stroke="hsl(var(--primary))"
                      strokeWidth={1.5}
                      fill="url(#spendGrad)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <SkeletonBlock />
          )}
        </Card>

        {/* Recent runs */}
        <Card
          title="Recent runs"
          subtitle="Last 8 pipeline executions"
          action={
            <button onClick={() => navigate("/runs")} className="text-meta text-muted-foreground hover:text-foreground">
              All runs →
            </button>
          }
          accent={<MockedChip />}
        >
          {runs.length === 0 ? (
            <SkeletonBlock />
          ) : (
            <ul className="-mx-2">
              {runs.map((r) => (
                <li
                  key={r.id}
                  onClick={() => navigate(`/runs/${r.id}`)}
                  className="row-hover flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-sec"
                >
                  <span className={cn("h-1.5 w-1.5 rounded-full", r.status === "success" ? "bg-primary" : r.status === "failed" ? "bg-destructive" : "bg-warning")} />
                  <span className="font-mono text-sec">{r.pipeline_name}</span>
                  <RelTime iso={r.started_at} className="text-meta" />
                  <span className="ml-auto tabular-nums text-meta text-muted-foreground">
                    {((r.elapsed_ms ?? 0) / 1000).toFixed(1)}s
                  </span>
                  <MicroUsd value={r.agent_cost_micro_usd} />
                  <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground" />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Card({
  title,
  subtitle,
  action,
  accent,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  accent?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="text-meta text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          {accent}
          {action}
        </div>
      </header>
      {children}
    </section>
  );
}

function EnvelopeRing({
  env,
}: {
  env: { id: number; pct: number; cap_cents: number; used_cents: number; category: string; soft_threshold_pct: number; employee_first_name?: string };
}) {
  const pct = Math.min(100, env.pct);
  const radius = 26;
  const circ = 2 * Math.PI * radius;
  const offset = circ * (1 - pct / 100);
  const color =
    env.pct >= 100 ? "hsl(var(--destructive))" : env.pct < env.soft_threshold_pct ? "hsl(var(--primary))" : "hsl(var(--warning))";
  const cat = env.category.split(".").pop();
  return (
    <div className="flex flex-col items-center rounded-md border border-border bg-card p-2.5">
      <div className="relative h-16 w-16">
        <svg viewBox="0 0 64 64" className="h-full w-full -rotate-90">
          <circle cx="32" cy="32" r={radius} fill="none" stroke="hsl(var(--muted))" strokeWidth="5" />
          <circle
            cx="32"
            cy="32"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="5"
            strokeDasharray={circ}
            strokeDashoffset={offset}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center text-meta font-medium tabular-nums">
          {Math.round(env.pct)}%
        </div>
      </div>
      <div className="mt-1.5 text-meta font-medium">{env.employee_first_name ?? "Team"}</div>
      <div className="text-meta text-muted-foreground">{cat}</div>
    </div>
  );
}

function SkeletonBlock() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-3 w-full animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

import { useMemo, useState } from "react";
import { Cpu, TrendingUp, Trophy } from "lucide-react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MicroUsd } from "@/components/fingent/primitives";
import { cn } from "@/lib/utils";
import { fetchAiCosts, fetchGamificationLeaderboard } from "@/lib/endpoints";
import type { AiCostsGroupKey, AiCostsResponse, AiCostsRow } from "@/lib/types";

// "By API key" stays hidden — agent_costs has no API-key field today.
type Pivot = "employee" | "model" | "pipeline";

const PIVOT_TO_GROUP_BY: Record<Pivot, AiCostsGroupKey[]> = {
  employee: ["employee", "provider"],
  model: ["model"],
  pipeline: ["pipeline"],
};

function pivotLabel(p: Pivot) {
  return p;
}

function rowKey(row: Record<string, string | number | null>, keys: AiCostsGroupKey[]): string {
  return keys.map((k) => String(row[k] ?? "")).join("|");
}

function rowLabel(row: Record<string, string | number | null>, keys: AiCostsGroupKey[]): string {
  return keys
    .map((k) => row[k])
    .filter((v) => v !== null && v !== undefined && String(v).length > 0)
    .map((v) => String(v))
    .join(" / ") || "(unattributed)";
}

function firstOfMonth(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-01`;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function AiSpendPage() {
  const [pivot, setPivot] = useState<Pivot>("employee");
  const [start] = useState<string>(firstOfMonth());
  const [end] = useState<string>(todayIso());

  const groupBy = PIVOT_TO_GROUP_BY[pivot];
  const { data, isLoading, isError } = useQuery({
    queryKey: ["ai-costs", start, end, groupBy.join(",")],
    queryFn: () => fetchAiCosts({ start, end, groupBy }).then((r) => r.data),
  });

  const resp: AiCostsResponse | undefined = data;
  const rows = resp?.rows ?? [];
  const total = resp?.totals.cost_micro_usd ?? 0;
  const totalCalls = resp?.totals.calls ?? 0;

  // Stacked bar: top 6 + Other.
  const segments = useMemo(() => {
    const top = rows.slice(0, 6);
    const other = rows.slice(6).reduce((a, b) => a + (b.cost_micro_usd as number), 0);
    return [
      ...top.map((r) => ({ label: rowLabel(r, groupBy), cost: r.cost_micro_usd as number })),
      ...(other > 0 ? [{ label: "Other", cost: other }] : []),
    ];
  }, [rows, groupBy]);

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 px-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI spend</h1>
          <p className="text-sec text-muted-foreground">Anthropic and OpenAI usage, attributed to the people and pipelines burning it.</p>
        </div>
      </div>

      <AdoptionStrip />

      {/* 14-day trend chart */}
      <DailyTrendChart />

      {/* Hero */}
      <section className="surface-hero p-8">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="text-meta uppercase tracking-[0.16em] text-muted-foreground">Total · this period</div>
            <div className="display mt-2 text-4xl prism-text"><MicroUsd value={total} /></div>
            <div className="mt-1 text-sec text-muted-foreground">
              {totalCalls.toLocaleString()} calls · {rows.length} {pivotLabel(pivot)}{rows.length === 1 ? "" : "s"}
              <span className="ml-2 font-mono text-meta">{start} → {end}</span>
            </div>
          </div>
        </div>

        {/* Stacked bar */}
        {total > 0 && (
          <div className="mt-6">
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
              {segments.map((s, i) => (
                <div
                  key={i}
                  title={`${s.label} · ${((s.cost / total) * 100).toFixed(1)}%`}
                  className="h-full"
                  style={{ width: `${(s.cost / total) * 100}%`, background: SEG_COLORS[i % SEG_COLORS.length] }}
                />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted-foreground">
              {segments.map((s, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: SEG_COLORS[i % SEG_COLORS.length] }} />
                  {s.label}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Pivot tabs */}
      <div className="surface-card p-1.5">
        <div className="flex gap-1">
          {(["employee", "model", "pipeline"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPivot(p)}
              className={cn(
                "flex-1 rounded-md px-3 py-2 text-sec transition-colors",
                pivot === p ? "bg-prism text-primary-foreground shadow-glow" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              By {pivotLabel(p)}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <section className="surface-card overflow-hidden">
        {isLoading && (
          <div className="px-4 py-12 text-center text-sec text-muted-foreground">
            Loading agent costs…
          </div>
        )}
        {isError && (
          <div className="px-4 py-12 text-center text-sec text-destructive">
            Failed to load /reports/ai-costs.
          </div>
        )}
        {!isLoading && !isError && rows.length === 0 && (
          <div className="px-4 py-12 text-center text-sec text-muted-foreground">
            No agent costs in this period yet — once a pipeline runs, this populates from agent_costs.
          </div>
        )}
        {!isLoading && !isError && rows.length > 0 && (
          <table className="w-full text-sec">
            <thead className="text-meta uppercase tracking-wide text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-4 py-2.5 text-left font-medium">{pivotLabel(pivot)}</th>
                <th className="px-4 py-2.5 text-right font-medium">Calls</th>
                <th className="px-4 py-2.5 text-right font-medium">Input tokens</th>
                <th className="px-4 py-2.5 text-right font-medium">Output tokens</th>
                <th className="px-4 py-2.5 text-right font-medium">Cost</th>
                <th className="px-4 py-2.5 text-right font-medium">Share</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const cost = r.cost_micro_usd as number;
                const share = total > 0 ? (cost / total) * 100 : 0;
                return (
                  <tr key={rowKey(r, groupBy)} className="row-hover border-b border-border/60 last:border-b-0">
                    <td className="px-4 py-2.5 font-medium">{rowLabel(r, groupBy)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{(r.calls as number).toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{(r.input_tokens as number).toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{(r.output_tokens as number).toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums"><MicroUsd value={cost} /></td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="ml-auto flex items-center justify-end gap-2">
                        <div className="h-1 w-16 overflow-hidden rounded-full bg-muted">
                          <div className="h-full bg-primary" style={{ width: `${share}%` }} />
                        </div>
                        <span className="w-10 text-right text-meta tabular-nums text-muted-foreground">{share.toFixed(1)}%</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

const SEG_COLORS = [
  "hsl(245 80% 62%)", "hsl(265 80% 65%)", "hsl(225 85% 60%)",
  "hsl(285 70% 65%)", "hsl(205 80% 60%)", "hsl(305 60% 60%)", "hsl(180 50% 50%)",
];

// silence unused
export { Cpu };

// 14-day cost trend — area chart over /reports/ai-costs?group_by=day.
// Uses USD on the Y axis (cost_micro_usd / 1e6) for legibility; tooltip
// shows the exact micro-USD figure. Empty state when no data.
function DailyTrendChart() {
  const end = todayIso();
  const start = (() => {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - 13);
    return d.toISOString().slice(0, 10);
  })();
  const { data, isLoading } = useQuery({
    queryKey: ["ai-costs-daily", start, end],
    queryFn: () => fetchAiCosts({ start, end, groupBy: ["day"] }).then((r) => r.data),
  });

  const series = useMemo(() => {
    const rows = (data?.rows ?? []) as AiCostsRow[];
    const byDay = new Map<string, { calls: number; cost: number }>();
    for (const r of rows) {
      const day = String(r.day ?? "");
      if (!day) continue;
      byDay.set(day, {
        calls: r.calls as number,
        cost: r.cost_micro_usd as number,
      });
    }
    const out: Array<{ day: string; label: string; usd: number; calls: number; cost: number }> = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date();
      d.setUTCDate(d.getUTCDate() - i);
      const iso = d.toISOString().slice(0, 10);
      const v = byDay.get(iso) ?? { calls: 0, cost: 0 };
      out.push({
        day: iso,
        label: iso.slice(5), // MM-DD
        usd: v.cost / 1_000_000,
        calls: v.calls,
        cost: v.cost,
      });
    }
    return out;
  }, [data]);

  const totalCost = series.reduce((a, b) => a + b.cost, 0);
  const totalCalls = series.reduce((a, b) => a + b.calls, 0);
  const peak = series.reduce((a, b) => (b.cost > a ? b.cost : a), 0);

  return (
    <section className="surface-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-primary" />
          <div>
            <div className="text-sec font-medium">14-day spend trend</div>
            <div className="text-meta text-muted-foreground">
              Daily AI cost over the last two weeks · {totalCalls.toLocaleString()} calls
              {" · "}
              <MicroUsd value={totalCost} /> total · peak day{" "}
              <MicroUsd value={peak} />
            </div>
          </div>
        </div>
      </div>
      {isLoading && (
        <div className="px-4 py-12 text-center text-sec text-muted-foreground">
          Loading trend…
        </div>
      )}
      {!isLoading && totalCost === 0 && (
        <div className="px-4 py-12 text-center text-sec text-muted-foreground">
          No agent costs in the last 14 days.
        </div>
      )}
      {!isLoading && totalCost > 0 && (
        <div className="h-48 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 6, right: 6, left: -12, bottom: 0 }}>
              <defs>
                <linearGradient id="usd-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="hsl(245 80% 62%)" stopOpacity={0.45} />
                  <stop offset="100%" stopColor="hsl(245 80% 62%)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
              <XAxis
                dataKey="label"
                axisLine={false}
                tickLine={false}
                tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                interval="preserveStartEnd"
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                tickFormatter={(v: number) =>
                  v >= 1 ? `$${v.toFixed(0)}` : `$${v.toFixed(2)}`
                }
                width={48}
              />
              <RTooltip
                contentStyle={{
                  background: "hsl(var(--popover))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelStyle={{ color: "hsl(var(--muted-foreground))" }}
                formatter={(_v, _n, ctx) => {
                  const p = ctx.payload as { cost: number; calls: number; day: string };
                  return [
                    <span key="t" className="font-mono">
                      <MicroUsd value={p.cost} /> · {p.calls} call{p.calls === 1 ? "" : "s"}
                    </span>,
                    p.day,
                  ];
                }}
              />
              <Area
                type="monotone"
                dataKey="usd"
                stroke="hsl(245 80% 65%)"
                strokeWidth={2}
                fill="url(#usd-fill)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

// Live adoption strip — the "extension at the token-consumption side."
// Pulls /gamification/leaderboard?period=month in real time so the cost
// pivot and the coin pivot sit on the same screen. When the backend is
// down, the apiFetch fallback ships an empty leaderboard and the strip
// stays visually consistent without crashing the page.
function AdoptionStrip() {
  const { data } = useQuery({
    queryKey: ["adoption-strip"],
    queryFn: () => fetchGamificationLeaderboard("month").then((r) => r.data),
    refetchInterval: 8000,
  });
  const top3 = (data?.items ?? []).slice(0, 3);
  const reward = data?.auto_coin_reward ?? 5;
  return (
    <Link
      to="/adoption"
      className="surface-card flex items-center justify-between gap-4 p-4 transition-colors hover:bg-muted/30"
    >
      <div className="flex items-center gap-3">
        <Trophy className="h-4 w-4 text-primary" />
        <div>
          <div className="text-sec font-medium">Adoption leaderboard · this month</div>
          <div className="text-meta text-muted-foreground">
            Every attributed agent call auto-credits {reward} coins.
            Self-declared AI use queues for manager approval.
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 text-sec">
        {top3.map((row, i) => (
          <div key={row.employee_id} className="flex items-center gap-1.5">
            <span className="font-mono text-meta text-muted-foreground">#{i + 1}</span>
            <span className="font-medium">{row.full_name ?? row.email}</span>
            <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-meta tabular-nums">
              {row.coins}
            </span>
          </div>
        ))}
        {top3.length === 0 && (
          <span className="text-meta text-muted-foreground">No coins yet.</span>
        )}
      </div>
    </Link>
  );
}

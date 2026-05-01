import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, ReceiptText } from "lucide-react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, BarChart, Bar, CartesianGrid } from "recharts";
import {
  CATEGORIES,
  CATEGORY_BY_KEY,
  SPEND_TX,
  categoryTotals,
  currentPeriod,
  monthlyTotals,
  txByEmployee,
} from "@/lib/dataset";
import { EMPLOYEES } from "@/lib/mocks";
import { useTraceDrawer } from "@/components/fingent/TraceDrawerContext";
import { ConfidenceBar, EmptyState, Money, RelTime, StatusBadge } from "@/components/fingent/primitives";
import { cn } from "@/lib/utils";

const CURRENT_USER_ID = 1; // Élise

export default function MePage({ asAdmin = false }: { asAdmin?: boolean }) {
  const params = useParams<{ id?: string }>();
  const navigate = useNavigate();

  const employeeId = asAdmin && params.id ? Number(params.id) : CURRENT_USER_ID;
  const employee = EMPLOYEES.find((e) => e.id === employeeId);

  if (!employee) {
    return (
      <div className="mx-auto max-w-[1280px] px-6 py-12">
        <EmptyState icon={ReceiptText} title="Employee not found" hint="This employee id isn't on the directory." />
      </div>
    );
  }

  return <SpendingView employeeId={employeeId} asAdmin={asAdmin} onBack={() => navigate("/budgets")} />;
}

function SpendingView({ employeeId, asAdmin, onBack }: { employeeId: number; asAdmin: boolean; onBack: () => void }) {
  const employee = EMPLOYEES.find((e) => e.id === employeeId)!;
  const { open } = useTraceDrawer();

  const period = currentPeriod();
  const txs = useMemo(() => txByEmployee(employeeId), [employeeId]);
  const thisPeriodTotals = useMemo(() => categoryTotals(employeeId, period), [employeeId, period]);
  const months = useMemo(() => monthlyTotals(employeeId, 6), [employeeId]);
  const recent = txs.slice(0, 12);

  const totalThisMonth = Object.values(thisPeriodTotals).reduce((a, b) => a + b.total_cents, 0);
  const totalCap = CATEGORIES.reduce((a, c) => a + (thisPeriodTotals[c.key].total_cents > 0 ? c.cap_cents : 0), 0);
  const lastMonthTotal = months[months.length - 2]?.total ?? 0;
  const delta = lastMonthTotal > 0 ? ((totalThisMonth - lastMonthTotal) / lastMonthTotal) * 100 : 0;

  // Build chart data: for each month, one row per category
  const chartData = months.map((m) => {
    const row: Record<string, number | string> = { period: monthLabel(m.period) };
    for (const c of CATEGORIES) row[c.short] = (m.totals[c.key] ?? 0) / 100;
    return row;
  });

  return (
    <div className="mx-auto max-w-[1280px] space-y-6 px-6 py-6">
      {/* Admin breadcrumb */}
      {asAdmin && (
        <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sec text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to budgets
        </button>
      )}

      {/* Hero card — totals + identity */}
      <section className="surface-hero relative overflow-hidden p-8">
        <div className="relative z-10 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-md bg-prism font-mono text-base font-semibold text-primary-foreground">
                {employee.first_name[0]}{employee.full_name.split(" ")[1]?.[0] ?? ""}
              </div>
              <div>
                <div className="text-meta uppercase tracking-[0.16em] text-muted-foreground">
                  {asAdmin ? "Employee · CFO inspector" : "Statement"}
                </div>
                <div className="text-lg font-semibold tracking-tight">{employee.full_name}</div>
                <div className="text-sec text-muted-foreground">{employee.department} · {monthLabel(period)}</div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-8">
            <Stat label="Spent this month" value={<Money cents={totalThisMonth} />} accent />
            <Stat label="Of caps" value={<Money cents={totalCap} />} muted />
            <Stat
              label="vs last month"
              value={
                <span className={cn(delta > 0 ? "text-warning" : "text-positive", "tabular-nums")}>
                  {delta > 0 ? "+" : ""}{delta.toFixed(1)}%
                </span>
              }
            />
          </div>
        </div>

        {/* Decorative prism shimmer */}
        <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-prism opacity-20 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 -left-16 h-72 w-72 rounded-full bg-primary-glow opacity-10 blur-3xl" />
      </section>

      {/* Category cards */}
      <section>
        <h2 className="mb-3 text-meta font-medium uppercase tracking-[0.16em] text-muted-foreground">
          Categories · {monthLabel(period)}
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {CATEGORIES.map((cat) => {
            const used = thisPeriodTotals[cat.key].total_cents;
            const count = thisPeriodTotals[cat.key].count;
            return <CategoryCard key={cat.key} cat={cat} used={used} count={count} />;
          })}
        </div>
      </section>

      {/* Charts */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Last 6 months" subtitle="Total spending across all categories">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={months.map((m) => ({ period: monthLabel(m.period), total: m.total / 100 }))}>
                <defs>
                  <linearGradient id="meTotal" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="period" stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `€${(v / 1000).toFixed(0)}k`} width={45} />
                <Tooltip content={<ChartTooltip />} />
                <Area type="monotone" dataKey="total" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#meTotal)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="By category" subtitle="Stacked monthly breakdown">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
                <XAxis dataKey="period" stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `€${(v / 1000).toFixed(0)}k`} width={45} />
                <Tooltip content={<ChartTooltip />} />
                {CATEGORIES.map((c, i) => (
                  <Bar key={c.key} dataKey={c.short} stackId="all" fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </section>

      {/* Recent transactions */}
      <section className="surface-card">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">Recent transactions</h2>
            <p className="text-meta text-muted-foreground">Last {recent.length} of {txs.length} this period</p>
          </div>
        </div>
        {recent.length === 0 ? (
          <div className="p-8">
            <EmptyState icon={ReceiptText} title="No transactions yet" hint="Charges will appear here as soon as the agents book them." />
          </div>
        ) : (
          <table className="w-full text-sec">
            <thead className="text-meta uppercase tracking-wide text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-4 py-2 text-left font-medium">When</th>
                <th className="px-4 py-2 text-left font-medium">Vendor</th>
                <th className="px-4 py-2 text-left font-medium">Category</th>
                <th className="px-4 py-2 text-right font-medium">Amount</th>
                <th className="px-4 py-2 text-left font-medium">Status</th>
                <th className="w-8 px-4" />
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => open(t.id)}
                  className="row-hover cursor-pointer border-b border-border last:border-b-0"
                  style={{ height: 40 }}
                >
                  <td className="px-4 text-muted-foreground"><RelTime iso={t.iso} /></td>
                  <td className="px-4">{t.vendor}</td>
                  <td className="px-4">
                    <span className="rounded-sm bg-muted px-1.5 py-0.5 text-meta text-muted-foreground">
                      {CATEGORY_BY_KEY[t.category_key]?.short ?? t.category_key}
                    </span>
                  </td>
                  <td className="px-4 text-right font-medium tabular-nums">
                    <Money cents={t.amount_cents} />
                  </td>
                  <td className="px-4"><StatusBadge status={t.status} /></td>
                  <td className="px-4 text-right">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, accent, muted }: { label: string; value: React.ReactNode; accent?: boolean; muted?: boolean }) {
  return (
    <div>
      <div className="text-meta uppercase tracking-[0.16em] text-muted-foreground">{label}</div>
      <div className={cn("display mt-2 text-3xl tabular-nums", accent && "prism-text", muted && "text-muted-foreground")}>
        {value}
      </div>
    </div>
  );
}

function CategoryCard({ cat, used, count }: { cat: typeof CATEGORIES[number]; used: number; count: number }) {
  const pct = cat.cap_cents > 0 ? Math.min(100, (used / cat.cap_cents) * 100) : 0;
  const exceeded = used > cat.cap_cents;
  const remaining = Math.max(0, cat.cap_cents - used);

  return (
    <div className="surface-card relative overflow-hidden p-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">{cat.short}</div>
          <div className="mt-0.5 text-sec font-medium">{cat.label}</div>
        </div>
        <div className="text-right text-meta tabular-nums text-muted-foreground">
          {count} {count === 1 ? "tx" : "txs"}
        </div>
      </div>

      <div className="mt-4">
        <div className="display text-2xl">
          <Money cents={used} />
        </div>
        <div className="mt-1 text-meta text-muted-foreground">
          of <span className="tabular-nums">€{(cat.cap_cents / 100).toFixed(0)}</span> cap
          {used > 0 && (
            <span className="mx-1.5">·</span>
          )}
          {used > 0 && (
            <span className={cn("tabular-nums", exceeded ? "text-warning" : "text-muted-foreground")}>
              {exceeded ? "exceeded" : <>€{(remaining / 100).toFixed(0)} left</>}
            </span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            exceeded ? "bg-warning" : pct > 80 ? "bg-primary-glow" : "bg-primary",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="surface-card p-4">
      <header className="mb-3">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="text-meta text-muted-foreground">{subtitle}</p>}
      </header>
      {children}
    </section>
  );
}

const CHART_COLORS = [
  "hsl(245 80% 62%)",
  "hsl(265 80% 65%)",
  "hsl(225 85% 60%)",
  "hsl(285 70% 65%)",
  "hsl(205 80% 60%)",
  "hsl(305 60% 60%)",
  "hsl(175 60% 50%)",
];

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="surface-elevated p-2.5 text-sec shadow-modal">
      <div className="mb-1 text-meta font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      {payload.map((p: any, i: number) => (
        <div key={i} className="flex items-center gap-2 tabular-nums">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-muted-foreground">{p.dataKey}</span>
          <span className="ml-auto font-medium">€{Number(p.value).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        </div>
      ))}
    </div>
  );
}

function monthLabel(period: string) {
  const [y, m] = period.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: "short" });
}

// Suppress unused import warnings
export { SPEND_TX };

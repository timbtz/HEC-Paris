import { useMemo, useState } from "react";
import { Calendar, Download, FileBarChart2, Sparkles, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CATEGORIES, CATEGORY_BY_KEY, SPEND_TX, currentPeriod, monthlyTotals } from "@/lib/dataset";
import { EMPLOYEES } from "@/lib/mocks";
import { Money, MockedChip, ConfidenceBar } from "@/components/fingent/primitives";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import {
  fetchAccountingPeriods,
  fetchJournalEntries,
  fetchTrialBalance,
  fetchBalanceSheet,
  fetchIncomeStatement,
  fetchVatReturn,
} from "@/lib/endpoints";
import type { AccountingPeriod } from "@/lib/types";
import { useToast } from "@/hooks/use-toast";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";

type ReportKey =
  | "trial_balance"
  | "balance_sheet"
  | "income_statement"
  | "cashflow"
  | "budget_vs_actuals"
  | "vat_return"
  | "period_close"
  | "year_end_close"
  | "cash_forecast"
  | "audit_pack";

interface ReportDef {
  key: ReportKey;
  label: string;
  group: "Statements" | "Operational" | "Agentic";
  // Maps a UI-selected report to a backend pipeline name. `null` = no live
  // pipeline (cash_forecast / audit_pack are demo-only views today).
  pipeline: string | null;
}

const REPORTS: ReportDef[] = [
  { key: "trial_balance", label: "Trial balance", group: "Statements", pipeline: null },
  { key: "balance_sheet", label: "Balance sheet", group: "Statements", pipeline: null },
  { key: "income_statement", label: "Income statement", group: "Statements", pipeline: null },
  { key: "cashflow", label: "Cashflow", group: "Statements", pipeline: null },
  { key: "budget_vs_actuals", label: "Budget vs actuals", group: "Operational", pipeline: null },
  { key: "vat_return", label: "VAT return", group: "Operational", pipeline: "vat_return" },
  { key: "period_close", label: "Period close", group: "Agentic", pipeline: "period_close" },
  { key: "year_end_close", label: "Year-end close", group: "Agentic", pipeline: "year_end_close" },
  { key: "cash_forecast", label: "Cash forecast", group: "Agentic", pipeline: null },
  { key: "audit_pack", label: "Audit pack", group: "Agentic", pipeline: null },
];

// Pipelines that take a period_code in their trigger payload.
const PERIOD_AWARE_PIPELINES = new Set<string>(["period_close", "vat_return", "year_end_close"]);

export default function ReportsPage() {
  const [selected, setSelected] = useState<ReportKey>("budget_vs_actuals");
  const def = REPORTS.find((r) => r.key === selected)!;
  const navigate = useNavigate();
  const { toast } = useToast();
  const [triggering, setTriggering] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickedPeriodCode, setPickedPeriodCode] = useState<string | null>(null);

  const periodsQuery = useQuery({
    queryKey: ["accounting_periods"],
    queryFn: async () => (await fetchAccountingPeriods()).data,
    staleTime: 30_000,
  });
  const periods: AccountingPeriod[] = periodsQuery.data ?? [];
  const defaultPeriodCode = useMemo(() => {
    const open = periods.find((p) => p.status !== "closed");
    return open?.code ?? periods[0]?.code ?? null;
  }, [periods]);

  async function fireTrigger(pipeline: string, periodCode: string | null) {
    setTriggering(true);
    try {
      const triggerPayload = periodCode ? { period_code: periodCode } : {};
      const res = await apiFetch<{ run_id: number; stream_url: string }>(
        `/pipelines/run/${pipeline}`,
        { method: "POST", body: { trigger_payload: triggerPayload } },
      );
      const runId = res.data.run_id;
      toast({
        title: `Run #${runId} started`,
        description: `Pipeline ${pipeline}${periodCode ? ` · ${periodCode}` : ""} — opening live DAG.`,
      });
      navigate(`/runs/${runId}/dag?pipeline=${encodeURIComponent(pipeline)}`);
    } catch (err) {
      toast({
        title: "Failed to start run",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setTriggering(false);
    }
  }

  async function runPipeline() {
    if (!def.pipeline) {
      toast({
        title: "No live pipeline",
        description: `${def.label} is rendered from cached data — no agentic pipeline behind it yet.`,
      });
      return;
    }
    if (PERIOD_AWARE_PIPELINES.has(def.pipeline)) {
      // Open picker; default to most-recent open period.
      setPickedPeriodCode(defaultPeriodCode);
      setPickerOpen(true);
      return;
    }
    await fireTrigger(def.pipeline, null);
  }

  async function confirmPickerAndTrigger() {
    if (!def.pipeline) return;
    setPickerOpen(false);
    await fireTrigger(def.pipeline, pickedPeriodCode);
  }

  async function exportCSV() {
    try {
      const { data } = await fetchJournalEntries({ limit: 200 });
      downloadCsv(
        "journal_entries.csv",
        ["id", "entry_date", "basis", "description", "status", "source_pipeline", "source_run_id", "total_cents", "line_count"],
        data.items.map((r) => [
          r.id,
          r.entry_date,
          r.basis,
          (r.description ?? "").replace(/"/g, '""'),
          r.status,
          r.source_pipeline ?? "",
          r.source_run_id ?? "",
          r.total_cents,
          r.line_count,
        ]),
      );
      toast({ title: "Exported", description: `${data.items.length} journal entries written.` });
    } catch (err) {
      toast({
        title: "Export failed",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    }
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 px-6 py-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="text-sec text-muted-foreground">
            Statutory, operational and agentic — every figure traces back to a journal line.
          </p>
        </div>
        <MockedChip />
      </div>

      {/* Selector */}
      <div className="surface-card p-2">
        <div className="flex flex-wrap items-center gap-1">
          {(["Statements", "Operational", "Agentic"] as const).map((group) => (
            <div key={group} className="flex items-center gap-1">
              <span className="px-2 text-meta font-medium uppercase tracking-[0.14em] text-muted-foreground">
                {group}
              </span>
              {REPORTS.filter((r) => r.group === group).map((r) => (
                <button
                  key={r.key}
                  onClick={() => setSelected(r.key)}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-sec transition-colors",
                    selected === r.key
                      ? "bg-prism text-primary-foreground shadow-glow"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {r.label}
                </button>
              ))}
              {group !== "Agentic" && <div className="mx-1 h-5 w-px bg-border" />}
            </div>
          ))}
        </div>
      </div>

      {/* Filters + actions row */}
      <div className="flex items-center justify-between rounded-md border border-border bg-card/60 px-3 py-2">
        <div className="flex items-center gap-2 text-sec">
          <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">Period</span>
          <span className="font-medium">{prettyPeriod(currentPeriod())}</span>
        </div>
        <div className="flex items-center gap-2">
          {def.pipeline && (
            <button
              type="button"
              disabled={triggering}
              onClick={runPipeline}
              className="inline-flex items-center gap-1.5 rounded-md bg-prism px-3 py-1.5 text-sec font-medium text-primary-foreground shadow-glow hover:opacity-90 disabled:opacity-50"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {triggering ? "Starting…" : "Run pipeline"}
            </button>
          )}
          <button
            type="button"
            onClick={exportCSV}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sec text-muted-foreground hover:bg-muted"
          >
            <Download className="h-3.5 w-3.5" />
            Export CSV
          </button>
        </div>
      </div>

      {/* Body */}
      {selected === "trial_balance" && <TrialBalance />}
      {selected === "balance_sheet" && <BalanceSheet />}
      {selected === "income_statement" && <IncomeStatement />}
      {selected === "cashflow" && <Cashflow />}
      {selected === "budget_vs_actuals" && <BudgetVsActuals />}
      {selected === "vat_return" && <VatReturn />}
      {selected === "period_close" && <PeriodClose />}
      {selected === "year_end_close" && <PeriodClose yearEnd />}
      {selected === "cash_forecast" && <CashForecast />}
      {selected === "audit_pack" && <AuditPack />}

      {/* Period picker — gates period_close / vat_return / year_end_close */}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Pick a period</DialogTitle>
            <DialogDescription>
              {def.pipeline ? (
                <>
                  Pipeline <span className="font-mono">{def.pipeline}</span> needs a{" "}
                  <span className="font-mono">period_code</span> in its trigger payload. Defaults
                  to the most recent open period.
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          {periodsQuery.isLoading ? (
            <div className="text-sec text-muted-foreground">Loading periods…</div>
          ) : periods.length === 0 ? (
            <div className="text-sec text-muted-foreground">
              No periods returned by the backend. Triggering with no period_code — backend will
              default to the latest non-closed period.
            </div>
          ) : (
            <Select
              value={pickedPeriodCode ?? undefined}
              onValueChange={(v) => setPickedPeriodCode(v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a period…" />
              </SelectTrigger>
              <SelectContent>
                {periods.map((p) => (
                  <SelectItem key={p.id} value={p.code}>
                    <span className="font-mono">{p.code}</span>
                    <span className="ml-2 text-meta text-muted-foreground">
                      {p.start_date} → {p.end_date} · {p.status}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setPickerOpen(false)}
              disabled={triggering}
            >
              Cancel
            </Button>
            <Button
              onClick={confirmPickerAndTrigger}
              disabled={triggering || (periods.length > 0 && !pickedPeriodCode)}
            >
              <Sparkles className="mr-1.5 h-3.5 w-3.5" />
              {triggering ? "Starting…" : "Run pipeline"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------- CSV helper ----------

function downloadCsv(filename: string, headers: string[], rows: (string | number | null | undefined)[][]) {
  const escape = (v: unknown) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const body = [headers.join(","), ...rows.map((r) => r.map(escape).join(","))].join("\n");
  const blob = new Blob([body], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- Helpers ----------

function prettyPeriod(p: string) {
  const [y, m] = p.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function ReportCard({ title, subtitle, children, totals }: { title: string; subtitle?: string; children: React.ReactNode; totals?: React.ReactNode }) {
  return (
    <section className="surface-card overflow-hidden">
      <header className="flex items-start justify-between border-b border-border p-5">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="mt-0.5 text-sec text-muted-foreground">{subtitle}</p>}
        </div>
        {totals && <div className="text-right">{totals}</div>}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

// ---------- Trial Balance ----------
function TrialBalance() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["trial_balance"],
    queryFn: () => fetchTrialBalance().then((r) => r.data),
  });
  if (isLoading) return <ReportCard title="Trial balance" subtitle="Loading…">{null}</ReportCard>;
  if (isError || !data) return <ReportCard title="Trial balance" subtitle="Failed to load /reports/trial_balance">{null}</ReportCard>;

  const lines = data.lines.filter(
    (l) => l.total_debit_cents !== 0 || l.total_credit_cents !== 0,
  );
  const totalDr = data.totals.total_debit_cents;
  const totalCr = data.totals.total_credit_cents;
  const balanced = data.totals.balanced;

  return (
    <ReportCard
      title="Trial balance"
      subtitle={`As of ${data.as_of} · ${data.basis} basis · ${data.currency}`}
      totals={<BalancedBadge balanced={balanced} dr={totalDr} cr={totalCr} />}
    >
      <table className="w-full text-sec">
        <thead className="text-meta uppercase tracking-wide text-muted-foreground">
          <tr className="border-b border-border">
            <th className="px-3 py-2 text-left font-medium">Code</th>
            <th className="px-3 py-2 text-left font-medium">Account</th>
            <th className="px-3 py-2 text-right font-medium">Debit</th>
            <th className="px-3 py-2 text-right font-medium">Credit</th>
            <th className="px-3 py-2 text-right font-medium">Balance</th>
          </tr>
        </thead>
        <tbody>
          {lines.length === 0 && (
            <tr><td colSpan={5} className="px-3 py-12 text-center text-muted-foreground">No posted journal entries yet.</td></tr>
          )}
          {lines.map((l) => (
            <tr key={l.code} className="row-hover border-b border-border/60 last:border-b-0">
              <td className="px-3 py-2 font-mono text-meta text-muted-foreground">{l.code}</td>
              <td className="px-3 py-2">{l.name}</td>
              <td className="px-3 py-2 text-right tabular-nums"><Money cents={l.total_debit_cents} mutedZero /></td>
              <td className="px-3 py-2 text-right tabular-nums"><Money cents={l.total_credit_cents} mutedZero /></td>
              <td className="px-3 py-2 text-right tabular-nums font-medium"><Money cents={l.balance_cents} /></td>
            </tr>
          ))}
        </tbody>
        <tfoot className="border-t-2 border-border">
          <tr>
            <td className="px-3 py-3" />
            <td className="px-3 py-3 font-medium">Totals</td>
            <td className="px-3 py-3 text-right font-semibold tabular-nums"><Money cents={totalDr} /></td>
            <td className="px-3 py-3 text-right font-semibold tabular-nums"><Money cents={totalCr} /></td>
            <td />
          </tr>
        </tfoot>
      </table>
    </ReportCard>
  );
}

function BalancedBadge({ balanced, dr, cr }: { balanced: boolean; dr: number; cr: number }) {
  return (
    <div className="space-y-1 text-right">
      <div className="display text-2xl"><Money cents={dr} /></div>
      <div className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-meta font-medium", balanced ? "bg-positive/10 text-positive" : "bg-warning/10 text-warning")}>
        {balanced ? "balanced ✓" : `off by ${(dr - cr) / 100} €`}
      </div>
    </div>
  );
}

// ---------- Balance Sheet ----------
function BalanceSheet() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["balance_sheet"],
    queryFn: () => fetchBalanceSheet().then((r) => r.data),
  });
  if (isLoading) return <ReportCard title="Balance sheet" subtitle="Loading…">{null}</ReportCard>;
  if (isError || !data) return <ReportCard title="Balance sheet" subtitle="Failed to load /reports/balance_sheet">{null}</ReportCard>;

  const provisional = !!data.provisional;
  return (
    <ReportCard
      title="Balance sheet"
      subtitle={`As of ${data.as_of} · ${data.basis} basis${provisional ? " · provisional (P&L not closed)" : ""}`}
      totals={<BalancedBadge balanced={data.totals.balanced} dr={data.totals.total_assets_cents} cr={data.totals.total_liabilities_equity_cents} />}
    >
      <div className="grid grid-cols-2 gap-6">
        <Section title="Assets">
          {data.sections.assets.length === 0 && (
            <div className="text-meta text-muted-foreground">No asset balances.</div>
          )}
          {data.sections.assets.map((a) => (
            <Row key={a.code} label={`${a.name} (${a.code})`} value={a.balance_cents} />
          ))}
          <Total label="Total assets" value={data.totals.total_assets_cents} />
        </Section>
        <Section title="Liabilities + Equity">
          {data.sections.liabilities.map((l) => (
            <Row key={l.code} label={`${l.name} (${l.code})`} value={l.balance_cents} muted />
          ))}
          {data.sections.equity.map((e) => (
            <Row key={e.code} label={`${e.name} (${e.code})`} value={e.balance_cents} />
          ))}
          {(data.sections.liabilities.length + data.sections.equity.length) === 0 && (
            <div className="text-meta text-muted-foreground">No liability or equity balances.</div>
          )}
          <Total label="Total liabilities + equity" value={data.totals.total_liabilities_equity_cents} />
        </Section>
      </div>
    </ReportCard>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <h3 className="mb-2 text-meta font-medium uppercase tracking-[0.14em] text-muted-foreground">{title}</h3>
      {children}
    </div>
  );
}
function Row({ label, value, muted }: { label: string; value: number; muted?: boolean }) {
  return (
    <div className="flex items-baseline justify-between border-b border-border/40 py-1.5 text-sec">
      <span className={cn(muted ? "text-muted-foreground" : "text-foreground")}>{label}</span>
      <span className="tabular-nums"><Money cents={value} /></span>
    </div>
  );
}
function Total({ label, value }: { label: string; value: number }) {
  return (
    <div className="mt-2 flex items-baseline justify-between border-t-2 border-border pt-2 text-sec font-semibold">
      <span>{label}</span>
      <span className="tabular-nums"><Money cents={value} /></span>
    </div>
  );
}

// ---------- Income Statement ----------
function IncomeStatement() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["income_statement"],
    queryFn: () => fetchIncomeStatement().then((r) => r.data),
  });
  if (isLoading) return <ReportCard title="Income statement" subtitle="Loading…">{null}</ReportCard>;
  if (isError || !data) return <ReportCard title="Income statement" subtitle="Failed to load /reports/income_statement">{null}</ReportCard>;

  const revenue = data.totals.revenue_cents;
  const totalExpenses = data.totals.expenses_cents;
  const netIncome = data.totals.net_income_cents;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <div className="xl:col-span-2">
        <ReportCard title="Income statement" subtitle={`${data.from} → ${data.to} · ${data.basis} basis`}>
          <div className="grid grid-cols-3 gap-6">
            <div>
              <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">Revenue</div>
              <div className="display mt-1 text-2xl text-positive"><Money cents={revenue} /></div>
            </div>
            <div>
              <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">Expenses</div>
              <div className="display mt-1 text-2xl"><Money cents={totalExpenses} /></div>
            </div>
            <div>
              <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">Net income</div>
              <div className={cn("display mt-1 text-2xl", netIncome >= 0 ? "prism-text" : "text-warning")}><Money cents={netIncome} signed /></div>
            </div>
          </div>
          <div className="mt-6 grid grid-cols-2 gap-6">
            <Section title="Revenue">
              {data.sections.revenue.length === 0 && <div className="text-meta text-muted-foreground">No revenue posted in this window.</div>}
              {data.sections.revenue.map((r) => (
                <Row key={r.code} label={`${r.name} (${r.code})`} value={r.amount_cents} />
              ))}
            </Section>
            <Section title="Expenses">
              {data.sections.expenses.length === 0 && <div className="text-meta text-muted-foreground">No expenses posted in this window.</div>}
              {data.sections.expenses.map((e) => (
                <Row key={e.code} label={`${e.name} (${e.code})`} value={e.amount_cents} muted />
              ))}
            </Section>
          </div>
        </ReportCard>
      </div>

      <ReportCard title="Expenses by code">
        <ul className="divide-y divide-border">
          {data.sections.expenses.length === 0 && <li className="py-3 text-meta text-muted-foreground">No expenses to break down.</li>}
          {data.sections.expenses.map((e) => {
            const pct = totalExpenses ? (e.amount_cents / totalExpenses) * 100 : 0;
            return (
              <li key={e.code} className="py-2.5">
                <div className="flex items-baseline justify-between text-sec">
                  <span>{e.name} <span className="font-mono text-meta text-muted-foreground">({e.code})</span></span>
                  <span className="tabular-nums font-medium"><Money cents={e.amount_cents} /></span>
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-prism" style={{ width: `${pct}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      </ReportCard>
    </div>
  );
}

// ---------- Cashflow ----------
function Cashflow() {
  const totalSpend = SPEND_TX.reduce((a, b) => a + b.amount_cents, 0);
  const opening = 142_500_000;
  const operating = 142_800_000 - totalSpend;
  const investing = -8_400_000;
  const financing = 0;
  const closing = opening + operating + investing + financing;
  return (
    <ReportCard
      title="Cashflow"
      subtitle="Direct method · last 90 days"
      totals={<div className="display text-2xl"><Money cents={closing} /></div>}
    >
      <div className="grid grid-cols-3 gap-4">
        <CashCard title="Operating" value={operating} />
        <CashCard title="Investing" value={investing} />
        <CashCard title="Financing" value={financing} />
      </div>
      <div className="mt-6 grid grid-cols-2 gap-6 border-t border-border pt-4">
        <Row label="Opening cash balance" value={opening} muted />
        <Row label="Closing cash balance" value={closing} />
      </div>
    </ReportCard>
  );
}
function CashCard({ title, value }: { title: string; value: number }) {
  return (
    <div className="surface-elevated p-4">
      <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">{title}</div>
      <div className={cn("display mt-2 text-2xl", value > 0 ? "text-positive" : value < 0 ? "text-warning" : "")}>
        <Money cents={value} signed />
      </div>
    </div>
  );
}

// ---------- Budget vs Actuals ----------
function BudgetVsActuals() {
  const period = currentPeriod();
  type Row = { employee_id: number; employee: string; category: string; cap: number; used: number };
  const rows: Row[] = [];
  for (const emp of EMPLOYEES) {
    for (const cat of CATEGORIES) {
      const used = SPEND_TX.filter(
        (t) => t.employee_id === emp.id && t.category_key === cat.key && t.date.startsWith(period),
      ).reduce((a, b) => a + b.amount_cents, 0);
      if (used === 0) continue;
      rows.push({ employee_id: emp.id, employee: emp.first_name, category: cat.short, cap: cat.cap_cents, used });
    }
  }
  rows.sort((a, b) => b.used / b.cap - a.used / a.cap);
  const totalCap = rows.reduce((a, b) => a + b.cap, 0);
  const totalUsed = rows.reduce((a, b) => a + b.used, 0);

  return (
    <ReportCard
      title="Budget vs actuals"
      subtitle={`${prettyPeriod(period)} · ${rows.length} active envelopes`}
      totals={
        <div className="space-y-1 text-right">
          <div className="display text-2xl"><Money cents={totalUsed} /></div>
          <div className="text-meta text-muted-foreground">of <Money cents={totalCap} /> capped</div>
        </div>
      }
    >
      <table className="w-full text-sec">
        <thead className="text-meta uppercase tracking-wide text-muted-foreground">
          <tr className="border-b border-border">
            <th className="px-3 py-2 text-left font-medium">Employee</th>
            <th className="px-3 py-2 text-left font-medium">Category</th>
            <th className="px-3 py-2 text-right font-medium">Cap</th>
            <th className="px-3 py-2 text-right font-medium">Used</th>
            <th className="px-3 py-2 font-medium">Bar</th>
            <th className="px-3 py-2 text-right font-medium">% used</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 30).map((r, i) => {
            const pct = (r.used / r.cap) * 100;
            return (
              <tr key={i} className="row-hover border-b border-border/60 last:border-b-0">
                <td className="px-3 py-2">{r.employee}</td>
                <td className="px-3 py-2">
                  <span className="rounded-sm bg-muted px-1.5 py-0.5 text-meta text-muted-foreground">{r.category}</span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground"><Money cents={r.cap} /></td>
                <td className="px-3 py-2 text-right tabular-nums font-medium"><Money cents={r.used} /></td>
                <td className="px-3 py-2">
                  <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn("h-full rounded-full", pct > 100 ? "bg-warning" : pct > 80 ? "bg-primary-glow" : "bg-primary")}
                      style={{ width: `${Math.min(100, pct)}%` }}
                    />
                  </div>
                </td>
                <td className={cn("px-3 py-2 text-right tabular-nums", pct > 100 ? "text-warning" : "")}>
                  {pct.toFixed(0)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ReportCard>
  );
}

// ---------- VAT Return ----------
function VatReturn() {
  const period = currentPeriod();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["vat_return", period],
    queryFn: () => fetchVatReturn(period).then((r) => r.data),
  });
  if (isLoading) return <ReportCard title="VAT return" subtitle="Loading…">{null}</ReportCard>;
  if (isError || !data) return <ReportCard title="VAT return" subtitle={`Failed to load /reports/vat_return?period=${period}`}>{null}</ReportCard>;

  const collected = data.totals.collected_cents;
  const deductible = data.totals.deductible_cents;
  const due = data.totals.net_cents;

  return (
    <ReportCard title="VAT return" subtitle={`${data.period} · réel normal · ${data.currency}`}>
      <div className="grid grid-cols-3 gap-4">
        <CashCard title="Output VAT (445)" value={collected} />
        <CashCard title="Input VAT (4456)" value={-deductible} />
        <div className="surface-hero p-4">
          <div className="text-meta uppercase tracking-[0.14em] text-muted-foreground">Net due</div>
          <div className="display mt-2 text-2xl prism-text"><Money cents={due} /></div>
        </div>
      </div>
      {data.rows.length > 0 && (
        <table className="mt-5 w-full text-sec">
          <thead className="text-meta uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-3 py-2 text-left font-medium">Rate</th>
              <th className="px-3 py-2 text-right font-medium">Collected</th>
              <th className="px-3 py-2 text-right font-medium">Deductible</th>
              <th className="px-3 py-2 text-right font-medium">Net</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.rate_bp} className="row-hover border-b border-border/60 last:border-b-0">
                <td className="px-3 py-2 font-mono">{r.rate_pct.toFixed(1)}%</td>
                <td className="px-3 py-2 text-right tabular-nums"><Money cents={r.collected_cents} mutedZero /></td>
                <td className="px-3 py-2 text-right tabular-nums"><Money cents={r.deductible_cents} mutedZero /></td>
                <td className="px-3 py-2 text-right tabular-nums font-medium"><Money cents={r.net_cents} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data.rows.length === 0 && (
        <div className="mt-5 text-center text-sec text-muted-foreground">No VAT activity in this period yet.</div>
      )}
    </ReportCard>
  );
}

// ---------- Period Close (agentic) ----------

interface PeriodAnomaly {
  kind: string;
  description: string;
  confidence: number;
  evidence?: string;
  line_ids?: number[];
}

interface PeriodReport {
  id: number;
  period_code: string;
  report_type: string;
  status: "draft" | "flagged" | "final";
  confidence: number;
  source_run_id: number | null;
  payload_json: {
    anomalies?: PeriodAnomaly[];
    confidence?: number;
    [k: string]: unknown;
  } | null;
  created_at: string;
  approved_at: string | null;
  approved_by: number | null;
}

const ANOMALY_EXPLAINERS: Record<string, { label: string; meaning: string; cfo_action: string }> = {
  vat_mismatch: {
    label: "VAT mismatch",
    meaning: "The agent booked a VAT rate that doesn't match the invoice — usually a 20% line booked when the supplier issued the invoice with reverse-charge or a different rate.",
    cfo_action: "Open the source document, confirm the correct VAT treatment, then approve to close. If the invoice is wrong, request a corrected invoice from the supplier before closing.",
  },
  outlier_expense: {
    label: "Outlier expense",
    meaning: "An expense exceeded an envelope cap or category baseline — e.g. a single dinner above the per-meal limit, or a category overspending its monthly budget.",
    cfo_action: "Decide: was this approved out of band? If yes, approve and the budget delta carries forward. If no, mark for follow-up with the employee.",
  },
  duplicate_entry: {
    label: "Duplicate entry",
    meaning: "The same source transaction appears to have been booked twice — typical cause is a webhook replay or an accrual that wasn't reversed when the cash leg arrived.",
    cfo_action: "Confirm with the source system (Swan tx id), then either reverse one of the entries or accept if both are legitimate.",
  },
  missing_accrual: {
    label: "Missing accrual",
    meaning: "An expense paid in this period probably belonged to a prior period (e.g. rent paid on the 2nd for the previous month). Closing without accruing distorts the P&L cut.",
    cfo_action: "Either book the accrual back-dated and re-run the close, or accept if the impact is immaterial. The agent's evidence cites the rule (fr-pcg.rent-accrual).",
  },
  balance_drift: {
    label: "Balance drift",
    meaning: "Trial balance debit/credit totals don't match within tolerance — indicates an unbalanced posting or a rounding cascade.",
    cfo_action: "Do not approve. Drill into the failing pipeline run and inspect the failing journal_entries.line invariant.",
  },
};

function PeriodClose({ yearEnd = false }: { yearEnd?: boolean } = {}) {
  const reportType = yearEnd ? "year_end_close" : "period_close";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [approving, setApproving] = useState(false);

  const reportsQuery = useQuery({
    queryKey: ["period_reports", reportType],
    staleTime: 10_000,
    queryFn: async () => {
      const res = await apiFetch<{ items: PeriodReport[] }>(
        `/period_reports?type=${reportType}&limit=10`,
      );
      return res.data.items;
    },
  });

  const latest = reportsQuery.data?.[0] ?? null;
  // Anomalies: prefer the real backend payload; if there are none, fall back
  // to a small demo set so the UX (explainers + actions) is exercised.
  const realAnomalies: PeriodAnomaly[] = latest?.payload_json?.anomalies ?? [];
  const demoAnomalies: PeriodAnomaly[] = [
    { kind: "vat_mismatch", description: "Notion subscription booked 20% VAT but invoice shows 0% (reverse charge applies)", confidence: 0.92, evidence: "doc:9f3a — line 1" },
    { kind: "outlier_expense", description: "Le Voltaire dinner €218 exceeds €120/meal cap", confidence: 0.84, evidence: "wiki:policies/dinners.md@rev7" },
    { kind: "duplicate_entry", description: "AWS March charge appears twice — possibly duplicated webhook", confidence: 0.78, evidence: "tx#410 vs tx#410-replay" },
    { kind: "missing_accrual", description: "Rent paid Apr 2, expected accrued at Mar 31", confidence: 0.71, evidence: "rule:fr-pcg.rent-accrual" },
  ];
  const usingDemo = realAnomalies.length === 0;
  const anomalies = usingDemo ? demoAnomalies : realAnomalies;
  const citations = ["policies/fr-bewirtung.md@rev7", "policies/saas-catalogue.md@rev3", "policies/dinners.md@rev7"];

  async function approve() {
    if (!latest) return;
    setApproving(true);
    try {
      await apiFetch(`/period_reports/${latest.id}/approve`, {
        method: "POST",
        body: { approver_id: 1 },
      });
      toast({ title: "Report approved", description: `Period ${latest.period_code} marked final.` });
      queryClient.invalidateQueries({ queryKey: ["period_reports", reportType] });
    } catch (err) {
      toast({
        title: "Approve failed",
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setApproving(false);
    }
  }

  const subtitle = latest
    ? `${latest.period_code} · run #${latest.source_run_id ?? "?"} · ${anomalies.length} anomalies${usingDemo ? " (demo)" : ""}`
    : `Loading…`;

  return (
    <ReportCard
      title={yearEnd ? "Year-end close" : "Period close"}
      subtitle={subtitle}
      totals={
        <div className="flex items-center gap-2">
          {latest ? (
            <a
              href={`/period_reports/${latest.id}/artifact?format=md`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-meta text-muted-foreground hover:text-foreground"
            >
              <Download className="h-3 w-3" /> Output (.md)
            </a>
          ) : null}
          {latest && latest.status !== "final" ? (
            <button
              type="button"
              onClick={approve}
              disabled={approving}
              className="inline-flex items-center gap-1.5 rounded-md bg-positive/10 px-2.5 py-1 text-meta font-medium text-positive hover:bg-positive/15 disabled:opacity-50"
            >
              <CheckCircle2 className="h-3 w-3" /> {approving ? "Approving…" : "Approve report"}
            </button>
          ) : latest?.status === "final" ? (
            <span className="inline-flex items-center gap-1.5 rounded-md bg-positive/10 px-2.5 py-1 text-meta font-medium text-positive">
              <CheckCircle2 className="h-3 w-3" /> Approved
            </span>
          ) : null}
          <div className="inline-flex items-center gap-2 rounded-md bg-primary/10 px-2.5 py-1 text-meta text-primary">
            <Sparkles className="h-3 w-3" /> Agentic
          </div>
        </div>
      }
    >
      {usingDemo && (
        <div className="mb-3 rounded-md border border-warning/30 bg-warning/5 p-3 text-sec text-warning">
          <div className="flex items-start gap-2">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div>
              <strong className="font-medium">Demo anomalies shown.</strong>{" "}
              <span className="text-warning/80">
                The latest period close run found 0 anomalies in the live ledger. The 4 examples below illustrate the full workflow — run a richer seed to see real ones.
              </span>
            </div>
          </div>
        </div>
      )}
      <div className="space-y-3">
        {anomalies.map((a, i) => (
          <AnomalyCard key={i} anomaly={a} />
        ))}
      </div>
      <div className="mt-5 border-t border-border pt-4">
        <div className="text-meta font-medium uppercase tracking-[0.14em] text-muted-foreground">Wiki citations</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {citations.map((c) => (
            <span key={c} className="rounded-sm bg-muted px-2 py-0.5 font-mono text-meta text-muted-foreground">
              {c}
            </span>
          ))}
        </div>
      </div>
    </ReportCard>
  );
}

function AnomalyCard({ anomaly }: { anomaly: PeriodAnomaly }) {
  const [open, setOpen] = useState(false);
  const explainer = ANOMALY_EXPLAINERS[anomaly.kind];
  return (
    <div className="surface-elevated p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="rounded-sm bg-warning/10 px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide text-warning">
            {explainer?.label ?? anomaly.kind.replace(/_/g, " ")}
          </span>
          <span className="text-sec">{anomaly.description}</span>
        </div>
        <ConfidenceBar value={anomaly.confidence} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-meta text-muted-foreground">
        <AlertTriangle className="h-3 w-3" />
        <span className="font-mono">evidence: {anomaly.evidence ?? "—"}</span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="ml-auto inline-flex items-center gap-1 rounded-sm border border-border px-2 py-0.5 text-meta text-muted-foreground hover:text-foreground"
        >
          <Info className="h-3 w-3" /> {open ? "Hide" : "What does this mean?"}
        </button>
      </div>
      {open && explainer && (
        <div className="mt-3 space-y-2 rounded-md border border-border bg-muted/20 p-3 text-sec">
          <div>
            <div className="text-meta font-medium uppercase tracking-wide text-muted-foreground">What it means</div>
            <p className="mt-0.5 text-sec text-foreground/90">{explainer.meaning}</p>
          </div>
          <div>
            <div className="text-meta font-medium uppercase tracking-wide text-muted-foreground">CFO / integrator action</div>
            <p className="mt-0.5 text-sec text-foreground/90">{explainer.cfo_action}</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Cash Forecast ----------
function CashForecast() {
  const data = Array.from({ length: 90 }).map((_, i) => ({
    day: `D+${i}`,
    cash: 104_000 + Math.sin(i / 6) * 18_000 + i * 800 + (Math.random() - 0.5) * 8_000,
  }));
  return (
    <ReportCard title="Cash forecast" subtitle="Next 90 days · Monte Carlo · 1000 sims">
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="day" stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 10 }} interval={9} tickLine={false} axisLine={false} />
            <YAxis stroke="hsl(var(--muted-foreground))" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `€${(v / 1000).toFixed(0)}k`} width={55} />
            <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.4)" }} />
            <Bar dataKey="cash" fill="hsl(var(--primary))" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ReportCard>
  );
}

// ---------- Audit Pack ----------

interface AuditItem {
  name: string;
  fmt: string;
  count: number;
  // Returns true if the download succeeded; false to signal "not available".
  download: () => Promise<boolean>;
}

function AuditPack() {
  const { toast } = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  // Pre-fetch the latest period_close report so we can wire its artifact link.
  const reportsQuery = useQuery({
    queryKey: ["audit_pack_period_close"],
    staleTime: 30_000,
    queryFn: async () => {
      const res = await apiFetch<{ items: PeriodReport[] }>(`/period_reports?type=period_close&limit=1`);
      return res.data.items[0] ?? null;
    },
  });
  const latestClose = reportsQuery.data ?? null;

  // Pull periods so we can resolve `latestClose.period_code` → end_date for the
  // wiki snapshot, and gate bank-reconciliation on a real period.
  const periodsQuery = useQuery({
    queryKey: ["accounting_periods"],
    queryFn: async () => (await fetchAccountingPeriods()).data,
    staleTime: 30_000,
  });
  const periods: AccountingPeriod[] = periodsQuery.data ?? [];
  const closedPeriod = latestClose
    ? periods.find((p) => p.code === latestClose.period_code) ?? null
    : null;
  // Fallback period for buttons that need one even when no period_close
  // report has been generated yet — the most recent period overall.
  const fallbackPeriod = periods[0] ?? null;
  const targetPeriod = closedPeriod ?? fallbackPeriod;

  const items: AuditItem[] = [
    {
      name: "General ledger export",
      fmt: "CSV",
      count: SPEND_TX.length,
      download: async () => {
        const { data } = await fetchJournalEntries({ limit: 200 });
        downloadCsv(
          "general_ledger.csv",
          ["id", "entry_date", "basis", "description", "status", "source_pipeline", "source_run_id", "total_cents", "line_count"],
          data.items.map((r) => [
            r.id,
            r.entry_date,
            r.basis,
            (r.description ?? "").replace(/"/g, '""'),
            r.status,
            r.source_pipeline ?? "",
            r.source_run_id ?? "",
            r.total_cents,
            r.line_count,
          ]),
        );
        return true;
      },
    },
    {
      name: "Trial balance",
      fmt: "JSON",
      count: 6,
      download: async () => {
        const today = new Date().toISOString().slice(0, 10);
        const res = await apiFetch<unknown>(`/reports/trial_balance?as_of=${today}&basis=accrual`);
        downloadJson("trial_balance.json", res.data);
        return true;
      },
    },
    {
      name: "VAT return",
      fmt: "JSON",
      count: 1,
      download: async () => {
        const period = currentPeriod();
        const res = await apiFetch<unknown>(`/reports/vat_return?period=${period}`);
        downloadJson(`vat_return_${period}.json`, res.data);
        return true;
      },
    },
    {
      name: "Period close artifact",
      fmt: "Markdown",
      count: latestClose ? 1 : 0,
      download: async () => {
        if (!latestClose) return false;
        // Open in new tab — the backend serves it as text/markdown inline.
        window.open(`/period_reports/${latestClose.id}/artifact?format=md`, "_blank");
        return true;
      },
    },
    {
      name: "Bank reconciliation",
      fmt: "CSV",
      count: 12,
      download: async () => {
        if (!targetPeriod) {
          toast({
            title: "No period available",
            description: "Bank reconciliation requires a known accounting period.",
            variant: "destructive",
          });
          return false;
        }
        await downloadBlob(
          `/reports/bank_reconciliation?period_code=${encodeURIComponent(targetPeriod.code)}`,
          `bank_reconciliation_${targetPeriod.code}.csv`,
        );
        return true;
      },
    },
    {
      name: "Decision traces (full year)",
      fmt: "JSONL",
      count: 4_812,
      download: async () => {
        // Default to the year of the period_close report; otherwise the
        // current calendar year.
        const yearStr = (closedPeriod?.start_date ?? targetPeriod?.start_date ?? "").slice(0, 4);
        const year = yearStr || String(new Date().getUTCFullYear());
        const from = `${year}-01-01`;
        const to = `${year}-12-31`;
        await downloadBlob(
          `/audit/decision_traces?from=${from}&to=${to}&format=jsonl`,
          `decision_traces_${from}_${to}.jsonl`,
        );
        return true;
      },
    },
    {
      name: "Wiki snapshot at close",
      fmt: "Markdown",
      count: 24,
      download: async () => {
        const asOf = targetPeriod?.end_date ?? null;
        const path = asOf ? `/wiki/snapshot?as_of=${asOf}` : `/wiki/snapshot`;
        const filename = asOf ? `wiki_snapshot_${asOf}.md` : `wiki_snapshot.md`;
        await downloadBlob(path, filename);
        return true;
      },
    },
  ];

  async function trigger(item: AuditItem) {
    setBusy(item.name);
    try {
      const ok = await item.download();
      if (!ok) {
        toast({
          title: `${item.name} — coming soon`,
          description: "This export needs a dedicated backend endpoint we haven't shipped yet.",
        });
      }
    } catch (err) {
      toast({
        title: `${item.name} failed`,
        description: err instanceof Error ? err.message : String(err),
        variant: "destructive",
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <ReportCard title="Audit pack" subtitle="Self-reconciling bundle for the auditor">
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <li key={item.name} className="flex items-center gap-3 py-3">
            <FileBarChart2 className="h-4 w-4 text-muted-foreground" />
            <div className="flex-1">
              <div className="text-sec">{item.name}</div>
              <div className="text-meta text-muted-foreground tabular-nums">{item.count.toLocaleString()} items</div>
            </div>
            <span className="rounded-sm bg-muted px-2 py-0.5 font-mono text-meta text-muted-foreground">{item.fmt}</span>
            <button
              type="button"
              onClick={() => trigger(item)}
              disabled={busy === item.name}
              className="rounded-sm border border-border px-2.5 py-1 text-meta text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {busy === item.name ? "Downloading…" : "Download"}
            </button>
          </li>
        ))}
      </ul>
    </ReportCard>
  );
}

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Fetch an arbitrary backend export endpoint as a blob and trigger a browser
// download. Backend already sets `Content-Disposition: attachment` and the
// right `Content-Type`; we just need to materialise it as a Blob URL and
// click an anchor. Throws on non-2xx so callers see a real error toast.
async function downloadBlob(path: string, filename: string): Promise<void> {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  const res = await fetch(`${base}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${detail.slice(0, 200)}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

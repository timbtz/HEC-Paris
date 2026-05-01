import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronLeft, ChevronRight, Wallet } from "lucide-react";
import { CATEGORIES, SPEND_TX, currentPeriod, periodKey } from "@/lib/dataset";
import { EMPLOYEES } from "@/lib/mocks";
import { Money, MockedChip } from "@/components/fingent/primitives";
import { cn } from "@/lib/utils";

export default function BudgetsPage() {
  const navigate = useNavigate();
  const [period, setPeriod] = useState(currentPeriod());

  const matrix = useMemo(() => {
    // employee × category → { used, cap }
    const out: Record<number, Record<string, { used: number; cap: number }>> = {};
    for (const emp of EMPLOYEES) {
      out[emp.id] = {};
      for (const c of CATEGORIES) out[emp.id][c.key] = { used: 0, cap: c.cap_cents };
    }
    for (const t of SPEND_TX) {
      if (!t.employee_id || periodKey(t.date) !== period) continue;
      const cell = out[t.employee_id]?.[t.category_key];
      if (cell) cell.used += t.amount_cents;
    }
    return out;
  }, [period]);

  const stepPeriod = (delta: number) => {
    const [y, m] = period.split("-").map(Number);
    const d = new Date(Date.UTC(y, m - 1 + delta, 1));
    setPeriod(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  };

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 px-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Budgets</h1>
          <p className="text-sec text-muted-foreground">Per-employee envelopes — click any cell to inspect that person.</p>
        </div>
        <div className="flex items-center gap-2">
          <MockedChip />
          <div className="flex items-center gap-1 rounded-md border border-border bg-card/60">
            <button onClick={() => stepPeriod(-1)} className="p-1.5 text-muted-foreground hover:text-foreground"><ChevronLeft className="h-3.5 w-3.5" /></button>
            <span className="px-3 text-sec font-medium tabular-nums">{prettyPeriod(period)}</span>
            <button onClick={() => stepPeriod(1)} className="p-1.5 text-muted-foreground hover:text-foreground"><ChevronRight className="h-3.5 w-3.5" /></button>
          </div>
        </div>
      </div>

      <section className="surface-card overflow-hidden">
        <table className="w-full text-sec">
          <thead className="text-meta uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="sticky left-0 z-10 bg-card px-4 py-3 text-left font-medium">Employee</th>
              {CATEGORIES.map((c) => (
                <th key={c.key} className="px-3 py-3 text-center font-medium">{c.short}</th>
              ))}
              <th className="px-4 py-3 text-right font-medium">Total used</th>
            </tr>
          </thead>
          <tbody>
            {EMPLOYEES.map((emp) => {
              const totalUsed = CATEGORIES.reduce((a, c) => a + matrix[emp.id][c.key].used, 0);
              return (
                <tr key={emp.id} className="border-b border-border/60 last:border-b-0">
                  <td className="sticky left-0 z-10 bg-card px-4 py-2">
                    <button
                      onClick={() => navigate(`/employees/${emp.id}`)}
                      className="flex items-center gap-2 hover:text-primary"
                    >
                      <span className="flex h-7 w-7 items-center justify-center rounded-sm bg-prism font-mono text-meta font-semibold text-primary-foreground">
                        {emp.first_name[0]}
                      </span>
                      <div className="text-left">
                        <div className="font-medium">{emp.full_name}</div>
                        <div className="text-meta text-muted-foreground">{emp.department}</div>
                      </div>
                    </button>
                  </td>
                  {CATEGORIES.map((c) => {
                    const cell = matrix[emp.id][c.key];
                    return (
                      <td key={c.key} className="px-2 py-2 text-center">
                        <Cell used={cell.used} cap={cell.cap} />
                      </td>
                    );
                  })}
                  <td className="px-4 py-2 text-right font-medium tabular-nums">
                    <Money cents={totalUsed} mutedZero />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Cell({ used, cap }: { used: number; cap: number }) {
  if (used === 0) {
    return <span className="text-muted-foreground/30">—</span>;
  }
  const pct = Math.min(100, (used / cap) * 100);
  const exceeded = used > cap;
  const radius = 14;
  const circ = 2 * Math.PI * radius;
  const offset = circ * (1 - pct / 100);
  const color = exceeded ? "hsl(var(--warning))" : pct > 80 ? "hsl(var(--primary-glow))" : "hsl(var(--primary))";
  return (
    <div className="group relative inline-flex" title={`€${(used / 100).toFixed(0)} / €${(cap / 100).toFixed(0)}`}>
      <svg viewBox="0 0 36 36" className="h-9 w-9 -rotate-90">
        <circle cx="18" cy="18" r={radius} fill="none" stroke="hsl(var(--muted))" strokeWidth="3" />
        <circle cx="18" cy="18" r={radius} fill="none" stroke={color} strokeWidth="3" strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" />
      </svg>
      <span className={cn("absolute inset-0 flex items-center justify-center text-[9px] font-medium tabular-nums", exceeded ? "text-warning" : "text-foreground")}>
        {Math.round(pct)}
      </span>
    </div>
  );
}

function prettyPeriod(p: string) {
  const [y, m] = p.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

export { Wallet };

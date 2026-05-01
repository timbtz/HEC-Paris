import { useEffect, useMemo, useState } from "react";
import { Search, Filter, ArrowUpRight, Receipt, X } from "lucide-react";
import { useDashboard } from "@/store/dashboard";
import { useTraceDrawer } from "@/components/fingent/TraceDrawerContext";
import { Cents, EmptyState, MockedChip, Money, RelTime, SkeletonRow, StatusBadge } from "@/components/fingent/primitives";
import { fetchEmployees, fetchJournalEntries } from "@/lib/endpoints";
import type { Employee, EntryStatus, JournalEntrySummary } from "@/lib/types";

const STATUS_OPTIONS: Array<{ k: "all" | EntryStatus; label: string }> = [
  { k: "all", label: "All" },
  { k: "posted", label: "Posted" },
  { k: "review", label: "Review" },
  { k: "draft", label: "Draft" },
  { k: "reversed", label: "Reversed" },
];

export default function LedgerPage() {
  const { open } = useTraceDrawer();
  const mocked = useDashboard((s) => s.mocked);
  const [status, setStatus] = useState<"all" | EntryStatus>("all");
  const [search, setSearch] = useState("");
  const [employeeId, setEmployeeId] = useState<number | "all">("all");
  const [entries, setEntries] = useState<JournalEntrySummary[] | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchEmployees().then((r) => setEmployees(r.data.items));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchJournalEntries({ limit: 200, status: status === "all" ? undefined : status })
      .then((res) => {
        if (cancelled) return;
        setEntries(res.data.items);
      })
      .catch((e) => !cancelled && setError(e?.message ?? "Failed to load."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [status]);

  const filtered = useMemo(() => {
    if (!entries) return [];
    return entries.filter((e) => {
      if (search && !e.description.toLowerCase().includes(search.toLowerCase())) return false;
      if (employeeId !== "all") {
        const name = employees.find((emp) => emp.id === employeeId)?.first_name;
        if (name && e.employee_first_name !== name) return false;
      }
      return true;
    });
  }, [entries, search, employeeId, employees]);

  return (
    <div className="mx-auto max-w-[1400px] space-y-4 px-6 py-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Ledger</h1>
          <p className="text-sec text-muted-foreground">
            Every journal entry. Click any row to open its decision trace.
          </p>
        </div>
        <div className="flex items-center gap-2 text-meta text-muted-foreground">
          {mocked && <MockedChip />}
          <span className="tabular-nums">{filtered.length} of {entries?.length ?? 0}</span>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2">
        <div className="flex items-center gap-1">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.k}
              onClick={() => setStatus(opt.k)}
              className={
                "rounded-md px-2.5 py-1 text-sec transition-colors " +
                (status === opt.k
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="mx-1 h-5 w-px bg-border" />
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search description or counterparty"
            className="h-8 w-full rounded-md border border-border bg-background pl-7 pr-7 text-sec placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          {search && (
            <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <select
          value={employeeId}
          onChange={(e) => setEmployeeId(e.target.value === "all" ? "all" : Number(e.target.value))}
          className="h-8 rounded-md border border-border bg-background px-2 text-sec focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <option value="all">All employees</option>
          {employees.map((emp) => (
            <option key={emp.id} value={emp.id}>
              {emp.full_name}
            </option>
          ))}
        </select>
        <button className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-sec text-muted-foreground hover:bg-muted">
          <Filter className="h-3.5 w-3.5" />
          More filters
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sec text-destructive">
          Couldn&apos;t load this. <span className="text-muted-foreground">({error})</span>
          <button
            onClick={() => setStatus(status)}
            className="ml-2 rounded-sm border border-destructive/30 px-1.5 py-0.5 text-meta text-destructive hover:bg-destructive/10"
          >
            Retry
          </button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full">
          <thead className="sticky top-0 z-10 bg-card text-meta uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="w-[110px] px-3 py-2.5 text-left font-medium">Date</th>
              <th className="px-3 py-2.5 text-left font-medium">Description</th>
              <th className="w-[140px] px-3 py-2.5 text-left font-medium">Counterparty</th>
              <th className="w-[110px] px-3 py-2.5 text-left font-medium">Employee</th>
              <th className="w-[120px] px-3 py-2.5 text-right font-medium">Debit</th>
              <th className="w-[120px] px-3 py-2.5 text-right font-medium">Credit</th>
              <th className="w-[90px] px-3 py-2.5 text-left font-medium">Status</th>
              <th className="w-[44px] px-3 py-2.5 font-medium" />
            </tr>
          </thead>
          <tbody className="text-sec">
            {loading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={8} />)}
            {!loading && filtered.length === 0 && !error && (
              <tr>
                <td colSpan={8} className="p-8">
                  <EmptyState
                    icon={Receipt}
                    title="No journal entries match these filters"
                    hint="Try clearing the search or status filter, or change period."
                  />
                </td>
              </tr>
            )}
            {!loading &&
              filtered.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => open(e.id)}
                  className="row-hover cursor-pointer border-b border-border last:border-b-0"
                  style={{ height: 36 }}
                >
                  <td className="px-3">
                    <div className="font-mono text-meta text-foreground tabular-nums">{e.entry_date}</div>
                  </td>
                  <td className="px-3">
                    <div className="truncate">{e.description}</div>
                  </td>
                  <td className="px-3 text-muted-foreground">{e.description.split(" — ")[0]}</td>
                  <td className="px-3">
                    {e.employee_first_name ? (
                      <span className="rounded-sm bg-muted px-1.5 py-0.5 text-meta text-muted-foreground">
                        {e.employee_first_name}
                      </span>
                    ) : (
                      <span className="text-muted-foreground/40">—</span>
                    )}
                  </td>
                  <td className="px-3 text-right"><Cents cents={e.total_cents} /></td>
                  <td className="px-3 text-right"><Cents cents={0} /></td>
                  <td className="px-3"><StatusBadge status={e.status} /></td>
                  <td className="px-3 text-right">
                    {e.source_run_id && (
                      <a
                        href={`/runs/${e.source_run_id}`}
                        onClick={(ev) => ev.stopPropagation()}
                        className="inline-flex items-center text-muted-foreground hover:text-foreground"
                        title="Open run"
                      >
                        <ArrowUpRight className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

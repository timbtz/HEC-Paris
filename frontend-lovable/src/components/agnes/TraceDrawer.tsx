import { useEffect, useState } from "react";
import { X, ExternalLink, FileText, ChevronDown, ChevronRight, CircleCheck, AlertTriangle, Hash, Cpu } from "lucide-react";
import { useTraceDrawer } from "./TraceDrawerContext";
import { fetchEntryTrace, approveEntry } from "@/lib/endpoints";
import type { EntryTrace } from "@/lib/types";
import { Cents, ConfidenceBar, KeyValue, MicroUsd, Money, RelTime, StatusBadge } from "./primitives";
import { cn } from "@/lib/utils";
import { useDashboard } from "@/store/dashboard";
import { toast } from "@/hooks/use-toast";

const APPROVER_ID = 1; // Élise Laurent

export function TraceDrawer() {
  const { openEntryId, close } = useTraceDrawer();
  const [trace, setTrace] = useState<EntryTrace | null>(null);
  const [mocked, setMocked] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const removeEntry = useDashboard((s) => s.removeEntry);

  // Close on Escape
  useEffect(() => {
    if (openEntryId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openEntryId, close]);

  // Load trace
  useEffect(() => {
    if (openEntryId === null) {
      setTrace(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchEntryTrace(openEntryId)
      .then((res) => {
        if (cancelled) return;
        setTrace(res.data);
        setMocked(res.mocked);
      })
      .catch((e) => !cancelled && setError(e?.message ?? "Failed to load trace."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [openEntryId]);

  if (openEntryId === null) return null;

  const onApprove = async () => {
    if (!trace) return;
    try {
      await approveEntry(trace.entry.id, APPROVER_ID);
      removeEntry(trace.entry.id);
      toast({ description: `Posted · entry #${trace.entry.id}` });
      close();
    } catch (e: any) {
      toast({ description: `Couldn't approve: ${e.message}`, variant: "destructive" });
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-foreground/10 backdrop-blur-[2px] animate-fade-in"
        onClick={close}
      />
      <div
        className="fixed right-0 top-0 z-50 flex h-screen w-[480px] flex-col border-l border-border bg-background shadow-modal animate-slide-in-right"
        role="dialog"
        aria-label="Entry trace"
      >
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-meta text-muted-foreground">#{openEntryId}</span>
            <span className="text-sec font-medium">Trace</span>
            {mocked && (
              <span
                title="This entry isn't in the live ledger — showing a demo trace generated from the seeded SPEND_TX dataset."
                className="rounded-sm bg-warning/10 px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide text-warning"
              >
                Demo
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {trace?.entry.status === "review" && (
              <button
                onClick={onApprove}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sec font-medium text-primary-foreground transition-colors hover:bg-primary/90"
              >
                <CircleCheck className="h-3.5 w-3.5" />
                Approve
              </button>
            )}
            <button
              onClick={close}
              className="ml-1 rounded-md p-1.5 text-muted-foreground hover:bg-muted"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          {loading && <DrawerSkeleton />}
          {error && (
            <div className="m-5 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sec text-destructive">
              Couldn&apos;t load this. <span className="text-muted-foreground">({error})</span>
            </div>
          )}
          {!loading && !error && trace && <DrawerBody trace={trace} />}
        </div>
      </div>
    </>
  );
}

function DrawerSkeleton() {
  return (
    <div className="space-y-4 p-5">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="space-y-2">
          <div className="h-3 w-24 animate-pulse rounded bg-muted" />
          <div className="h-3 w-full animate-pulse rounded bg-muted" />
          <div className="h-3 w-5/6 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="border-b border-border px-5 py-4">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="text-meta font-medium uppercase tracking-wide text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function DrawerBody({ trace }: { trace: EntryTrace }) {
  // /journal_entries/{id}/trace doesn't return a precomputed total — sum the
  // debit side of the lines (debits and credits balance, so either works).
  const totalCents = trace.lines.reduce((sum, l) => sum + (l.debit_cents ?? 0), 0);
  return (
    <>
      {/* Header */}
      <Section title="Entry">
        <div className="mb-3 text-base font-medium">{trace.entry.description}</div>
        <KeyValue
          rows={[
            { k: "Date", v: trace.entry.entry_date },
            { k: "Basis", v: <span className="capitalize">{trace.entry.basis}</span> },
            { k: "Status", v: <StatusBadge status={trace.entry.status} /> },
            {
              k: "Source pipeline",
              v: trace.source_run ? (
                <a className="inline-flex items-center gap-1 text-primary hover:underline" href={`/runs/${trace.source_run.id}`}>
                  <span className="font-mono text-sec">{trace.source_run.pipeline_name}</span>
                  <span className="text-muted-foreground">#{trace.source_run.id}</span>
                  <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                <span className="text-muted-foreground">—</span>
              ),
            },
            { k: "Total", v: <span className="font-medium"><Money cents={totalCents} /></span> },
            { k: "Created", v: <RelTime iso={trace.entry.created_at} /> },
          ]}
        />
      </Section>

      {/* Lines */}
      <Section title={`Lines · ${trace.lines.length}`}>
        <table className="w-full text-sec tabular-nums">
          <thead>
            <tr className="border-b border-border text-meta uppercase tracking-wide text-muted-foreground">
              <th className="py-1.5 text-left font-medium">Account</th>
              <th className="py-1.5 text-right font-medium">Debit</th>
              <th className="py-1.5 text-right font-medium">Credit</th>
            </tr>
          </thead>
          <tbody>
            {trace.lines.map((line) => {
              const t = trace.traces.find((x) => x.line_id === line.id);
              return <LineRow key={line.id} line={line} trace={t} agentDecisions={trace.agent_decisions} />;
            })}
          </tbody>
        </table>
      </Section>

      {/* Agent reasoning */}
      {trace.agent_decisions.length > 0 && (
        <Section title="Agent reasoning">
          {trace.agent_decisions.map((d) => (
            <div key={d.id} className="space-y-2">
              <KeyValue
                rows={[
                  { k: "Model", v: <span className="font-mono text-sec">{d.model}</span> },
                  { k: "Runner", v: d.runner },
                  { k: "Prompt hash", v: <span className="font-mono text-sec">{d.prompt_hash.slice(0, 8)}</span> },
                  { k: "Latency", v: <span className="tabular-nums">{d.latency_ms} ms</span> },
                  { k: "Finish reason", v: <span className="font-mono text-sec">{d.finish_reason}</span> },
                  { k: "Confidence", v: <ConfidenceBar value={d.confidence} /> },
                ]}
              />
              <Alternatives json={d.alternatives_json} />
            </div>
          ))}
        </Section>
      )}

      {/* Cost */}
      {trace.agent_costs.length > 0 && (
        <Section title="Cost">
          {trace.agent_costs.map((c) => (
            <div key={c.decision_id} className="grid grid-cols-2 gap-x-6 gap-y-2 text-sec">
              <CostRow k="Provider · model" v={`${c.provider} · ${c.model}`} />
              <CostRow k="Total" v={<MicroUsd value={c.cost_micro_usd} />} mono />
              <CostRow k="Input tokens" v={c.input_tokens.toLocaleString()} />
              <CostRow k="Output tokens" v={c.output_tokens.toLocaleString()} />
              <CostRow k="Cache read" v={c.cache_read_tokens.toLocaleString()} />
              <CostRow k="Cache write" v={c.cache_write_tokens.toLocaleString()} />
            </div>
          ))}
        </Section>
      )}

      {/* Source artefacts */}
      {(trace.swan_transactions.length > 0 || trace.documents.length > 0) && (
        <Section title="Source artefacts">
          {trace.swan_transactions.map((tx) => (
            <SwanCard key={tx.id} tx={tx} />
          ))}
          {trace.documents.map((doc) => (
            <DocCard key={doc.id} doc={doc} />
          ))}
        </Section>
      )}
    </>
  );
}

function CostRow({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between border-b border-border/60 py-1.5 last:border-b-0">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("tabular-nums", mono && "font-mono")}>{v}</span>
    </div>
  );
}

function LineRow({
  line,
  trace,
  agentDecisions,
}: {
  line: EntryTrace["lines"][number];
  trace?: EntryTrace["traces"][number];
  agentDecisions: EntryTrace["agent_decisions"];
}) {
  const [open, setOpen] = useState(false);
  const decision = trace?.agent_decision_id_logical
    ? agentDecisions.find((d) => d.line_id_logical === line.id)
    : null;
  return (
    <>
      <tr
        className="row-hover cursor-pointer border-b border-border/60"
        onClick={() => setOpen((o) => !o)}
      >
        <td className="py-2">
          <div className="flex items-center gap-2">
            {open ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
            <span className="font-mono text-sec">{line.account_code}</span>
            <span className="text-muted-foreground">{line.account_name}</span>
          </div>
        </td>
        <td className="text-right"><Cents cents={line.debit_cents} /></td>
        <td className="text-right"><Cents cents={line.credit_cents} /></td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} className="bg-muted/30 px-3 py-3">
            <div className="space-y-2">
              <div className="text-meta uppercase tracking-wide text-muted-foreground">Decision</div>
              {trace ? (
                <div className="flex flex-wrap items-center gap-3 text-sec">
                  <SourceChip source={trace.source} />
                  {trace.rule_id && (
                    <span className="inline-flex items-center gap-1 font-mono text-meta text-muted-foreground">
                      <Hash className="h-3 w-3" />
                      {trace.rule_id}
                    </span>
                  )}
                  <ConfidenceBar value={trace.confidence} />
                  {decision && (
                    <span className="ml-auto inline-flex items-center gap-1 text-meta text-muted-foreground">
                      <Cpu className="h-3 w-3" />
                      {decision.model}
                    </span>
                  )}
                </div>
              ) : (
                <div className="text-sec text-muted-foreground">No trace recorded.</div>
              )}
              {line.counterparty_name && (
                <div className="text-sec">
                  <span className="text-muted-foreground">Counterparty: </span>
                  {line.counterparty_name}
                </div>
              )}
              <div className="text-sec text-muted-foreground">{line.description}</div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function SourceChip({ source }: { source: string }) {
  const cls = {
    rule: "bg-muted text-foreground/80",
    agent: "bg-primary/10 text-primary",
    cache: "bg-primary/5 text-primary/80",
    human: "bg-warning/10 text-warning",
  }[source] ?? "bg-muted text-foreground/80";
  return (
    <span className={cn("inline-flex items-center rounded-sm px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide", cls)}>
      {source}
    </span>
  );
}

function Alternatives({ json }: { json: string }) {
  const [open, setOpen] = useState(false);
  let alts: Array<{ gl: string; conf: number }> = [];
  try {
    alts = JSON.parse(json);
  } catch {
    return null;
  }
  if (alts.length === 0) return null;
  return (
    <div className="rounded-md border border-border bg-muted/30 p-2.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 text-meta uppercase tracking-wide text-muted-foreground"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Alternatives ({alts.length})
      </button>
      {open && (
        <ul className="mt-2 space-y-1 text-sec">
          {alts.map((a, i) => (
            <li key={i} className="flex items-center justify-between font-mono">
              <span>{a.gl}</span>
              <span className="tabular-nums text-muted-foreground">{a.conf.toFixed(2)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SwanCard({ tx }: { tx: EntryTrace["swan_transactions"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-2 rounded-md border border-border bg-card p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded-sm bg-primary/10 px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide text-primary">
            Swan
          </span>
          <span className="text-sec">{tx.counterparty_label}</span>
        </div>
        <span className="font-mono text-sec tabular-nums"><Money cents={tx.amount_cents} /></span>
      </div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="mt-2 text-meta text-muted-foreground hover:text-foreground"
      >
        {open ? "Hide" : "Show"} raw payload
      </button>
      {open && (
        <pre className="mt-2 max-h-48 overflow-auto rounded-sm bg-muted/40 p-2 font-mono text-meta">
{JSON.stringify(tx.raw_payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

function DocCard({ doc }: { doc: EntryTrace["documents"][number] }) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-muted-foreground" />
        <span className="text-sec">{doc.filename ?? doc.blob_path.split("/").pop()}</span>
        <span className="ml-auto rounded-sm bg-muted px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide text-muted-foreground">
          {doc.kind.replace("_", " ")}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2 font-mono text-meta text-muted-foreground">
        <span>sha256:{doc.sha256.slice(0, 12)}…</span>
        {doc.amount_cents !== null && <span className="ml-auto"><Money cents={doc.amount_cents} /></span>}
      </div>
      <div className="mt-2 flex h-32 items-center justify-center rounded-sm border border-dashed border-border bg-muted/30 text-meta text-muted-foreground">
        <div className="flex flex-col items-center gap-1">
          <AlertTriangle className="h-4 w-4" />
          <span>PDF preview not available in mock mode.</span>
        </div>
      </div>
    </div>
  );
}

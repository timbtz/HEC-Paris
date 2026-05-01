/**
 * NodeTraceDrawer — right-rail panel for a single DAG node.
 *
 * Sections (PRD-AutonomousCFO §7.4): Status & Timing | Inputs | Reasoning |
 * Cost | Wiki Citations. The Reasoning section is the highlight — it surfaces
 * the agent.decision payload (model, runner, provider, prompt_hash,
 * finish_reason, confidence) so a CFO can audit *what reasoning each node
 * did*.
 *
 * The Wiki Citations section is now live (Phase 4.A wiring). The executor
 * resolves `result.wiki_references` against `wiki_pages` + `wiki_revisions`
 * before publishing `agent.decision`, so each citation already carries
 * `path`, `title`, `revision_number`. Clicking a row expands an inline panel
 * that fetches `/wiki/pages/{page_id}/revisions/{revision_id}` for the
 * snapshot body.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Brain, ChevronDown, ChevronRight, Cog, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMicroUsd } from "@/lib/format";
import { apiFetch } from "@/lib/api";
import type { NodeState, WikiCitation } from "@/hooks/useDagRun";

interface WikiRevisionDoc {
  page_id: number;
  revision_id: number;
  revision_number: number;
  path: string;
  title: string;
  body_md: string;
  frontmatter: Record<string, unknown> | null;
  created_at: string | null;
}

interface Props {
  open: boolean;
  node: NodeState | null;
  onClose: () => void;
}

export function NodeTraceDrawer({ open, node, onClose }: Props) {
  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent side="right" className="w-[480px] sm:max-w-[480px]">
        {node ? <NodeTraceBody node={node} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function NodeTraceBody({ node }: { node: NodeState }) {
  const { meta, status, elapsed_ms, error, output, decision } = node;
  const KindIcon = meta.kind === "agent" ? Brain : meta.kind === "condition" ? GitBranch : Cog;

  return (
    <ScrollArea className="-mr-6 h-[calc(100vh-2rem)] pr-6">
      <SheetHeader className="space-y-2 text-left">
        <div className="flex items-center gap-2 text-meta uppercase tracking-wide text-muted-foreground">
          <KindIcon className="h-3.5 w-3.5" />
          {meta.kind}
          <span aria-hidden>·</span>
          <Badge variant="outline" className="font-mono">{status}</Badge>
        </div>
        <SheetTitle className="font-mono text-base">{meta.id}</SheetTitle>
        <SheetDescription className="font-mono text-meta">{meta.ref}</SheetDescription>
      </SheetHeader>

      <Section title="Status & Timing">
        <KV k="status" v={status} mono />
        <KV k="elapsed" v={elapsed_ms !== null ? `${elapsed_ms} ms` : "—"} mono />
        <KV k="depends_on" v={meta.depends_on.length ? meta.depends_on.join(", ") : "—"} mono />
        <KV k="when" v={meta.when ?? "—"} mono />
        <KV k="cacheable" v={String(meta.cacheable)} mono />
        <KV k="layer" v={String(meta.layer_index)} mono />
        {error ? <KV k="error" v={error} mono className="text-destructive" /> : null}
      </Section>

      {meta.kind === "agent" ? (
        <Section title="Reasoning">
          {decision ? (
            <>
              <KV k="model" v={decision.model} mono />
              <KV k="runner" v={decision.runner} mono />
              <KV k="provider" v={decision.provider} mono />
              <KV k="prompt_hash" v={decision.prompt_hash} mono className="break-all" />
              <KV k="finish_reason" v={decision.finish_reason ?? "—"} mono />
              <KV k="confidence" v={decision.confidence !== null ? decision.confidence.toFixed(2) : "—"} mono />
              <KV k="latency" v={`${decision.latency_ms} ms`} mono />
              <KV k="decision_id" v={`#${decision.decision_id}`} mono />
            </>
          ) : (
            <p className="text-sec text-muted-foreground">
              {status === "pending" || status === "running"
                ? "Decision not yet emitted."
                : "No agent.decision event recorded."}
            </p>
          )}
        </Section>
      ) : null}

      {meta.kind === "agent" && decision ? (
        <Section title="Cost">
          <KV k="input_tokens" v={decision.input_tokens.toLocaleString()} mono />
          <KV k="output_tokens" v={decision.output_tokens.toLocaleString()} mono />
          <KV k="cache_read_tokens" v={decision.cache_read_tokens.toLocaleString()} mono />
          <KV k="cache_write_tokens" v={decision.cache_write_tokens.toLocaleString()} mono />
          <KV k="reasoning_tokens" v={decision.reasoning_tokens.toLocaleString()} mono />
          <KV k="cost" v={formatMicroUsd(decision.cost_micro_usd)} mono />
          <KV
            k="cost_micro_usd"
            v={`${decision.cost_micro_usd.toLocaleString()} µ$`}
            mono
            className="text-muted-foreground"
          />
        </Section>
      ) : null}

      <Section title="Inputs / Output">
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-meta">
          {output !== null && output !== undefined
            ? truncateJson(output)
            : status === "pending" || status === "running"
              ? "(awaiting completion)"
              : "(no output captured)"}
        </pre>
      </Section>

      {meta.kind === "agent" ? (
        <Section title="Wiki Citations">
          <WikiCitations citations={decision?.wiki_citations ?? []} />
        </Section>
      ) : null}
    </ScrollArea>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5 space-y-2 border-t border-border pt-4">
      <h3 className="text-meta font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      <div className="space-y-1.5">{children}</div>
    </section>
  );
}

function KV({
  k,
  v,
  mono,
  className,
}: {
  k: string;
  v: string;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sec">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("text-right", mono && "font-mono", className)}>{v}</span>
    </div>
  );
}

function WikiCitations({ citations }: { citations: WikiCitation[] }) {
  if (!citations.length) {
    return (
      <p className="text-sec text-muted-foreground">
        No policy citations for this node.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {citations.map((c, i) => (
        <WikiCitationRow
          key={`${c.page_id}-${c.revision_id}-${i}`}
          citation={c}
        />
      ))}
    </ul>
  );
}

function WikiCitationRow({ citation }: { citation: WikiCitation }) {
  const [open, setOpen] = useState(false);
  const canFetch =
    citation.page_id !== null &&
    citation.page_id !== undefined &&
    citation.revision_id !== null &&
    citation.revision_id !== undefined;

  return (
    <li className="rounded-md border border-border bg-muted/20 text-sec">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          asChild
          disabled={!canFetch}
        >
          <button
            type="button"
            className={cn(
              "flex w-full items-start gap-2 px-2 py-1.5 text-left",
              canFetch
                ? "hover:bg-muted/40 cursor-pointer"
                : "cursor-default opacity-80",
            )}
          >
            <span className="mt-0.5 text-muted-foreground" aria-hidden>
              {open ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </span>
            <span className="flex-1 min-w-0">
              <span className="block font-medium truncate">
                {citation.title ??
                  citation.path ??
                  `page #${citation.page_id ?? "?"}`}
              </span>
              <span className="block font-mono text-meta text-muted-foreground truncate">
                {citation.path ?? "—"}
                {citation.revision_number !== null &&
                citation.revision_number !== undefined
                  ? ` · rev ${citation.revision_number}`
                  : ""}
              </span>
            </span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {canFetch && open ? (
            <WikiCitationBody
              pageId={citation.page_id as number}
              revisionId={citation.revision_id as number}
            />
          ) : null}
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function WikiCitationBody({
  pageId,
  revisionId,
}: {
  pageId: number;
  revisionId: number;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["wiki-revision", pageId, revisionId],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const res = await apiFetch<WikiRevisionDoc>(
        `/wiki/pages/${pageId}/revisions/${revisionId}`,
      );
      return res.data;
    },
  });

  if (isLoading) {
    return (
      <div className="border-t border-border px-2 py-2 text-meta text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div className="border-t border-border px-2 py-2 text-meta text-destructive">
        Failed to load wiki revision.
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="border-t border-border px-2 py-2 space-y-1">
      <div className="font-mono text-meta text-muted-foreground">
        {data.path} · rev {data.revision_number}
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-2 font-mono text-meta">
        {data.body_md}
      </pre>
    </div>
  );
}

function truncateJson(value: unknown, max = 4_000): string {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  if (text.length > max) {
    return `${text.slice(0, max)}\n… (${text.length - max} more chars)`;
  }
  return text;
}

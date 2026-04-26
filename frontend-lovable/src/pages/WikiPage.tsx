/**
 * WikiPage — read-only browser for the Living Rule Wiki.
 *
 * Source: PRD-AutonomousCFO §7.3. The wiki is the agent's prompt input;
 * this page lets the CFO see exactly which markdown the agents are
 * reading, with frontmatter (applies_to / jurisdictions / threshold_eur /
 * agent_input_for / revision / last_audited_*) surfaced as the audit
 * spine. Edit/diff are deferred to a follow-up — Phase 4.A bullet "wiki
 * editor (CodeMirror)" is the next slice.
 *
 * Endpoints used (added in `backend/api/wiki.py`):
 *   GET /wiki/pages
 *   GET /wiki/pages/{page_id}
 *   GET /wiki/pages/{page_id}/revisions
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CheckCircle2, Hash, ScrollText, Tag, Zap } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";

interface WikiPageSummary {
  page_id: number;
  path: string;
  title: string;
  frontmatter: Record<string, unknown> | null;
  updated_at: string | null;
  head_revision_id: number;
  head_revision_number: number;
  head_revision_created_at: string | null;
}

interface WikiPageBody {
  page_id: number;
  revision_id: number;
  revision_number: number;
  path: string;
  title: string;
  body_md: string;
  frontmatter: Record<string, unknown> | null;
  created_at: string | null;
}

interface WikiRevisionSummary {
  revision_id: number;
  revision_number: number;
  author: string | null;
  created_at: string | null;
  parent_revision_id: number | null;
}

interface WikiRatification {
  review_id: number;
  kind: string;
  confidence: number | null;
  reason: string;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: number | null;
}

export default function WikiPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const listQuery = useQuery({
    queryKey: ["wiki", "pages"],
    queryFn: async () => (await apiFetch<{ items: WikiPageSummary[] }>(`/wiki/pages`)).data,
  });

  const items = listQuery.data?.items ?? [];

  // Default-select the first page once the list resolves.
  useEffect(() => {
    if (selectedId === null && items.length > 0) {
      setSelectedId(items[0].page_id);
    }
  }, [selectedId, items]);

  const selectedSummary = items.find((p) => p.page_id === selectedId) ?? null;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      <header className="border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <BookOpen className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Living Rule Wiki</h1>
            <p className="text-sec text-muted-foreground">
              Markdown the agents read verbatim. Every classification cites a (page · revision) pair.
            </p>
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Left rail: ratifications panel + page list */}
        <aside className="w-[300px] shrink-0 border-r border-border bg-sidebar/40">
          <ScrollArea className="h-full">
            <RatificationPanel />
            <div className="space-y-0.5 p-2">
              {listQuery.isLoading ? (
                <div className="px-3 py-6 text-center text-sec text-muted-foreground">
                  Loading pages…
                </div>
              ) : items.length === 0 ? (
                <div className="px-3 py-6 text-center text-sec text-muted-foreground">
                  No wiki pages yet. Run{" "}
                  <code className="font-mono text-meta">
                    uv run python -m backend.scripts.seed_wiki
                  </code>
                  .
                </div>
              ) : (
                items.map((p) => {
                  const active = p.page_id === selectedId;
                  return (
                    <button
                      key={p.page_id}
                      type="button"
                      onClick={() => setSelectedId(p.page_id)}
                      className={cn(
                        "block w-full rounded-md px-2.5 py-2 text-left text-sec transition-colors",
                        active
                          ? "bg-sidebar-accent text-foreground"
                          : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                      )}
                    >
                      <div className="font-medium">{p.title}</div>
                      <div className="font-mono text-meta text-muted-foreground/70 truncate">
                        {p.path}
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-meta text-muted-foreground/70">
                        <Hash className="h-3 w-3" />
                        rev {p.head_revision_number}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </ScrollArea>
        </aside>

        {/* Right pane: page body + revisions */}
        <main className="flex-1 overflow-hidden">
          {selectedSummary ? (
            <WikiPageDetail summary={selectedSummary} />
          ) : (
            <div className="flex h-full items-center justify-center text-sec text-muted-foreground">
              Select a page on the left.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function WikiPageDetail({ summary }: { summary: WikiPageSummary }) {
  const bodyQuery = useQuery({
    queryKey: ["wiki", "page", summary.page_id],
    queryFn: async () =>
      (await apiFetch<WikiPageBody>(`/wiki/pages/${summary.page_id}`)).data,
  });

  const revisionsQuery = useQuery({
    queryKey: ["wiki", "revisions", summary.page_id],
    queryFn: async () =>
      (
        await apiFetch<{ items: WikiRevisionSummary[] }>(
          `/wiki/pages/${summary.page_id}/revisions`,
        )
      ).data,
  });

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto max-w-[920px] px-8 py-6">
        <header className="mb-4">
          <div className="font-mono text-meta uppercase tracking-wide text-muted-foreground">
            {summary.path}
          </div>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight">
            {bodyQuery.data?.title ?? summary.title}
          </h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge variant="outline" className="font-mono">
              rev {summary.head_revision_number}
            </Badge>
            {bodyQuery.data?.created_at ? (
              <Badge variant="outline" className="font-mono text-muted-foreground">
                {bodyQuery.data.created_at}
              </Badge>
            ) : null}
          </div>
        </header>

        {/* Frontmatter card */}
        <FrontmatterCard fm={summary.frontmatter} />

        {/* Body */}
        <section className="mt-5">
          <h3 className="mb-2 flex items-center gap-2 text-meta font-semibold uppercase tracking-wide text-muted-foreground">
            <ScrollText className="h-3.5 w-3.5" />
            Body (read by agents verbatim)
          </h3>
          {bodyQuery.isLoading ? (
            <div className="rounded-md border border-border bg-card px-4 py-6 text-sec text-muted-foreground">
              Loading body…
            </div>
          ) : bodyQuery.error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sec text-destructive">
              Failed to load page body.
            </div>
          ) : (
            <pre className="whitespace-pre-wrap rounded-md border border-border bg-card p-4 font-mono text-sec leading-6">
              {bodyQuery.data?.body_md ?? ""}
            </pre>
          )}
        </section>

        {/* Revisions */}
        <section className="mt-6">
          <h3 className="mb-2 flex items-center gap-2 text-meta font-semibold uppercase tracking-wide text-muted-foreground">
            <Hash className="h-3.5 w-3.5" />
            Revisions
          </h3>
          {revisionsQuery.data?.items.length ? (
            <ul className="rounded-md border border-border bg-card divide-y divide-border">
              {revisionsQuery.data.items.map((r) => (
                <li
                  key={r.revision_id}
                  className="flex items-center justify-between px-3 py-2 text-sec"
                >
                  <div>
                    <span className="font-mono">rev {r.revision_number}</span>
                    {r.author ? (
                      <span className="ml-2 text-muted-foreground">
                        · {r.author}
                      </span>
                    ) : null}
                  </div>
                  <div className="font-mono text-meta text-muted-foreground">
                    {r.created_at ?? "—"}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-sec text-muted-foreground">No revisions.</div>
          )}
        </section>
      </div>
    </ScrollArea>
  );
}

function FrontmatterCard({ fm }: { fm: Record<string, unknown> | null }) {
  if (!fm) return null;
  const appliesTo = (fm.applies_to as string[] | undefined) ?? [];
  const jurisdictions = (fm.jurisdictions as string[] | undefined) ?? null;
  const agentInputFor = (fm.agent_input_for as string[] | undefined) ?? null;
  const thresholdEur = fm.threshold_eur as number | undefined;
  const lastAuditedBy = fm.last_audited_by as string | undefined;
  const lastAuditedAt = fm.last_audited_at as string | undefined;

  return (
    <section className="rounded-md border border-border bg-card p-4">
      <h3 className="mb-2 flex items-center gap-2 text-meta font-semibold uppercase tracking-wide text-muted-foreground">
        <Tag className="h-3.5 w-3.5" />
        Frontmatter
      </h3>
      <dl className="grid grid-cols-1 gap-y-1 text-sec sm:grid-cols-[180px,1fr]">
        <Row k="applies_to">
          <TagList items={appliesTo} />
        </Row>
        {jurisdictions ? (
          <Row k="jurisdictions">
            <TagList items={jurisdictions} />
          </Row>
        ) : null}
        {agentInputFor ? (
          <Row k="agent_input_for">
            <TagList items={agentInputFor} mono />
          </Row>
        ) : null}
        {thresholdEur !== undefined && thresholdEur !== null ? (
          <Row k="threshold_eur">
            <span className="font-mono">€{thresholdEur.toLocaleString()}</span>
          </Row>
        ) : null}
        {lastAuditedBy ? (
          <Row k="last_audited_by">
            <span className="font-mono">{lastAuditedBy}</span>
          </Row>
        ) : null}
        {lastAuditedAt ? (
          <Row k="last_audited_at">
            <span className="font-mono">{lastAuditedAt}</span>
          </Row>
        ) : null}
      </dl>
    </section>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

function TagList({ items, mono }: { items: string[]; mono?: boolean }) {
  if (!items.length) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((t) => (
        <Badge
          key={t}
          variant="secondary"
          className={cn("font-normal", mono && "font-mono")}
        >
          {t}
        </Badge>
      ))}
    </div>
  );
}

// Pending wiki_rule_change ratifications. The post-mortem agent files one
// of these every time it proposes editing a `policies/*` page; the CFO
// closes the loop with a one-click approve. The actual page edit can be
// made via the wiki editor (deferred) or the snapshot view; this panel
// just owns the queue.
function RatificationPanel() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["wiki", "ratifications"],
    queryFn: async () =>
      (await apiFetch<{ items: WikiRatification[] }>(`/wiki/ratifications?status=pending`)).data,
    refetchInterval: 12_000,
  });
  const items = data?.items ?? [];
  const approve = useMutation({
    mutationFn: async (reviewId: number) =>
      (
        await apiFetch<{ review_id: number; status: string }>(
          `/wiki/ratifications/${reviewId}/approve`,
          { method: "POST", headers: { "x-agnes-author": "tim@hec.example" } },
        )
      ).data,
    onSuccess: (resp) => {
      toast({
        title: `Ratified rule change #${resp.review_id}`,
        description: "The post-mortem agent's proposal is signed off.",
      });
      qc.invalidateQueries({ queryKey: ["wiki", "ratifications"] });
    },
    onError: (e: unknown) => {
      toast({
        title: "Failed to ratify",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    },
  });

  if (isLoading) return null;
  if (items.length === 0) return null;
  return (
    <div className="border-b border-border bg-prism-soft/40 p-3">
      <div className="mb-2 flex items-center gap-2 text-meta font-semibold uppercase tracking-wide text-primary">
        <Zap className="h-3.5 w-3.5" />
        Pending CFO ratifications · {items.length}
      </div>
      <ul className="space-y-2">
        {items.map((r) => {
          const target = (r.reason.match(/target=([^;]+);/) ?? [])[1]?.trim() ?? "—";
          const title = (r.reason.match(/title='([^']+)'/) ?? [])[1] ?? r.reason.slice(0, 80);
          const runMatch = r.reason.match(/run (\d+)/);
          return (
            <li key={r.review_id} className="rounded-md border border-border bg-card p-2.5">
              <div className="font-medium text-sec leading-snug">{title}</div>
              <div className="mt-0.5 font-mono text-meta text-muted-foreground truncate">
                {target}
                {runMatch ? <span className="ml-1.5">· run #{runMatch[1]}</span> : null}
              </div>
              <Button
                size="sm"
                className="mt-2 w-full"
                disabled={approve.isPending}
                onClick={() => approve.mutate(r.review_id)}
              >
                <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
                {approve.isPending ? "Ratifying…" : "Ratify"}
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

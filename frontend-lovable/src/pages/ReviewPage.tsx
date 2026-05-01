/**
 * ReviewPage — entries the agents flagged for human eyes.
 *
 * Surfaces every journal entry currently in `status='review'`. Clicking a row
 * opens the existing trace drawer (which already has an Approve button when
 * the entry is in review state — see `TraceDrawer.tsx::onApprove`).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Inbox, RefreshCw } from "lucide-react";
import { fetchJournalEntries } from "@/lib/endpoints";
import { useTraceDrawer } from "@/components/fingent/TraceDrawerContext";
import { Money, RelTime, EmptyState } from "@/components/fingent/primitives";
import { Button } from "@/components/ui/button";

export default function ReviewPage() {
  const queryClient = useQueryClient();
  const { open } = useTraceDrawer();

  const reviewQuery = useQuery({
    queryKey: ["journal_entries", "review"],
    staleTime: 5_000,
    queryFn: async () => (await fetchJournalEntries({ status: "review", limit: 100 })).data,
  });

  const items = reviewQuery.data?.items ?? [];

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-6">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Review</h1>
          <p className="text-sec text-muted-foreground">
            Entries the agents flagged for human eyes — confidence too low,
            invariant tripped, or routing rule disagreed with the model.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => queryClient.invalidateQueries({ queryKey: ["journal_entries", "review"] })}
        >
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </header>

      {reviewQuery.isLoading ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center text-sec text-muted-foreground">
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-8">
          <EmptyState
            icon={Inbox}
            title="Nothing to review"
            hint="The agents posted everything cleanly — no entries flagged for human approval."
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <table className="w-full text-sec">
            <thead className="bg-muted/40 text-meta uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">Entry</th>
                <th className="px-3 py-2 text-left">Description</th>
                <th className="px-3 py-2 text-left">Pipeline</th>
                <th className="px-3 py-2 text-right">Amount</th>
                <th className="px-3 py-2 text-left">Date</th>
                <th className="px-3 py-2 text-left">Created</th>
                <th className="w-8 px-3" />
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => open(e.id)}
                  className="cursor-pointer border-t border-border hover:bg-accent/30"
                >
                  <td className="px-3 py-2 font-mono">#{e.id}</td>
                  <td className="px-3 py-2">{e.description ?? "—"}</td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">
                    {e.source_pipeline ?? "—"}
                    {e.source_run_id !== null ? ` · #${e.source_run_id}` : ""}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    <Money cents={e.total_cents} />
                  </td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">{e.entry_date}</td>
                  <td className="px-3 py-2">
                    <RelTime iso={e.created_at} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

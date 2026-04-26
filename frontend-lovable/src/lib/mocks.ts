import type {
  AiSpendToday,
  EntryTrace,
  EnvelopesResponse,
  JournalEntriesResponse,
  JournalEntrySummary,
  RunSummary,
} from "./types";
import { AI_CALLS, CATEGORIES, CATEGORY_BY_KEY, SPEND_TX, currentPeriod, monthlyTotals } from "./dataset";
import { EMPLOYEES, employeeById } from "./employees";

// Re-exported so existing `import { EMPLOYEES } from "./mocks"` call sites keep working.
export { EMPLOYEES };

// ----- Journal entries (now sourced from the coherent dataset) ---------------

function txToEntry(t: typeof SPEND_TX[number]): JournalEntrySummary {
  return {
    id: t.id,
    basis: "accrual",
    entry_date: t.date,
    description: t.description,
    status: t.status,
    source_pipeline: t.pipeline,
    source_run_id: t.source_run_id,
    accrual_link_id: null,
    reversal_of_id: null,
    created_at: t.iso,
    total_cents: t.amount_cents,
    line_count: 2,
    employee_first_name: t.employee_id ? employeeById(t.employee_id)?.first_name ?? null : null,
    confidence: t.confidence,
    review_reason: t.review_reason ?? null,
  };
}

export const JOURNAL_ENTRIES: JournalEntrySummary[] = SPEND_TX.map(txToEntry);

export function listJournalEntries(params: { limit?: number; offset?: number; status?: string } = {}): JournalEntriesResponse {
  const { limit = 50, offset = 0, status } = params;
  let items = JOURNAL_ENTRIES;
  if (status && status !== "all") items = items.filter((e) => e.status === status);
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    limit,
    offset,
  };
}

// ----- Envelopes -------------------------------------------------------------

/**
 * Build envelopes from the categories × employees grid.
 * For each (employee, category) where the employee actually spent in this period,
 * issue an envelope with the canonical cap and the actual usage.
 */
export function listEnvelopes(params: { period?: string; employee_id?: number } = {}): EnvelopesResponse {
  const period = params.period ?? currentPeriod();
  const items = [];
  let id = 10;
  for (const emp of EMPLOYEES) {
    for (const cat of CATEGORIES) {
      const used = SPEND_TX.filter(
        (t) => t.employee_id === emp.id && t.category_key === cat.key && t.date.startsWith(period),
      ).reduce((a, b) => a + b.amount_cents, 0);
      const count = SPEND_TX.filter(
        (t) => t.employee_id === emp.id && t.category_key === cat.key && t.date.startsWith(period),
      ).length;
      if (used === 0) continue;
      items.push({
        id: id++,
        scope_kind: "employee" as const,
        scope_id: emp.id,
        category: cat.key,
        period,
        cap_cents: cat.cap_cents,
        soft_threshold_pct: 80,
        used_cents: used,
        allocation_count: count,
        employee_first_name: emp.first_name,
      });
    }
  }
  if (params.employee_id) return { items: items.filter((e) => e.scope_id === params.employee_id) };
  return { items };
}

// ----- AI spend today --------------------------------------------------------
export function aiSpendToday(): AiSpendToday {
  const today = new Date();
  const series_14d: Array<{ date: string; cost_micro_usd: number }> = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today.getTime() - i * 86400_000).toISOString().slice(0, 10);
    const total = AI_CALLS.filter((c) => c.date === d).reduce((a, b) => a + b.cost_micro_usd, 0);
    series_14d.push({ date: d, cost_micro_usd: total });
  }
  return {
    total_today_micro_usd: series_14d[series_14d.length - 1].cost_micro_usd,
    series_14d,
  };
}

// ----- Recent runs -----------------------------------------------------------
export function listRuns(limit = 8): { items: RunSummary[]; total: number } {
  const items: RunSummary[] = [];
  const recent = SPEND_TX.slice(0, limit);
  for (let i = 0; i < recent.length; i++) {
    const t = recent[i];
    items.push({
      id: t.source_run_id,
      pipeline_name: t.pipeline,
      status: t.status === "review" ? "success" : "success",
      started_at: t.iso,
      elapsed_ms: 800 + Math.round(Math.random() * 8000),
      agent_cost_micro_usd: Math.round(2000 + Math.random() * 18000),
      review_count: t.status === "review" ? 1 : 0,
    });
  }
  return { items, total: 312 };
}

// ----- Entry trace -----------------------------------------------------------
export function entryTrace(entryId: number): EntryTrace {
  const entry = JOURNAL_ENTRIES.find((e) => e.id === entryId) ?? JOURNAL_ENTRIES[0];
  const tx = SPEND_TX.find((t) => t.id === entry.id);
  const total = entry.total_cents;
  const cat = tx ? CATEGORY_BY_KEY[tx.category_key] : undefined;
  const counterparty = tx?.vendor ?? entry.description.split(" — ")[0];

  const lines = [
    {
      id: entryId * 10 + 1,
      entry_id: entry.id,
      account_code: cat?.account_code ?? "613500",
      account_name: cat?.label ?? "Other external services",
      debit_cents: total,
      credit_cents: 0,
      counterparty_id: 14,
      counterparty_name: counterparty,
      swan_transaction_id: null,
      document_id: 81,
      description: entry.description,
    },
    {
      id: entryId * 10 + 2,
      entry_id: entry.id,
      account_code: "401",
      account_name: "Suppliers",
      debit_cents: 0,
      credit_cents: total,
      counterparty_id: 14,
      counterparty_name: counterparty,
      swan_transaction_id: 9001,
      document_id: null,
      description: "Supplier payable",
    },
  ];

  const conf = entry.confidence ?? 0.92;
  const isAgent = conf < 0.99;
  const traces = lines.map((l, idx) => ({
    id: l.id + 1000,
    line_id: l.id,
    source: (isAgent ? (idx === 0 ? "agent" : "rule") : "rule") as "agent" | "rule",
    rule_id: idx === 1 ? "fr-pcg.401-supplier-default" : null,
    confidence: idx === 0 ? conf : 1.0,
    agent_decision_id_logical: idx === 0 ? `dec_${entryId}_a3f8c2` : null,
    approver_id: entry.status === "review" ? null : 1,
    approved_at: entry.status === "review" ? null : entry.created_at,
  }));

  const agent_decisions = isAgent
    ? [
        {
          id: 1,
          run_id_logical: entry.source_run_id ?? 0,
          node_id: "ai-account-fallback",
          source: "agent" as const,
          runner: "anthropic",
          model: "claude-sonnet-4-6",
          response_id: "msg_01HX8Z7BVPC9YQK2N3JFGT4WMP",
          prompt_hash: "a3f8c241b2",
          alternatives_json: JSON.stringify([
            { gl: "626700", conf: 0.07 },
            { gl: "606300", conf: 0.01 },
          ]),
          confidence: conf,
          line_id_logical: lines[0].id,
          latency_ms: 1810,
          finish_reason: "tool_use",
          temperature: 0.0,
        },
      ]
    : [];

  const agent_costs = isAgent
    ? [
        {
          decision_id: 1,
          employee_id: tx?.employee_id ?? null,
          provider: "anthropic",
          model: "claude-sonnet-4-6",
          input_tokens: 4210,
          output_tokens: 142,
          cache_read_tokens: 1820,
          cache_write_tokens: 0,
          reasoning_tokens: 0,
          cost_micro_usd: 4310,
        },
      ]
    : [];

  return {
    entry,
    lines,
    traces,
    agent_decisions,
    agent_costs,
    source_run: entry.source_run_id
      ? {
          id: entry.source_run_id,
          pipeline_name: entry.source_pipeline ?? "transaction_booked",
          status: "success",
          started_at: entry.created_at,
          elapsed_ms: 4820,
        }
      : null,
    swan_transactions: [
      {
        id: 9001,
        amount_cents: total,
        side: "debit",
        counterparty_label: counterparty,
        posted_at: entry.created_at,
        raw_payload: {
          swan_transaction_id: "tx_01HX8Z7BVPC9YQK2N3JFGT4WMP",
          counterparty_iban: "FR76 3000 6000 0112 3456 7890 189",
          reference: entry.description,
          posted_at: entry.created_at,
          original_amount: { value: total / 100, currency: "EUR" },
        },
      },
    ],
    documents: [
      {
        id: 81,
        sha256: "9f3a1c2b4e6d7f8901a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4",
        kind: "invoice_in",
        amount_cents: total,
        blob_path: "data/blobs/2026/04/inv-9f3a.pdf",
        filename: `${counterparty.toLowerCase().replace(/\s+/g, "-")}.pdf`,
      },
    ],
  };
}

// Used by /me — silence unused warning
export { monthlyTotals };

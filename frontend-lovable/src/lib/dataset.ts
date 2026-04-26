/**
 * Coherent mock dataset.
 *
 * 90 days of synthetic transactions across 8 employees and 7 spend categories.
 * Same dataset feeds /me, /reports, /budgets, /ai-spend and the ledger pulse —
 * so numbers reconcile when the user drills between pages.
 *
 * Deterministic seed: same dataset on every reload.
 */

import { EMPLOYEES } from "./employees";
import type { Employee } from "./types";

// ----- Categories ------------------------------------------------------------

export interface CategoryDef {
  key: string;          // canonical key, e.g. "benefit.jobrad"
  label: string;        // human label
  short: string;        // short label for chips
  account_code: string; // FR PCG-style GL
  cap_cents: number;    // monthly per-employee cap
  cadence: "monthly" | "weekly" | "sporadic" | "daily";
  amount_lo: number;    // typical amount range (cents)
  amount_hi: number;
  pct_employees: number; // share of employees who use it
  description: (emp: Employee, when: Date) => string;
  vendors: string[];
}

const monthShort = (d: Date) => d.toLocaleDateString("en-US", { month: "long" });

export const CATEGORIES: CategoryDef[] = [
  {
    key: "benefit.jobrad",
    label: "JobRad bike lease",
    short: "JobRad",
    account_code: "613500",
    cap_cents: 12_000,
    cadence: "monthly",
    amount_lo: 7_900,
    amount_hi: 11_900,
    pct_employees: 0.7,
    vendors: ["JobRad GmbH"],
    description: (emp, when) => `JobRad lease — ${emp.first_name} — ${monthShort(when)}`,
  },
  {
    key: "benefit.wellpass",
    label: "Wellpass benefits",
    short: "Wellpass",
    account_code: "645200",
    cap_cents: 4_500,
    cadence: "monthly",
    amount_lo: 4_500,
    amount_hi: 4_500,
    pct_employees: 0.85,
    vendors: ["Wellpass"],
    description: (emp, when) => `Wellpass — ${emp.first_name} — ${monthShort(when)}`,
  },
  {
    key: "expense.dinners",
    label: "Client & team dinners",
    short: "Dinners",
    account_code: "625100",
    cap_cents: 60_000,
    cadence: "sporadic",
    amount_lo: 6_400,
    amount_hi: 24_800,
    pct_employees: 0.55,
    vendors: ["Bistro Le Voltaire", "Mama San", "Borchardt", "Septime", "Le Cinq", "Nobelhart & Schmutzig", "L'Avant Comptoir"],
    description: (emp) => `Client dinner — ${emp.first_name}`,
  },
  {
    key: "expense.travel",
    label: "Travel",
    short: "Travel",
    account_code: "625700",
    cap_cents: 120_000,
    cadence: "sporadic",
    amount_lo: 8_900,
    amount_hi: 78_400,
    pct_employees: 0.6,
    vendors: ["Lufthansa", "SNCF", "Eurostar", "Deutsche Bahn", "Hotel Adlon", "Le Pigalle", "25hours Hotel"],
    description: (emp) => `Travel — ${emp.first_name}`,
  },
  {
    key: "saas.api",
    label: "API & infra",
    short: "API",
    account_code: "613600",
    cap_cents: 800_000,
    cadence: "weekly",
    amount_lo: 18_400,
    amount_hi: 142_800,
    pct_employees: 0.4,
    vendors: ["AWS", "Anthropic", "OpenAI", "Cloudflare", "Hetzner", "Vercel", "Stripe API"],
    description: (emp, when) => `${monthShort(when)} usage`,
  },
  {
    key: "saas.tooling",
    label: "SaaS tooling",
    short: "SaaS",
    account_code: "613700",
    cap_cents: 80_000,
    cadence: "monthly",
    amount_lo: 1_200,
    amount_hi: 32_000,
    pct_employees: 0.5,
    vendors: ["Linear", "Notion", "Slack", "Figma", "GitHub", "Datadog", "Sentry", "1Password"],
    description: (emp, when) => `${monthShort(when)} subscription`,
  },
  {
    key: "benefit.finn",
    label: "Finn vehicle lease",
    short: "Finn",
    account_code: "613100",
    cap_cents: 90_000,
    cadence: "monthly",
    amount_lo: 49_900,
    amount_hi: 78_900,
    pct_employees: 0.25,
    vendors: ["Finn"],
    description: (emp, when) => `Finn lease — ${emp.first_name} — ${monthShort(when)}`,
  },
];

export const CATEGORY_BY_KEY: Record<string, CategoryDef> = Object.fromEntries(
  CATEGORIES.map((c) => [c.key, c]),
);

// ----- Deterministic RNG -----------------------------------------------------

function mulberry32(seed: number) {
  return function () {
    let t = (seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rand = mulberry32(20260425); // deterministic per build

function pick<T>(arr: T[]): T {
  return arr[Math.floor(rand() * arr.length)];
}

function randAmount(lo: number, hi: number) {
  return Math.round((lo + rand() * (hi - lo)) / 50) * 50;
}

// ----- Generate transactions -------------------------------------------------

export interface SpendTransaction {
  id: number;
  date: string;          // YYYY-MM-DD
  iso: string;           // ISO timestamp
  employee_id: number | null;
  category_key: string;
  vendor: string;
  description: string;
  amount_cents: number;
  account_code: string;
  source_run_id: number;
  pipeline: string;
  status: "posted" | "review";
  confidence: number;
  review_reason?: string;
}

const DAYS_BACK = 90;
let nextId = 100;

function generateAll(): SpendTransaction[] {
  const out: SpendTransaction[] = [];
  const now = new Date();
  // Strip to midnight UTC
  const today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));

  for (let dayOffset = DAYS_BACK; dayOffset >= 0; dayOffset--) {
    const date = new Date(today.getTime() - dayOffset * 86400_000);
    const dayOfMonth = date.getUTCDate();

    for (const cat of CATEGORIES) {
      // Decide which employees use this category
      const participants = EMPLOYEES.filter(() => rand() < cat.pct_employees);

      if (cat.cadence === "monthly") {
        // Fire on the 5th of each month for each participant
        if (dayOfMonth !== 5) continue;
        for (const emp of participants) {
          out.push(makeTx(cat, emp, date));
        }
      } else if (cat.cadence === "weekly") {
        // Fire weekly per category — not per employee — these are infra invoices
        if (date.getUTCDay() !== 1) continue;
        for (const v of cat.vendors.slice(0, 3)) {
          const tx = makeTx(cat, null, date, v);
          out.push(tx);
        }
      } else if (cat.cadence === "sporadic") {
        // ~30% chance per participant per day
        for (const emp of participants) {
          if (rand() > 0.07) continue;
          out.push(makeTx(cat, emp, date));
        }
      } else if (cat.cadence === "daily") {
        for (const emp of participants) {
          if (rand() > 0.4) continue;
          out.push(makeTx(cat, emp, date));
        }
      }
    }
  }

  return out.sort((a, b) => (a.iso < b.iso ? 1 : -1));
}

function makeTx(cat: CategoryDef, emp: Employee | null, date: Date, vendorOverride?: string): SpendTransaction {
  const id = nextId++;
  const vendor = vendorOverride ?? pick(cat.vendors);
  const amount = randAmount(cat.amount_lo, cat.amount_hi);
  const confidence = 0.6 + rand() * 0.4;
  const flagged = confidence < 0.78 || amount > cat.cap_cents * 1.5;
  // Offset by some random hours within the day so timestamps are realistic
  const hours = Math.floor(rand() * 11) + 8;
  const minutes = Math.floor(rand() * 60);
  const iso = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), hours, minutes)).toISOString();

  const desc = emp ? `${vendor} — ${cat.short} — ${emp.first_name}` : `${vendor} — ${cat.short}`;

  return {
    id,
    date: iso.slice(0, 10),
    iso,
    employee_id: emp?.id ?? null,
    category_key: cat.key,
    vendor,
    description: desc,
    amount_cents: amount,
    account_code: cat.account_code,
    source_run_id: 1000 + id,
    pipeline: "transaction_booked",
    status: flagged ? "review" : "posted",
    confidence,
    review_reason: flagged
      ? amount > cat.cap_cents * 1.5
        ? `Amount exceeds typical cap for ${cat.label}.`
        : `Low classification confidence (${confidence.toFixed(2)}) — manual review requested.`
      : undefined,
  };
}

export const SPEND_TX = generateAll();

// ----- Aggregations ----------------------------------------------------------

export function txByEmployee(employeeId: number) {
  return SPEND_TX.filter((t) => t.employee_id === employeeId);
}

export function txByCategoryAndEmployee(employeeId: number) {
  const byCat: Record<string, SpendTransaction[]> = {};
  for (const t of SPEND_TX) {
    if (t.employee_id !== employeeId) continue;
    (byCat[t.category_key] ??= []).push(t);
  }
  return byCat;
}

export function periodKey(date: Date | string): string {
  const d = typeof date === "string" ? new Date(date) : date;
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/**
 * Monthly totals per category for a given employee, last N months.
 * Returns an array of months oldest → newest with each category total.
 */
export function monthlyTotals(employeeId: number | null, months = 6) {
  const buckets: Record<string, Record<string, number>> = {};
  const now = new Date();
  const orderedPeriods: string[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    orderedPeriods.push(periodKey(d));
  }
  for (const p of orderedPeriods) buckets[p] = {};

  for (const t of SPEND_TX) {
    if (employeeId !== null && t.employee_id !== employeeId) continue;
    const p = periodKey(t.date);
    if (!buckets[p]) continue;
    buckets[p][t.category_key] = (buckets[p][t.category_key] ?? 0) + t.amount_cents;
  }

  return orderedPeriods.map((p) => ({
    period: p,
    totals: buckets[p],
    total: Object.values(buckets[p]).reduce((a, b) => a + b, 0),
  }));
}

export function categoryTotals(employeeId: number | null, period?: string) {
  const out: Record<string, { total_cents: number; count: number }> = {};
  for (const c of CATEGORIES) out[c.key] = { total_cents: 0, count: 0 };
  for (const t of SPEND_TX) {
    if (employeeId !== null && t.employee_id !== employeeId) continue;
    if (period && periodKey(t.date) !== period) continue;
    if (!out[t.category_key]) continue;
    out[t.category_key].total_cents += t.amount_cents;
    out[t.category_key].count += 1;
  }
  return out;
}

export function currentPeriod(): string {
  const d = new Date();
  return periodKey(d);
}

// ----- AI spend (a slice of saas.api filtered to AI vendors) -----------------

const AI_VENDORS = new Set(["Anthropic", "OpenAI"]);
const AI_MODELS = [
  { vendor: "Anthropic", model: "claude-sonnet-4-6", cost_per_input_micro: 3, cost_per_output_micro: 15 },
  { vendor: "Anthropic", model: "claude-haiku-4", cost_per_input_micro: 1, cost_per_output_micro: 4 },
  { vendor: "OpenAI", model: "gpt-5", cost_per_input_micro: 4, cost_per_output_micro: 18 },
  { vendor: "OpenAI", model: "gpt-5-mini", cost_per_input_micro: 1, cost_per_output_micro: 4 },
];

const PIPELINES = [
  "transaction_booked",
  "document_ingested",
  "envelope_check",
  "period_close",
  "vat_return",
  "anomaly_detection",
];

export interface AiCallRecord {
  iso: string;
  date: string;
  employee_id: number | null;
  vendor: string;
  model: string;
  pipeline: string;
  api_key_label: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  latency_ms: number;
  cost_micro_usd: number;
}

const API_KEYS = ["prod-anthropic-01", "prod-anthropic-02", "prod-openai-01", "dev-shared"];

function generateAiCalls(): AiCallRecord[] {
  const out: AiCallRecord[] = [];
  const now = new Date();
  const today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const aiRand = mulberry32(20260499);

  for (let d = DAYS_BACK; d >= 0; d--) {
    const date = new Date(today.getTime() - d * 86400_000);
    // ~80–280 calls per day
    const count = 80 + Math.floor(aiRand() * 200);
    for (let i = 0; i < count; i++) {
      const m = AI_MODELS[Math.floor(aiRand() * AI_MODELS.length)];
      const emp = aiRand() < 0.7 ? EMPLOYEES[Math.floor(aiRand() * EMPLOYEES.length)] : null;
      const pipeline = PIPELINES[Math.floor(aiRand() * PIPELINES.length)];
      const apiKey = m.vendor === "Anthropic"
        ? (aiRand() < 0.5 ? "prod-anthropic-01" : "prod-anthropic-02")
        : "prod-openai-01";
      const input = 800 + Math.floor(aiRand() * 9200);
      const output = 40 + Math.floor(aiRand() * 600);
      const cacheRead = Math.floor(input * (aiRand() * 0.6));
      const cost = input * m.cost_per_input_micro + output * m.cost_per_output_micro - cacheRead * (m.cost_per_input_micro * 0.7);
      const hours = Math.floor(aiRand() * 24);
      const minutes = Math.floor(aiRand() * 60);
      const iso = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), hours, minutes)).toISOString();
      out.push({
        iso,
        date: iso.slice(0, 10),
        employee_id: emp?.id ?? null,
        vendor: m.vendor,
        model: m.model,
        pipeline,
        api_key_label: apiKey,
        input_tokens: input,
        output_tokens: output,
        cache_read_tokens: cacheRead,
        latency_ms: 800 + Math.floor(aiRand() * 4200),
        cost_micro_usd: Math.max(0, Math.round(cost)),
      });
    }
  }
  return out;
}

export const AI_CALLS = generateAiCalls();

export function aiSpendByPeriod(period: string) {
  return AI_CALLS.filter((c) => periodKey(c.date) === period);
}

export function aiSpendToday() {
  const today = new Date().toISOString().slice(0, 10);
  return AI_CALLS.filter((c) => c.date === today).reduce((a, c) => a + c.cost_micro_usd, 0);
}

/** Suppress unused-vendor warning while keeping the constant available. */
export const __AI_VENDORS_HINT = AI_VENDORS;
export const __API_KEYS_HINT = API_KEYS;

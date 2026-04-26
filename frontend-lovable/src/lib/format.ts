// Locale-aware formatters. Money is integer cents EUR. Cost is integer micro-USD.

const EUR = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const EUR_THOUSANDS = new Intl.NumberFormat("fr-FR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatEur(cents: number, opts: { signed?: boolean } = {}): string {
  const v = cents / 100;
  if (opts.signed && v > 0) return `+${EUR.format(v)}`;
  return EUR.format(v);
}

export function formatEurPlain(cents: number): string {
  return EUR_THOUSANDS.format(cents / 100);
}

export function formatMicroUsd(microUsd: number | null | undefined): string {
  if (microUsd === null || microUsd === undefined || Number.isNaN(microUsd)) return "—";
  const usd = microUsd / 1_000_000;
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(2)}`;
  if (usd < 1000) return `$${usd.toFixed(2)}`;
  if (usd < 1_000_000) return `$${(usd / 1000).toFixed(2)}k`;
  return `$${(usd / 1_000_000).toFixed(2)}M`;
}

export function formatRelTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h < 24) return remM ? `${h}h ${remM}min` : `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatAbsoluteDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export function formatDate(iso: string): string {
  return new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

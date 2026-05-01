import { cn } from "@/lib/utils";
import { formatEur, formatEurPlain, formatMicroUsd, formatRelTime, formatAbsoluteDateTime } from "@/lib/format";
import { useEffect, useState } from "react";

export function Money({
  cents,
  signed = false,
  mutedZero = false,
  className,
}: {
  cents: number;
  signed?: boolean;
  mutedZero?: boolean;
  className?: string;
}) {
  const isZero = cents === 0;
  const isPositive = cents > 0;
  return (
    <span
      data-tnum
      className={cn(
        "tabular-nums",
        isZero && mutedZero && "text-muted-foreground",
        signed && isPositive && "text-primary",
        className,
      )}
    >
      {formatEur(cents, { signed })}
    </span>
  );
}

/** Plain decimal (no currency symbol), for dense ledger columns. */
export function Cents({ cents, mutedZero = true, className }: { cents: number; mutedZero?: boolean; className?: string }) {
  if (cents === 0) {
    return <span className={cn("tabular-nums", mutedZero && "text-muted-foreground/50", className)}>—</span>;
  }
  return <span data-tnum className={cn("tabular-nums", className)}>{formatEurPlain(cents)}</span>;
}

export function MicroUsd({ value, className }: { value: number | null | undefined; className?: string }) {
  return <span data-tnum className={cn("tabular-nums font-mono text-sec", className)}>{formatMicroUsd(value)}</span>;
}

export function RelTime({ iso, className }: { iso: string; className?: string }) {
  // Re-render every 30s so "X min ago" stays fresh
  const [, force] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => force((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, []);
  return (
    <span title={formatAbsoluteDateTime(iso)} className={cn("text-muted-foreground", className)}>
      {formatRelTime(iso)}
    </span>
  );
}

export function LiveDot({ status }: { status: "connecting" | "connected" | "reconnecting" | "offline" }) {
  const cls =
    status === "connected"
      ? "bg-primary animate-live-pulse"
      : status === "reconnecting" || status === "connecting"
        ? "bg-warning"
        : "bg-foreground/20";
  const label =
    status === "connected" ? "Live" : status === "reconnecting" ? "Reconnecting…" : status === "connecting" ? "Connecting…" : "Offline";
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-muted-foreground">
      <span className={cn("h-1.5 w-1.5 rounded-full", cls)} />
      <span className="uppercase tracking-wide">{label}</span>
    </span>
  );
}

export function NodeBadge({ kind }: { kind: "tool" | "agent" | "condition" }) {
  const map: Record<string, string> = {
    tool: "bg-muted text-foreground/80",
    agent: "bg-primary/10 text-primary",
    condition: "bg-warning/10 text-warning",
  };
  return (
    <span className={cn("inline-flex items-center rounded-sm px-1.5 py-0.5 text-meta font-medium", map[kind])}>
      {kind}
    </span>
  );
}

export function ConfidenceBar({ value, floor = 0.85 }: { value: number; floor?: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const ok = value >= floor;
  return (
    <div className="inline-flex items-center gap-2">
      <div className="relative h-1.5 w-24 overflow-hidden rounded-sm bg-muted">
        <div
          className={cn("absolute left-0 top-0 h-full", ok ? "bg-primary" : "bg-warning")}
          style={{ width: `${pct}%` }}
        />
        <div className="absolute top-0 h-full w-px bg-foreground/30" style={{ left: `${floor * 100}%` }} />
      </div>
      <span className="font-mono text-meta tabular-nums text-muted-foreground">{value.toFixed(2)}</span>
    </div>
  );
}

export function StatusBadge({ status }: { status: "posted" | "review" | "draft" | "reversed" }) {
  const cls = {
    posted: "bg-primary/10 text-primary",
    review: "bg-warning/10 text-warning",
    draft: "bg-muted text-muted-foreground",
    reversed: "bg-destructive/10 text-destructive",
  }[status];
  return (
    <span className={cn("inline-flex items-center rounded-sm px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide", cls)}>
      {status}
    </span>
  );
}

export function MockedChip() {
  if (!import.meta.env.DEV) return null;
  return (
    <span
      title="Reading from mocks/*.ts (no backend)"
      className="inline-flex items-center rounded-sm bg-warning/10 px-1.5 py-0.5 text-meta font-medium uppercase tracking-wide text-warning"
    >
      mocked
    </span>
  );
}

export function KeyValue({ rows }: { rows: Array<{ k: string; v: React.ReactNode }> }) {
  return (
    <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-sec">
      {rows.map((r, i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{r.k}</dt>
          <dd className="text-foreground">{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  hint,
  cta,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  hint: string;
  cta?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-card/50 p-12 text-center">
      <Icon className="h-8 w-8 text-muted-foreground/60" />
      <div>
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="mt-1 text-sec text-muted-foreground">{hint}</div>
      </div>
      {cta}
    </div>
  );
}

export function SkeletonRow({ cols = 6 }: { cols?: number }) {
  return (
    <tr className="border-b border-border">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="h-9 px-3">
          <div className="h-3 w-3/4 animate-pulse rounded bg-muted" />
        </td>
      ))}
    </tr>
  );
}

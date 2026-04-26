import { Construction } from "lucide-react";
import { MockedChip } from "@/components/agnes/primitives";

export function ComingSoonPage({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sec text-muted-foreground">{hint}</p>
        </div>
        <MockedChip />
      </div>
      <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-card/50 px-8 py-24 text-center">
        <Construction className="h-8 w-8 text-muted-foreground/60" />
        <div>
          <div className="text-sm font-medium">Shipping next.</div>
          <div className="mt-1 max-w-md text-sec text-muted-foreground">
            This route is part of Agnes&apos;s nine-route surface. Today, Ledger and the trace
            drawer are the foundation — the rest plug in as the backend lands.
          </div>
        </div>
      </div>
    </div>
  );
}

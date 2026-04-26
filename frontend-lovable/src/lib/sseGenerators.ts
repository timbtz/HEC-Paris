import type { SseEvent } from "@/lib/types";

/**
 * Dashboard event generator for mock mode. Emits a steady trickle of
 * envelope decrements + an occasional new posting so the live dot has
 * something to do.
 */
export function dashboardMockGenerator(
  envelopeIds: () => number[],
): (emit: (e: SseEvent) => void) => () => void {
  return (emit) => {
    const interval = window.setInterval(() => {
      const ids = envelopeIds();
      if (ids.length === 0) return;
      const id = ids[Math.floor(Math.random() * ids.length)];
      emit({
        event_type: "envelope.decremented",
        envelope_id: id,
        amount_cents: Math.round(50 + Math.random() * 450) * 100,
        line_id: Math.round(Math.random() * 9999),
      });
    }, 12_000);

    return () => window.clearInterval(interval);
  };
}

import { create } from "zustand";
import type { Envelope, JournalEntrySummary, SseEvent } from "@/lib/types";

type ConnState = "connecting" | "connected" | "reconnecting" | "offline";

interface DashboardState {
  // Live ledger feed (capped at 200, newest-first)
  entries: JournalEntrySummary[];
  entriesById: Record<number, JournalEntrySummary>;
  // Envelopes keyed by id
  envelopes: Record<number, Envelope>;
  // Review queue ids
  reviewIds: Set<number>;
  // SSE connection state
  conn: ConnState;
  mocked: boolean;

  setEntries: (items: JournalEntrySummary[]) => void;
  setEnvelopes: (items: Envelope[]) => void;
  upsertEntry: (e: JournalEntrySummary) => void;
  removeEntry: (id: number) => void;
  setConn: (c: ConnState) => void;
  setMocked: (m: boolean) => void;
  applySseEvent: (e: SseEvent) => void;
}

const MAX_ENTRIES = 200;

export const useDashboard = create<DashboardState>((set, get) => ({
  entries: [],
  entriesById: {},
  envelopes: {},
  reviewIds: new Set(),
  conn: "connecting",
  mocked: false,

  setEntries: (items) =>
    set({
      entries: items,
      entriesById: Object.fromEntries(items.map((e) => [e.id, e])),
      reviewIds: new Set(items.filter((e) => e.status === "review").map((e) => e.id)),
    }),

  setEnvelopes: (items) =>
    set({ envelopes: Object.fromEntries(items.map((e) => [e.id, e])) }),

  upsertEntry: (e) =>
    set((s) => {
      const existing = s.entriesById[e.id];
      const entriesById = { ...s.entriesById, [e.id]: e };
      let entries: JournalEntrySummary[];
      if (existing) {
        entries = s.entries.map((x) => (x.id === e.id ? e : x));
      } else {
        entries = [e, ...s.entries].slice(0, MAX_ENTRIES);
      }
      const reviewIds = new Set(s.reviewIds);
      if (e.status === "review") reviewIds.add(e.id);
      else reviewIds.delete(e.id);
      return { entries, entriesById, reviewIds };
    }),

  removeEntry: (id) =>
    set((s) => {
      const { [id]: _omit, ...rest } = s.entriesById;
      const reviewIds = new Set(s.reviewIds);
      reviewIds.delete(id);
      return { entries: s.entries.filter((e) => e.id !== id), entriesById: rest, reviewIds };
    }),

  setConn: (conn) => set({ conn }),
  setMocked: (mocked) => set({ mocked }),

  applySseEvent: (event) => {
    if (event.event_type === "ledger.entry_posted") {
      const entry = get().entriesById[event.entry_id];
      if (entry) {
        get().upsertEntry({
          ...entry,
          status: "posted",
          confidence: entry.confidence,
        });
      }
    } else if (event.event_type === "envelope.decremented") {
      const env = get().envelopes[event.envelope_id];
      if (env) {
        set((s) => ({
          envelopes: {
            ...s.envelopes,
            [event.envelope_id]: { ...env, used_cents: env.used_cents + event.amount_cents },
          },
        }));
      }
    } else if (event.event_type === "review.enqueued") {
      const entry = get().entriesById[event.entry_id];
      if (entry) {
        get().upsertEntry({
          ...entry,
          status: "review",
          confidence: event.confidence,
          review_reason: event.reason,
        });
      }
    }
  },
}));

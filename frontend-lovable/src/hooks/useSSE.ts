import { useEffect, useRef, useState } from "react";
import { buildSseUrl, isMockMode } from "@/lib/api";
import type { SseEvent } from "@/lib/types";

type ConnStatus = "connecting" | "connected" | "reconnecting" | "offline";

interface UseSseOptions {
  url: string; // path, prefixed with API base in real mode
  onEvent: (e: SseEvent) => void;
  enabled?: boolean;
  /** When in mock mode, this generator emits synthetic events on a timer. */
  mockGenerator?: (emit: (e: SseEvent) => void) => () => void;
}

export function useSSE({ url, onEvent, enabled = true, mockGenerator }: UseSseOptions) {
  const [status, setStatus] = useState<ConnStatus>("connecting");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;

    if (isMockMode) {
      setStatus("connected");
      if (!mockGenerator) return;
      const stop = mockGenerator((e) => onEventRef.current(e));
      return () => stop();
    }

    let cancelled = false;
    let es: EventSource | null = null;

    const open = () => {
      try {
        es = new EventSource(buildSseUrl(url));
        es.onopen = () => !cancelled && setStatus("connected");
        es.onmessage = (msg) => {
          try {
            const data = JSON.parse(msg.data);
            onEventRef.current(data);
          } catch {
            /* ignore malformed events */
          }
        };
        es.onerror = () => {
          if (cancelled) return;
          setStatus("reconnecting");
          // EventSource auto-reconnects; if it goes into a closed state, retry manually
          if (es?.readyState === EventSource.CLOSED) {
            setTimeout(() => !cancelled && open(), 2000);
          }
        };
      } catch {
        setStatus("offline");
      }
    };

    open();

    return () => {
      cancelled = true;
      es?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled]);

  return { status };
}

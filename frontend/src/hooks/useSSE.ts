import { useEffect, useRef } from 'react'

// Generic EventSource hook with StrictMode-safe cleanup.
// Fingent wire format: `data: {json}\n\n` with no `id:` or `event:` lines —
// all messages dispatch as type "message", routed by `event_type` from JSON.
export function useSSE<T>(
  url: string,
  onEvent: (event: T) => void,
  onStatus?: (open: boolean) => void,
): void {
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus

  useEffect(() => {
    if (!url) return
    const es = new EventSource(url)
    es.onopen = () => onStatusRef.current?.(true)
    es.onmessage = (m) => {
      try {
        onEventRef.current(JSON.parse(m.data) as T)
      } catch (err) {
        console.warn('[useSSE] malformed payload', err)
      }
    }
    es.onerror = () => onStatusRef.current?.(false)
    return () => {
      es.close()
    }
  }, [url])
}

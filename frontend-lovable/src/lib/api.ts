// Thin fetch wrapper.
//
// VITE_API_BASE_URL is the optional absolute prefix for fetch URLs. When it's
// empty (the default in `.env.local`), `fetch(path)` issues a same-origin
// request, which the Vite dev server's proxy forwards to the backend (see
// `vite.config.ts`). When it's set, requests go directly to that origin.
//
// Either way the wrapper *always* tries the network first. If the network
// call fails (offline, 5xx, no proxy match), and a `mockLoader` was provided,
// we fall back to it so the dev experience stays usable even when the backend
// is down.

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export interface FetchOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export interface ApiResult<T> {
  data: T;
  mocked: boolean;
}

/**
 * Try the real API first. If it fails (network, 5xx), fall back to a local
 * mock when one was provided. 4xx responses are surfaced as `ApiError` and
 * never trigger the mock — they're the backend telling us "this resource
 * doesn't exist" or "you sent garbage."
 */
export async function apiFetch<T>(
  path: string,
  options: FetchOptions = {},
  mockLoader?: () => Promise<T> | T,
): Promise<ApiResult<T>> {
  const { method = "GET", body, signal, headers = {} } = options;

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method,
      signal,
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, detail?.detail ?? res.statusText);
    }
    const data = (await res.json()) as T;
    return { data, mocked: false };
  } catch (err) {
    if (err instanceof ApiError && err.status >= 400 && err.status < 500) throw err;
    // network or 5xx — fall through to mock
    if (!mockLoader) throw err;
  }

  if (!mockLoader) {
    throw new ApiError(0, "No backend reachable and no mock provided.");
  }
  const data = await mockLoader();
  // small artificial latency so skeletons get a chance to render — keeps it honest
  await new Promise((r) => setTimeout(r, 60));
  return { data, mocked: true };
}

export function buildSseUrl(path: string): string {
  return `${BASE_URL}${path}`;
}

export const isMockMode = !BASE_URL;

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// Backend mounted prefixes — must match `backend/api/main.py` router mounts:
// /healthz, /swan, /external, /documents, /pipelines, /runs, /journal_entries,
// /envelopes, /review, /reports, /dashboard, /employees, /period_reports, /wiki.
// Lovable's src/lib/api.ts uses `VITE_API_BASE_URL` to form absolute fetch URLs;
// we point it at this dev server origin (see .env.local) so requests come back
// here and get proxied to the local Fingent backend.
//
// Default port is 8001 because :8000 is held by another service on this dev
// box. Override with `FINGENT_BACKEND_URL=http://127.0.0.1:<port> npm run dev`
// if you've started uvicorn on a different port.
const BACKEND = process.env.FINGENT_BACKEND_URL ?? "http://127.0.0.1:8001";

// Some backend prefixes overlap with React Router routes (e.g. /runs, /wiki,
// /reports). On a hard reload of `/wiki` the browser sends a document
// request — we want Vite's SPA fallback to serve index.html, NOT a JSON
// 404 from the backend. The bypass below lets HTML navigations skip the
// proxy and fall through to Vite's static handling. XHR/JSON requests
// (Accept: application/json) keep going to the backend.
//
// Exception: artifact / blob endpoints serve raw bytes (markdown, PDF, CSV)
// that the user opens via <a target="_blank">. Those navigations also arrive
// with Accept: text/html, but we need them to reach the backend so the file
// streams. Whitelist them here.
const ARTIFACT_PATTERNS = [
  /^\/period_reports\/\d+\/artifact(\?|$)/,
  /^\/documents\/\d+\/blob(\?|$)/,
];

function spaBypass(req: { headers: Record<string, string | string[] | undefined>; url?: string }) {
  const url = req.url ?? "/";
  for (const re of ARTIFACT_PATTERNS) {
    if (re.test(url)) return null;
  }
  const accept = String(req.headers["accept"] ?? "");
  if (accept.includes("text/html")) {
    return url;
  }
  return null;
}

const proxied = (target: string) => ({
  target,
  changeOrigin: true,
  bypass: spaBypass,
});

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: 5174,           // primary Lovable frontend; existing frontend/ keeps :5173
    strictPort: true,
    hmr: {
      overlay: false,
    },
    proxy: {
      "/healthz":          proxied(BACKEND),
      "/swan":             proxied(BACKEND),
      "/external":         proxied(BACKEND),
      "/documents":        proxied(BACKEND),
      "/employees":        proxied(BACKEND),
      "/pipelines":        proxied(BACKEND),
      "/runs":             proxied(BACKEND),
      "/journal_entries":  proxied(BACKEND),
      "/envelopes":        proxied(BACKEND),
      "/review":           proxied(BACKEND),
      "/reports":          proxied(BACKEND),
      "/period_reports":   proxied(BACKEND),
      "/wiki":             proxied(BACKEND),
      "/dashboard":        proxied(BACKEND),
      "/ai-spend":         proxied(BACKEND),
      "/demo":             proxied(BACKEND),
      "/gamification":     proxied(BACKEND),
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    dedupe: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime", "@tanstack/react-query", "@tanstack/query-core"],
  },
}));

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import tsconfigPaths from 'vite-tsconfig-paths'

// Backend mounted prefixes — must match `backend/api/main.py` router mounts:
// /healthz, /swan, /external, /documents, /pipelines, /runs, /journal_entries,
// /envelopes, /review, /reports, /dashboard
const BACKEND = 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react(), tailwindcss(), tsconfigPaths()],
  server: {
    host: '0.0.0.0',          // bind on all ifaces so SSH / VS Code can forward
    port: 5173,
    strictPort: true,
    proxy: {
      '/healthz':         BACKEND,
      '/swan':            { target: BACKEND, changeOrigin: true },
      '/external':        { target: BACKEND, changeOrigin: true },
      '/documents':       { target: BACKEND, changeOrigin: true },
      '/pipelines':       { target: BACKEND, changeOrigin: true },
      '/runs':            { target: BACKEND, changeOrigin: true },
      '/journal_entries': { target: BACKEND, changeOrigin: true },
      '/envelopes':       { target: BACKEND, changeOrigin: true },
      '/review':          { target: BACKEND, changeOrigin: true },
      '/reports':         { target: BACKEND, changeOrigin: true },
      '/dashboard':       { target: BACKEND, changeOrigin: true },
    },
  },
})

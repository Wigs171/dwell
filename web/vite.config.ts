import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Dwell web frontend (Phase 2). In dev, the Svelte app runs on Vite (5173) and
// proxies the API calls to the FastAPI engine (dwell_server.py on 8000), so the
// browser sees one same-origin host (no CORS) — exactly how it will look in
// production when FastAPI serves the built `dist/`. SSE streams pass through the
// proxy untouched. `host: true` exposes the dev server on the LAN for the tablet.
const API = process.env.DWELL_API || 'http://127.0.0.1:8000'
const apiRoutes =
  /^\/(vaults|vault-cover|vault-sources|vault|learn|endpoints|reader|session|page|repage|steer|branches|expand|quiz|voices|voice|level|form|language|missed|timeline|nodes|state|wander|health|tts|asset)\b/

export default defineConfig({
  // Root this project explicitly so Vite can be launched from anywhere (the repo
  // root, the preview tool) without a cwd dance.
  root: import.meta.dirname,
  plugins: [svelte()],
  // Avoid lightningcss (its native binding isn't installed on Windows via npm —
  // same class of issue as the rolldown binding); use esbuild/postcss instead so
  // `npm run build` works and FastAPI can serve a single-server `dist/`.
  css: { transformer: 'postcss' },
  build: { cssMinify: false },
  server: {
    host: true,
    port: Number(process.env.PORT) || 5173,
    proxy: {
      [apiRoutes.source]: { target: API, changeOrigin: true },
    },
  },
})

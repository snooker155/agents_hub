import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// Where the dev server forwards /api. Defaults to a backend on this machine;
// docker-compose points it at the backend service instead.
const apiProxyTarget = process.env.VITE_API_PROXY || 'http://localhost:8000'

// Same-origin /api keeps CORS out of the picture and lets the backend be
// published on any port. SSE endpoints go through here too, so the proxy must
// not buffer: http-proxy streams by default.
const apiProxy = {
  '/api': {
    target: apiProxyTarget,
    changeOrigin: true,
    // WebSockets under /api (the terminal) need
    // the upgrade forwarded too; http-proxy only does that when asked.
    ws: true,
  },
  // The proxied pages live outside /api: a deployed app is published under
  // /apps/<slug>/ (docs/project-deployments.md). Without this the dev server
  // answers with the dashboard's own index.html and the frame stays blank.
  '/apps': {
    target: apiProxyTarget,
    changeOrigin: true,
  },
  // The consent portal's public page and OAuth callback (docs/consent.md).
  // Not changeOrigin: the backend builds the callback address from Host.
  '/consent': {
    target: apiProxyTarget,
  },
}

export default defineConfig({
  plugins: [react()],
  server: { proxy: apiProxy },
  // `vite preview` serves the built bundle, which `ah up` uses as the local
  // production dashboard. It needs the same forwarding: without it the bundle
  // loads and every request to the API 404s against the static server.
  preview: { proxy: apiProxy },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    // Tests live next to the code they cover, in a `__tests__` folder.
    include: ['src/**/__tests__/**/*.test.{js,jsx}'],
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'html', 'json-summary'],
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/**/__tests__/**', 'src/test/**', 'src/demo/fixtures/**', 'src/main.jsx'],
      // A floor, not a goal: CI fails when coverage drops below what the suite
      // reached, so raise it as tests grow. Re-measured 2026-10-10 on Vitest 5,
      // whose v8 coverage counts by syntax tree: the same suite that read
      // 66.7% lines under Vitest 3 reads 52.4% here.
      thresholds: { lines: 52, statements: 49, functions: 41, branches: 41 },
    },
  },
})

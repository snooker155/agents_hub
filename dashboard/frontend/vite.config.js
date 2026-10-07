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
    // WebSockets under /api (the terminal, the browser's frame stream) need
    // the upgrade forwarded too; http-proxy only does that when asked.
    ws: true,
  },
  // The proxied pages live outside /api: a preview iframe loads
  // /preview/<ticket>/ and a deployed app is published under /apps/<slug>/
  // (docs/project-deployments.md). Without these two the dev server answers
  // both with the dashboard's own index.html and the frame stays blank.
  '/preview': {
    target: apiProxyTarget,
    changeOrigin: true,
  },
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

// The demo build (`npm run build:demo`): the real dashboard over recorded data,
// served by the site under /agents_hub/demo/ (see site/scripts/build-demo.mjs).
// Its own output folder, so it never overwrites the production bundle.
const isDemo = process.env.VITE_DEMO === '1'
const demoBuild = isDemo
  ? { base: process.env.VITE_DEMO_BASE || '/agents_hub/demo/', build: { outDir: 'dist-demo' } }
  : {}

export default defineConfig({
  ...demoBuild,
  // The bundle's map (source file to hashed chunks), read by the service
  // worker to keep the pages a phone opens most ready offline (public/sw.js,
  // docs/pwa.md). Served next to index.html, outside /assets.
  build: { manifest: 'asset-manifest.json', ...demoBuild.build },
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
      reporter: ['text', 'html'],
      include: ['src/lib/**', 'src/components/*.js', 'src/i18n/core.js', 'src/views/*.js'],
    },
  },
})

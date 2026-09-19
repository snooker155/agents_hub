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
      reporter: ['text', 'html'],
      include: ['src/lib/**', 'src/components/*.js', 'src/i18n/core.js', 'src/views/*.js'],
    },
  },
})

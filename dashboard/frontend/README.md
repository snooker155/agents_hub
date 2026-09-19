# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.

## Reporting failures

`useToast()` (from `src/components/toast.js`, mounted by `ToastProvider` in
`main.jsx`) is how a failed request tells the user something went wrong:

```js
const toast = useToast();
try { await saveThing(); } catch (e) { toast.error(t('page.errors.save'), errorDetail(e)); }
```

`errorDetail(e)` pulls out the most specific thing the failure knows — FastAPI's
`detail` (string or validation list) when the server answered, the transport
error when it did not — and the toast shows it as a second line. Message strings
live in each page's own `errors` namespace in the locale files.

A toast reports; it never blocks, never asks a question and never takes focus.
Anything the user has to answer still belongs in a dialog.

Not everything that can fail deserves one. `localStorage` writes, background
polls and optional enrichment with a working fallback stay silent on purpose —
each of those `catch` blocks carries a comment saying what happens instead. A
subscriber that throws inside the event stream is logged to the console rather
than shown: the user cannot act on it and a broken stream would repeat it.

## Tests

Unit tests run on [Vitest](https://vitest.dev) in a jsdom environment, with
React Testing Library for the component tests.

```bash
npm test              # one run
npm run test:watch    # re-run on change
npm run test:coverage # + a coverage table (and coverage/index.html)
```

Tests live in a `__tests__` folder next to the code they cover
(`src/lib/__tests__/capabilities.test.js` covers `src/lib/capabilities.js`) and
import their matchers from `vitest` explicitly, so no globals are configured.
`src/test/setup.js` runs first: it loads the jest-dom matchers, unmounts
whatever the previous test rendered, and stubs the `matchMedia` jsdom omits.

What is covered is the logic that is easy to get quietly wrong — the capability
guard, date parsing across timezones, tool-call formatting, the view op algebra,
the i18n plural and fallback rules — rather than markup. Page components are not
tested; they are mostly data plumbing over the API client.

## Theming

Colors are not written per component. `src/theme.css` is **generated** by
`scripts/gen-theme.mjs` (run automatically by `predev` / `prebuild`, or by hand
with `npm run gen:theme`) and imported after `index.css` in `main.jsx`. It does
two things:

- **Brand remap.** `indigo` / `violet` / `purple` / `fuchsia` utilities are
  repainted with a deep-navy ramp in both themes, so the app has no purple.
- **Dark theme.** Every Tailwind color utility used in `src/` gets a dark
  counterpart: neutrals follow one surface ladder (page → sunken → card →
  raised) and colored badges flip to the light tier of their own hue, so a
  `text-green-700` label stays readable on a dark tint instead of staying dark.

Rules are wrapped in `:where(...)` so they keep plain-utility specificity and
win only by source order — an explicit `dark:` variant in a component still
overrides them.

To change a color, edit the palettes at the top of `scripts/gen-theme.mjs` and
regenerate; don't edit `src/theme.css`. Anything that needs a color outside a
Tailwind class (inline styles, canvas, cytoscape) should read the tokens —
`var(--surface-card)`, `var(--text-muted)`, `var(--brand-600)`, `var(--warn)` —
declared at the top of the generated file.

## Container image

`Dockerfile` here builds this directory alone — the build context is
`dashboard/frontend`, and nothing in it needs the Python side of the repo.

| Target | What it is |
|---|---|
| `dev` | The Vite dev server with HMR. What `docker compose up` runs, with this directory bind-mounted over `/app`. |
| default (`nginx`) | `npm run build` output served by nginx on port 80, with `/api` proxied to the backend. |

The nginx target is the deployable one. Its config is `docker/nginx.conf`, plus
`docker/20-agents-hub-config.sh`, which the stock nginx entrypoint runs at
startup to write two generated includes:

- the `agents_hub_backend` upstream, from `BACKEND_SERVERS` (space-separated
  `host:port` list, `least_conn` across them)
- the view-asset cache, from `API_ASSET_CACHE_SECONDS` (`0` disables it)

What is cached and what deliberately is not:

- **Hashed bundle** (`/assets/…`) — `immutable`, one year. Vite fingerprints it,
  so the file at a URL never changes.
- **`index.html`** — `no-cache`. It is the pointer to the current bundle, so a
  cached copy would pin a browser to the previous deploy.
- **`/api/views/{id}/assets/…`** — short shared cache, because these are plain
  generated files (meshes, images, datasets) the Studio re-requests on every
  render. Short, because a view can be mutated in place.
- **Everything else under `/api`** — not cached and not buffered. The dashboard
  reads live state, and SSE streams have to arrive frame by frame.

`VITE_API_ORIGIN` is a build arg, not a runtime variable: it is baked into the
bundle. Leave it empty for the normal same-origin setup where nginx proxies
`/api`; set it only to point a build at a backend on another host.

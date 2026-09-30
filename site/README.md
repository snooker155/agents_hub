# site/

The public landing page and documentation site, published to GitHub Pages at
**https://snooker155.github.io/agents_hub/**.

It is a [VitePress](https://vitepress.dev) project. The landing page lives here
(`index.md`); the documentation does not. `scripts/sync-docs.mjs` copies the
shipped corpus out of `docs/` into `site/guide/` before every dev run and build,
so the site and the app serve the same text and there is nothing to keep in sync
by hand.

```bash
cd site
npm install
npm run dev        # http://localhost:5173, corpus synced first
npm run build      # -> site/.vitepress/dist
npm run preview    # serve the built output
```

## The demo

`/demo/` on the site is the real dashboard running over recorded data. It is
`dashboard/frontend` built with `VITE_DEMO=1` (`npm run build:demo` there): a
mock service worker ([MSW](https://mswjs.io)) answers every `/api` call from
`dashboard/frontend/src/demo/fixtures/`, and replays recorded runs as event
streams, so it needs no backend and works on GitHub Pages.

```bash
npm run demo       # build the demo and copy it into site/public/demo/
```

`npm run build` does this first (its `prebuild` runs `sync` then `demo`).
When `dashboard/frontend/node_modules` is missing the demo step prints a
message and skips, so a docs only build still works, just without `/demo/`.
`DEMO_SKIP=1` skips it on purpose. `site/public/demo/` is generated and
git-ignored.

For GitHub Pages to publish the demo, the workflow has to install the
frontend's dependencies before building the site, for example a step
`npm ci` with `working-directory: dashboard/frontend` (and
`dashboard/frontend/package-lock.json` added to the npm cache paths). Without
that step the published site simply has no `/demo/`.

## Recipes

`site/recipes/*.md` are short task oriented walkthroughs. The sidebar under
`/recipes/` is built from that folder when the config loads: a new file shows
up on its own, titled by its first `# ` heading.

## Screenshots

The pictures on the landing page and in the recipes are taken from the demo,
so they show the same recorded data a visitor can click through:

```bash
npx playwright install chromium   # once
npm run screenshots               # all of them
npm run screenshots -- chat flows # only these ids
npm run screenshots -- --build    # rebuild the demo first
```

`scripts/screenshots.mjs` serves `dashboard/frontend/dist-demo` with
`vite preview` on a free port and writes `public/screenshots/<id>.png` (the
six landing page shots) and `public/screenshots/recipes/<id>.png` (one per
recipe; the page each recipe is taken on is the `RECIPE_PAGES` table in the
script). Without recorded fixtures it leaves the landing page shots alone.

Every shot is taken twice, in the dashboard's light and dark theme, the
dark set under `public/screenshots/dark/` with the same paths. A markdown
image under `/screenshots/` is rendered as both pictures (the image rule in
`.vitepress/config.mjs`) and `theme/custom.css` shows the one that matches
the reader's theme, so the site's screenshots follow its appearance switch
with no markup in the pages. The README at the repository root uses the
dark set directly.

## Why the copy

`docs/` is a product asset, not a website: `tests/test_docs_corpus.py` asserts
that `docs/*.md` matches `docs/index.json` exactly, and agents read it through
the `search_docs` and `read_doc` tools. A `.vitepress/` folder or an extra
`index.md` in there would fail that test. So the corpus is copied out, never
written back. `site/guide/` is generated and git-ignored.

The sidebar is built from `docs/index.json` too, grouped by `GROUPS` in
`.vitepress/config.mjs`. A document added to the corpus appears on the site
automatically; add its id to a group to place it, otherwise it lands under
"More".

## Things that will break it

- **`base`** in `.vitepress/config.mjs` is `/agents_hub/` and must match the
  repository name, or every asset 404s on the published site.
- **Frontmatter is YAML.** A value in `index.md` containing `: ` has to be
  quoted.
- **Markdown is compiled as a Vue template.** Raw `<angle-brackets>` and `{{ }}`
  outside code spans break the build. Everything in the corpus is inside
  backticks today; keep it that way.
- **Dead links fail the build.** That is deliberate: it catches a corpus link to
  a document that no longer exists.

Deployment is `.github/workflows/pages.yml`, on pushes to `main` that touch
`site/**` or `docs/**`. Pages must be set to the "GitHub Actions" source once in
the repository settings.

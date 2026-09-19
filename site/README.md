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

## Building an html view

An `html` view is a live, interactive HTML/JS/CSS page in a sandboxed iframe: scripts run, but the page is origin-isolated (no same-origin, no host storage) and its Content Security Policy allows no network by default. Scripts and styles must be inline or come from the view's own asset files; fonts likewise (`self` or `data:`). There is no CDN: a library the page needs is written into the workspace and bound as an asset.

- **Inline page**: `create_view` with kind `html` and spec `{"html": "<!doctype html>..."}` for a page that fits in one string (CSS and JS inline).
- **Multi-file page**: write `index.html`, `app.js`, `style.css` and any images into the workspace with `write_file` first, then `create_view` with spec `{"entry": "index.html"}` and `files` listing every file. References between files are relative paths. `view_add_asset` binds a further workspace file later.
- The page talks to the host only through the injected `viewhost` postMessage bridge; the system theme comes through `prefers-color-scheme`, so style both schemes.
- **A real backend**: after the page is in place, `view_serve(upstream="http://localhost:PORT")` exposes a service you already started behind the view's origin-isolated proxy, or `view_serve(command="python app.py", port=8123)` launches it in the workspace when launching is enabled; the page reaches it at the proxy base the bridge exposes. `view_serve_stop` shuts it down.
- To change the page, rewrite the file and `view_add_asset` it again, or `view_apply_ops` on `spec.html` for an inline page. Keep the page working at phone width.

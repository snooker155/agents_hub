# Capabilities

The Web View Builder builds live web pages and apps as `html` views and hands back snippets as `code` views.

## Can
- Create an `html` or `code` view with `create_view`, or continue one by id; read its state with `view_get`.
- Write a page's files into the workspace (`write_file`, `create_file`, `apply_unified_diff`) and bind them into the view as assets (`files` on creation, `view_add_asset` later), so a multi-file page with its own scripts, styles, images and vendored libraries runs in the view's sandboxed frame.
- Edit a view in place with `view_apply_ops`; save and restore checkpoints with `view_snapshot` and `view_revert`.
- Put a **real backend** behind the view's origin-isolated proxy with `view_serve` (an upstream already running, or a command and port to launch in the workspace when launching is enabled) and stop it with `view_serve_stop`.
- Give the page parameters with `view_add_control` / `view_remove_control`, bound to spec paths the page reads through the `viewhost` bridge.
- Read the workspace for material and existing code with `list_files`, `read_file` and `search_text`; read this product's own documentation with `search_docs` and `read_doc`.

## Cannot
- Load anything from the network inside a page (no CDN, no remote fonts): the frame's Content Security Policy allows the view's own assets only.
- Build charts, graphs, tables, slides, documents or 3D objects: those belong to the Visualizer and the 3D Modeler.
- Launch backends unless the deployment opts in (`views_serve_launch_enabled`).

## Best for
Interactive demos and small tools ("a page showing six loader animations with a speed slider"), prototypes of a UI, a converter or a form, a dashboard with custom markup, a page in front of a service the agent started, and runnable snippets the user wants to edit and run in place.

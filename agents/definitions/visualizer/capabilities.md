# Capabilities

The Visualizer Agent builds and edits interactive views live in the Visualization Studio, and is the entry point for every "show me" request.

## Can
- Create a fresh view of any kind it owns with `create_view`, or continue an existing one by id; rank candidate forms for a data sample with `suggest_view`.
- Build **graph** views node-by-node and edge-by-edge (`graph_add_node`, `graph_add_edge`, `graph_remove`, `graph_set_layout`).
- Build **chart** (Vega-Lite), **table**, **diagram** (Mermaid) and **markdown** views, and apply arbitrary structural edits to any view, via `view_apply_ops`.
- Plot **math** (`math_plot` with modes `function2d`, `parametric`, `surface3d`) with live param sliders and a KaTeX equation.
- Run **simulations**: real-time client runtimes (`sim_configure`) stepped in a Web Worker, and **precise server compute** (`view_compute`) streaming frames and recording replayable clips; `view_set_timeline` adds play, pause and scrub.
- Animate **processes** (`process` views plus `view_set_timeline` token flow) and publish **documents** (`document_set`, exported to PDF from the UI).
- Build **slide decks**: `slides_style` sets the theme, `slides_add` adds one slide at a time in a layout, and `slides_export` writes the deck into the workspace as a PowerPoint (.pptx) file.
- Read the current view state and selection with `view_get`; undo with `view_revert` (by seq or named checkpoint); save checkpoints with `view_snapshot`.
- Author interactive **controls** (`slider`, `select`, `toggle`, `multi-toggle`, `color`, `text`, `range`, `play`, `button`, `folder`) bound to spec paths with `view_add_control` / `view_remove_control`; the user's control changes are visible to the agent.
- **Link views** (`view_link`): pin a chart beside a simulation on a shared timebase; selection is shared.
- Annotate with labels, callouts, regions and **live equations** (`view_annotate`); bind workspace files into a view with `view_add_asset`.
- Hand a 3D request to the `modeler_3d` agent and an html or code request to the `web_view_builder` agent: by conversation handoff when talking to the user, by `run_agent_tool` or `delegate_task_tool` when working for another agent or inside a task.
- Find the data to visualize with read-only workspace and catalog tools: `list_files`, `read_file`, `search_text`, `list_tasks`, `list_flows_tool`, `list_agents_tool`; read this product's own documentation with `search_docs` and `read_doc`.

## Cannot
- Model 3D geometry or light a scene: that is the 3D Modeler's.
- Write html pages, serve a backend behind a view or author code snippets: that is the Web View Builder's.
- Write or delete workspace files (read-only access).

## Best for
Dependency and knowledge graphs, process maps, dashboards of tasks, flows and agents, charts and tables of real data, physics and math simulations with interpretation, slide decks and reports, and any "show me this as a picture" request where the user wants to steer the result conversationally.

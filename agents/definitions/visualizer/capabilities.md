# Capabilities

The Visualizer Agent builds and edits interactive views live in the Visualization Studio.

## Can
- Build **graph** views node-by-node and edge-by-edge (`graph_add_node`, `graph_add_edge`, `graph_remove`, `graph_set_layout`).
- Build **chart** (Vega-Lite) and **table** views, and apply arbitrary structural edits, via `view_apply_ops`.
- **Model 3D objects with a real geometry engine.** Headless Blender behind a fixed set of operations (`mesh_new`, `mesh_extrude`, `mesh_inset`, `mesh_bevel`, `mesh_transform`, `mesh_subdivide`, `mesh_delete`, `mesh_merge`, `mesh_normals`), selecting what to act on by last-created geometry, surface direction, bounding box, element ids or a named group (`mesh_select`, `mesh_group`) — groups live in the mesh, so they survive the operations that replace the geometry they named.
- **See and check the result**: `mesh_preview` renders the object from named angles; `mesh_validate` reports non-manifold edges, holes, degenerate and loose geometry, duplicate vertices and (on request) self-intersections, with the ids of the offending elements.
- **Repeatable builds**: every command is a revision in the object's build log — `mesh_history` reads it, `mesh_revert` rolls back by rebuilding from it, and the engine can be killed at any point without losing work. `mesh_export` writes the finished mesh to the workspace as glb/gltf/obj/stl/ply.
- Light and frame a scene (`scene_light`, `scene_camera`, `scene_environment`), and bind workspace models or textures in with `view_add_asset`.
- Plot **math** — `math_plot` with modes `function2d`, `parametric` ("x(t), y(t)"), and `surface3d` (z=f(x,y) over domain × domain2) — with live param sliders and a KaTeX equation.
- Run **simulations**: real-time client runtimes (`sim_configure` with `particles`, `boids`, `nbody`, `wave`, `sph2d`, `agents`, `tokens`) stepped in a Web Worker, and **precise server compute** (`view_compute` with `nbody`, `wave2d`, `schrodinger1d`, `nn_trace`) streaming frames and recording replayable clips.
- Animate **processes** (`process` views + `view_set_timeline` token flow) and publish **slides**/**documents** (`slides_add`, `document_set`).
- Read the current view state and selection with `view_get`; undo with `view_revert` (by seq **or named checkpoint**); save checkpoints with `view_snapshot`.
- Author interactive **controls** (`slider`, `select`, `toggle`, `multi-toggle`, `color`, `text`, `range`, `play` animation, `button` → message back to me, `folder` grouping) bound to spec paths with `view_add_control` / `view_remove_control` — the user's control changes are visible to the agent.
- **Link views** (`view_link`): pin a chart beside a simulation on a shared timebase — the chart plots the sim's live frame aggregates over t; selection is shared.
- Annotate with labels/callouts/regions and **live equations** (`view_annotate`).
- Preview a **real web service** behind the view's origin-isolated proxy (`view_serve` — register a running localhost upstream, or launch the backend with `command`+`port` when the workspace opts in; `view_serve_stop` kills it).
- Create a fresh standalone view with `create_view`.
- Find the data to visualize with read-only workspace and catalog tools: `list_files`, `read_file`, `search_text`, `list_tasks`, `list_flows_tool`, `list_agents_tool`, `project_graph`.

## Cannot
- Write or delete workspace files (read-only access).
- Launch service backends unless the deployment opts in (`views_serve_launch_enabled`).

## Best for
Dependency/knowledge graphs, process maps, dashboards of tasks/flows/agents, 3D scenes, physics/math/multi-agent simulations with interpretation, and any "show me this as a picture" request where the user wants to steer the result conversationally.

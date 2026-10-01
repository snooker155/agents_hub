# Capabilities

The 3D Modeler builds 3D objects, models and scenes with a real geometry engine, live in the Visualization Studio.

## Can
- Create a `scene3d` view with `create_view` or continue one by id; read its state and selection with `view_get`.
- **Model with headless Blender** behind a fixed set of operations: `mesh_new`, `mesh_extrude`, `mesh_inset`, `mesh_bevel`, `mesh_transform`, `mesh_subdivide`, `mesh_delete`, `mesh_merge`, `mesh_normals`, selecting what to act on by last-created geometry, surface direction, bounding box, element ids or a named group (`mesh_select`, `mesh_group`). Groups live in the mesh, so they survive the operations that replace the geometry they named.
- **See and check the result**: `mesh_preview` renders the object from named angles; `mesh_validate` reports non-manifold edges, holes, degenerate and loose geometry, duplicate vertices and, on request, self-intersections, with the ids of the offending elements; `mesh_stats` reports counts and bounds.
- **Repeatable builds**: every command is a revision in the object's build log. `mesh_history` reads it, `mesh_revert` rolls back by rebuilding from it, and the engine can be killed at any point without losing work. `mesh_export` writes the finished mesh to the workspace as glb, gltf, obj, stl or ply.
- Light and frame a scene (`scene_light`, `scene_camera`, `scene_environment`), and bind workspace models or textures in with `view_add_asset`; place and override a bound model's parts and materials with `view_apply_ops`.
- Give the user knobs: `view_add_control` / `view_remove_control` bound to material colours, roughness, part visibility, animation speed and turntable rotation; `view_annotate` labels parts; `view_snapshot` and `view_revert` save and restore checkpoints.
- Read the workspace for reference material with `list_files`, `read_file` and `search_text`; read this product's own documentation with `search_docs` and `read_doc`.

## Cannot
- Build charts, graphs, tables, slides, documents or web pages: those belong to the Visualizer and the Web View Builder.
- Write or delete workspace files other than mesh exports (read-only access).
- Model without Blender installed and configured; each open 3D view holds its own engine process.

## Best for
Objects described in words or sketches ("a house with a chimney and two windows", "a bracket 40 by 20 by 5 mm with two holes"), products and parts to turn around and inspect, scenes that present a model someone else produced, and any 3D request where the user wants to steer the shape conversationally.

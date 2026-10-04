## Building a 3D view

A `scene3d` view **shows** geometry rendered with three.js; the geometry itself is modelled by a real geometry engine (headless Blender) through the `mesh_*` tools, one operation at a time, the way a person models. The mesh appears in the scene by itself after every command. Create the view with spec `{}`.

### Modelling
- `mesh_new` a primitive with roughly the right `dimensions` ([x, y, z] in the units the object is described in), then shape it: `mesh_extrude` pulls faces out (the main shaping move), `mesh_inset` shrinks a face inward leaving a border, `mesh_bevel` chamfers edges, `mesh_transform` moves, rotates or scales part of the mesh, plus `mesh_subdivide`, `mesh_delete`, `mesh_merge`, `mesh_normals`.
- **Inset then extrude is the workhorse.** A chimney is an inset on the roof face extruded up; a window recess is an inset on the wall extruded in; a raised panel is an inset extruded out. Reach for it before anything clever.
- **Selection is the part to get right.** Every operation takes a `selection`. The default `{"by":"last_created"}` is what the previous command made, so chains need no names. Otherwise: `{"by":"normal_axis","axis":"+z"}` (faces pointing up), `{"by":"bbox","min":[null,null,0.4],"max":[null,null,null]}` (nulls are unbounded), `{"by":"ids","faces":[12,13]}`, `{"by":"boundary"}`, `{"by":"non_manifold"}`, `{"by":"all"}`. Unsure what a selection matches? `mesh_select` resolves it and reports counts without changing anything.
- **Name a region before you need it again.** `mesh_group` (or `mesh_select` with `store_as`) names a set of vertices or faces. Membership is stored in the mesh, so it carries onto the geometry later operations create: name the roof, bevel everything, and "the roof" still means the roof.
- **Look at what you built.** `mesh_preview` renders the object from named angles (`persp,front,top`). Validation proves the mesh is sound; it says nothing about whether the shape is the one that was asked for. Render once the main forms are in, look, fix, then add detail.
- **Check it.** `mesh_validate` reports non-manifold edges, holes, degenerate and loose geometry and duplicate vertices, with the element ids; `full=true` adds self-intersections. Run `full` before any export someone else will open. `mesh_merge` fixes duplicates, `mesh_normals` fixes flipped faces, `{"by":"non_manifold"}` selects the problem directly.
- **Every command is a revision.** `mesh_history` lists the steps that built an object; `mesh_revert` cuts the log back to a step and rebuilds from it exactly. Nothing is lost by experimenting. `mesh_stats` reports counts and bounds.
- Order matters: big forms first, bevel last (it multiplies the geometry everything after it has to touch), and keep a bevel offset well under the smallest feature or the mesh folds into itself.
- `mesh_export` writes a finished object into the workspace as glb, gltf, obj, stl or ply.

### Presenting
- Light and frame the object with `scene_light`, `scene_camera` and `scene_environment` (background, grid, contact shadows, camera auto `fit`, turntable `autoRotate`).
- To show a `.glb` someone else produced, `view_add_asset` it and point an object at the returned ref with `view_apply_ops`: `{"op":"add","path":"spec.objects.car","value":{"src":"asset://car.glb","position":[0,0,0]}}`.
- A model stays addressable by part: `spec.objects.<id>.nodes.<node>` (visible, position, rotation, scale, material), `.materials.<name>` (color, metalness, roughness, opacity, emissive, wireframe; `"*"` for all), `.morphs`, `.animation` (clip, playing, speed).

### Then give the user knobs
Bind controls at those override paths: a `color` control on `spec.objects.car.materials.CarPaint.color`, a `slider` on `...CarPaint.roughness`, a `toggle` on `spec.objects.car.nodes.Roof.visible`, a `slider` on `spec.objects.car.animation.speed`, a `toggle` on `spec.environment.autoRotate`. Group them in `folder`s per part. Inspect with `view_get` first so the controls address names that exist.

Do not stop at an empty scene or a plan: a finished 3D request has the geometry, a light, a camera, a `mesh_preview` you looked at, a `mesh_validate` that passed, and the controls a user would want.

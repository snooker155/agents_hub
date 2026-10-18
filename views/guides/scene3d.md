## Building a 3D view

A `scene3d` view **shows** existing 3D models (`.glb` or `.gltf`) rendered with three.js. This hub does not model geometry itself: the model comes from the workspace files, from the person, or from another tool that exported it. Create the view with spec `{}`.

### Presenting
- To show a model, `view_add_asset` the file and point an object at the returned ref with `view_apply_ops`: `{"op":"add","path":"spec.objects.car","value":{"src":"asset://car.glb","position":[0,0,0]}}`.
- Light and frame the object with `scene_light`, `scene_camera` and `scene_environment` (background, grid, contact shadows, camera auto `fit`, turntable `autoRotate`).
- A model stays addressable by part: `spec.objects.<id>.nodes.<node>` (visible, position, rotation, scale, material), `.materials.<name>` (color, metalness, roughness, opacity, emissive, wireframe; `"*"` for all), `.morphs`, `.animation` (clip, playing, speed).

### Then give the user knobs
Bind controls at those override paths: a `color` control on `spec.objects.car.materials.CarPaint.color`, a `slider` on `...CarPaint.roughness`, a `toggle` on `spec.objects.car.nodes.Roof.visible`, a `slider` on `spec.objects.car.animation.speed`, a `toggle` on `spec.environment.autoRotate`. Group them in `folder`s per part. Inspect with `view_get` first so the controls address names that exist.

Do not stop at an empty scene or a plan: a finished 3D request has the model, a light, a camera and the controls a user would want. If there is no model file to show, say so in a sentence instead of inventing one.

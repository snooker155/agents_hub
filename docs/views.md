# Views and Studio

A view is something an agent built to be looked at: a chart, a graph, a table, a
3D scene, a simulation, a slide deck or a document.

## Getting one

Ask for it. An agent with `create_view` builds the view instead of describing it
in prose, and the view appears as its own object you can open, share and edit.

## Studio

Studio is the view's own editing surface, bound to the **Visualizer** agent. You
edit by talking: "make the bars horizontal", "colour by region", "add a slider
for the year". The outliner lists the objects in the view, and selecting one is
injected into the next prompt as scene context, so "make it bigger" resolves
without re-reading the whole document.

## What views can do

- **Charts and graphs** from data the agent computed
- **3D objects modelled by a real geometry engine** — a headless Blender behind
  a fixed set of operations (`mesh_new`, `mesh_extrude`, `mesh_inset`,
  `mesh_bevel`, …). The agent works the way a person models: primitive first,
  then inset and extrude. Every command is a revision in the object's build log,
  so the build is repeatable and `mesh_revert` can roll it back. `mesh_validate`
  checks manifoldness, holes and self-intersections; `mesh_preview` renders the
  object so the agent can see what it made. Configure it under
  [settings](settings.md) → Blender.
- **3D scenes** around that object: cameras, lights, environments, and glTF
  models bound in from the workspace
- **Simulations** — `view_compute` runs a scenario server-side on numpy and
  streams frames in: gravitational N-body, a 2D wave field, the 1D
  time-dependent Schrödinger equation, the per-layer activations of a forward
  pass. Optionally recorded as a replayable clip.
- **Controls** — sliders and toggles wired to parameters
- **Annotations and timelines** over the result

## Gotchas

- The Visualizer edits views; it does not analyse your data. Give it the numbers
  or the file.
- `view_serve` proxies a running localhost service behind the view's isolated
  proxy. It counts as an outbound channel for capability purposes.
- 3D modelling needs Blender installed and configured. Each open 3D view holds
  its own engine process, which costs a few hundred MB; the ceiling and the idle
  timeout are on the settings page. Stopping an engine loses nothing, since the
  next command rebuilds the scene from its log.

Related: [agents](agents.md), [tools-and-capabilities](tools-and-capabilities.md).

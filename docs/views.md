# Views and Studio

A view is something an agent built to be looked at: a chart, a graph, a table, a
3D scene, a simulation, a slide deck or a document.

A view is owned by a run of any kind: an agent run, or a flow, loop, team or
scenario run. The owner is `{kind: "run"|"flow"|"loop"|"team"|"scenario", id: "..."}`.
A view created inside a flow node, a team turn or a scenario decision is owned
by the containing entity run. A process launched for an entity run knows its
owner from its environment.

## Getting one

Ask for it. An agent with `create_view` builds the view instead of describing it
in prose, and the view appears as its own object you can open, share and edit.
`GET /api/views` accepts `owner_kind` and `owner_id`; responses carry `owner` and
`owner_entity_id`. The views gallery (the Views tab of the Artifacts page,
`/artifacts`; the old `/views` redirects there) shows an owner chip linking to
the run's page. Its other tab is the workspace's files (docs/files.md), since a
view's exports become files and an html view is built from them.

## Studio

Studio is the view's own editing surface, bound to the agent that owns the
view's kind: the **Visualizer** for most kinds, the **3D Modeler** for a
`scene3d` view, the **Web View Builder** for `html` and `code` views (see
[Kind guides and specialists](#kind-guides-and-specialists)). You edit by
talking: "make the bars horizontal", "colour by region", "add a slider
for the year". The outliner lists the objects in the view, and selecting one is
injected into the next prompt as scene context, so "make it bigger" resolves
without re-reading the whole document.

## From chat

Ask any chat agent for something to look at, a 3D object or a chart, and it
delegates to the Visualizer. With no view open, the Visualizer creates one with
`create_view` and builds it: the view it created becomes the default target of
its view tools for the rest of that run, and the result of `create_view` carries
the guide for that kind. A 3D object or a web page it passes on to the
specialist that owns the kind. It reports the `view_id` back, and the delegating agent either answers or delegates again with
that id to finish the same view. The reply shows each view the turn made as a
live preview that opens full size and links to the view's page; build view
lists them in its panel next to the changed files.

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
- **Code**: a runnable, editable, versioned snippet, instead of a code fence
  in prose. See [Code](#code) below.
- **Slide decks** with layouts and themes, shown full screen and downloaded as
  PDF or PowerPoint. See [Slides](#slides) below.

## Kind guides and specialists

The fifteen kinds share one tool surface and have little in common, so what a
view agent sees and is told depends on the kind of the view it is on.

- **Tools follow the kind.** `views/focus.py` maps each kind to the tools that
  act on it alone (`graph_*` for graphs, `scene_*` and `mesh_*` for 3D,
  `slides_*` for decks, `sim_configure`, `view_compute` and `view_set_timeline`
  for simulations, `document_set`, `math_plot`, `view_serve` for html). The
  agent loop shows the model the common view tools plus the active kind's and
  hides the others, see [agent-loop](agent-loop.md#view-focus).
- **One guide per kind.** `views/guides/<kind>.md` says what to build with
  which tool, in what order, and what the renderer expects. The agent reads it
  when a view of that kind becomes its target: in the result of `create_view`,
  in the first `view_get` on an existing view, or in the Studio's active-view
  note. Each guide is read once per run, and the system prompt stays general.
  Editing a guide changes the next call; no restart.
- **Two specialists.** 3D modelling and web pages are different crafts, so
  `scene3d` belongs to the `modeler_3d` agent and `html` and `code` to the
  `web_view_builder` agent. The Studio and a view's own chat open those kinds
  with the specialist. The `visualizer` stays the single entry point for any
  other agent: it hands the conversation over when the user talks to it
  directly, and delegates (`run_agent_tool` in a chat, `delegate_task_tool` in
  a task) when it works for another agent. The three are system agents, so an
  existing install receives the two new ones and the visualizer's handoff list
  on its next start; a visualizer the install already had keeps its mesh tools,
  which view focus hides unless the run is on a 3D view.

## Code

The `code` kind is a snippet the agent hands back through `create_view`
(`{"language": "python", "body": "print('hi')"}`), or the user starts from the
Chat code panel. `language` names anything for display, but only Python,
Node/JavaScript and Bash actually run; `filename` defaults from the language
when left empty.

- **Versions.** Every edit, by the agent or by you, is a new version: the
  original body is version 1, and each save records who made it and why.
  `GET /api/views/{id}/code/versions` lists them; `POST` with `{"body",
  "note"}` records your own edit as the next version, and
  `PUT /api/views/{id}/code/body` with `{"body"}` overwrites the current one
  in place (the panel's plain Save). `GET /api/views/{id}/code/diff?a=1&b=2`
  returns a unified diff between any two versions.
- **Run.** `POST /api/views/{id}/code/run` runs the current body (or a
  `{"body": ...}` override, itself recorded as a new version first) in the
  same sandbox `run_code` uses, see
  [tools-and-capabilities](tools-and-capabilities.md#run_code). Non-runnable
  languages are refused. `GET /api/views/{id}/code/runs` lists the last 10
  results, newest first.
- **Save to project.** `POST /api/views/{id}/code/save` with `{"project_id",
  "path"}` writes the body into that project's own folder; it refuses to
  overwrite an existing file unless `{"overwrite": true}`, and a path that
  would land outside the project folder is rejected.
- **On the view's page.** `/views/{id}` of a code view is the same workbench
  the chat's Code panel has: the editor, Run, Save version, Versions with a
  diff, Save to project, plus the view's recorded runs, and Discuss and Edit
  start a message in the view's own chat (the floating panel).
- **Without a view.** A fenced block of a chat reply gets the same three
  without becoming a view: `POST /api/views/code/run` with `{"language",
  "body"}` (plus `mount_workspace` and `workspace`) runs the text and records
  nothing; `GET`/`POST /api/views/code/snippet/versions` keep a version
  history under a `key` the Code panel chooses (conversation, message and
  block index), with the reply's own text as version 1, and
  `GET /api/views/code/snippet/diff?key=&a=&b=` diffs two of them;
  `POST /api/views/code/save` with `{"project_id", "path", "body"}` writes
  the text into a project.

## Slides

A `slides` view is a deck on a 16:9 stage. The Visualizer builds it one slide at
a time: `slides_style` sets the look, `slides_add` adds or replaces a slide, and
`slides_export` writes the deck into the workspace as a .pptx file.

Each slide has a `layout`, and each layout draws specific fields:

| Layout | What it shows | Fields it needs |
|---|---|---|
| `title` | the cover, on the theme's gradient | `title`, optional `subtitle`, `icon`, `body` |
| `section` | a numbered divider between parts | `title`, optional `subtitle` |
| `content` | a title and a markdown body (the default) | `title` or `body` |
| `two_column` | two markdown columns side by side | `columns`, exactly two |
| `stats` | big numbers | `items` with `value` and `title`, 1 to 4 |
| `cards` | a grid of cards | `items` with `icon`, `title`, `text`, 1 to 6 |
| `timeline` | steps or dates on a line | `items` with `value`, `title`, `text`, 1 to 8 |
| `quote` | a large quotation | `body`, the attribution in `subtitle` |
| `image_left`, `image_right` | a picture beside the text | `image` |
| `image_full` | a full-bleed picture with the title over it | `image` |

A body is markdown: headings, bullets (nested), numbered lists, bold, italic,
code, links and tables. `icon` is one emoji. `image` is a view asset
(`asset://name`, added with `view_add_asset`) or an https URL. `notes` are
speaker notes, shown under the slide and carried into the .pptx. `accent`
overrides the colour for one slide.

The deck's `theme` is one of `light`, `dark`, `corporate`, `ocean`, `sunset`,
`forest` or `mono`; `accent` replaces the theme's accent colour, `footer` is
printed on every content slide, and `numbers: false` hides slide numbers.

A slide the renderer cannot draw is refused when it is written: an unknown
field, a structured `body`, a `stats` slide without items, an image layout
without an image. The error names the slide and says what to change.

In the viewer:

- **Arrows**, PageUp/PageDown, space, Home and End move between slides once
  the deck has focus (click it). **F** presents full screen, **N** shows the
  speaker notes, and the grid button shows every slide as a thumbnail.
- **PDF** prints every slide as a 16:9 page through the browser's print dialog.
- **PPTX** downloads the deck from `GET /api/views/{id}/export/pptx`, built
  by `views/slides_pptx.py` with the same layouts and palette as the browser.

Text that does not fit its box is shrunk: in the browser by measuring, in the
.pptx by an estimate, since PowerPoint only re-fits text when it is edited.
The .pptx embeds only the view's own assets. A remote image URL is not fetched
by the server; the slide gets a placeholder, and the export's
`X-Export-Warnings` header counts the images it left out.

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

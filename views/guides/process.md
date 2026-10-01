## Building a process view

A `process` view is a process model in a `notation` (`flowchart` by default, also `bpmn`, `sequence`) whose execution can be animated with tokens.

- Add nodes and edges with `view_apply_ops`: `{"op":"add","path":"spec.nodes.review","value":{"label":"Review","type":"task"}}`. Node `type` is `start`, `task`, `gateway` or `end`; edges are `{"source","target","label"}`; `spec.lanes` groups nodes by role or system.
- Then `view_set_timeline` so tokens animate along the edges with play, pause, scrub and step.
- Ids are stable lowercase slugs, labels are short verbs or states. Build the happy path first, then the branches off each gateway.
- Controls worth adding: a `select` on `spec.notation`, a `slider` on the timeline speed, a `toggle` per lane's visibility.

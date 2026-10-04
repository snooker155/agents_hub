## Building a graph view

A graph is nodes and edges rendered with Cytoscape; `spec.nodes` and `spec.edges` are keyed maps `{id: {...}}`.

- Add one element per call so the user watches it assemble: `graph_add_node(node_id, label, group, kind)`, `graph_add_edge(source, target, label, edge_id)`. Removing a node with `graph_remove` also drops its incident edges.
- Ids are stable lowercase slugs (`auth`, `billing-db`), labels are short. Use `group` for layers or teams, so a layout and a colour can follow it.
- `graph_set_layout(layout)` picks the Cytoscape layout: `cose` for general graphs, `breadthfirst` for hierarchies and dependency trees, `circle` or `concentric` for small sets, `grid` for matrices.
- Batches and attributes beyond the add tools go through `view_apply_ops`, e.g. `{"op":"update","path":"spec.nodes.auth","value":{"label":"Auth","group":"core"}}`.
- Controls worth adding: a `select` on `spec.layout` for the layout, a `toggle` per group's visibility, a `text` filter when the graph is large.
- Before changing an existing graph, `view_get` the node ids; "the selected node" is `state.selection` in the Active view note.

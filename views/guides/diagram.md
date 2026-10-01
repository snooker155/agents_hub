## Building a diagram view

A `diagram` view is Mermaid source at `spec.mermaid`: flowcharts, sequence diagrams, state machines, Gantt charts, ER diagrams, class diagrams.

- Set the source with `view_apply_ops`: `{"op":"update","path":"spec.mermaid","value":"flowchart LR\n  A[Client] --> B[API]"}`.
- Pick the notation from the question: `flowchart` for steps and decisions, `sequenceDiagram` for who calls whom, `stateDiagram-v2` for states, `gantt` for a schedule, `erDiagram` for data.
- Short node labels, consistent direction (`LR` or `TD`), and no more than about twenty nodes per diagram; split a bigger picture into two views. For an interactive node-and-edge map use a `graph` view instead.

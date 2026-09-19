# Usage

The Visualizer Agent powers the **Visualization Studio** (the Views → Studio page): a live render space paired with a chat. Open a new view (graph / chart / table), then ask in plain language:

- "Map the dependencies between the services in this project."
- "Chart task throughput per agent this week."
- "Show these components as a graph, then group them by layer and add a layout selector."
- "Remove the selected node and reconnect its neighbours."

The agent builds the view step by step on the canvas and adds controls you can tweak. It is also a normal agent: reachable from plain chat, usable as an orchestrator worker ("produce a dashboard for this task's results"), and composable into flows.

Bind a conversation to a view by passing `view_id` on the chat request (the Studio does this automatically); without one, `create_view` still lets it produce a standalone view.

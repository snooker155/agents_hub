# Usage

The Visualizer Agent powers the **Visualization Studio** (the Views → Studio page) for every view kind except 3D scenes and web views, which open with their own specialist: a live render space paired with a chat. Open a new view, then ask in plain language:

- "Map the dependencies between the services in this project."
- "Chart task throughput per agent this week."
- "Show these components as a graph, then group them by layer and add a layout selector."
- "Make a six-slide deck from these release notes."

The agent builds the view step by step on the canvas and adds controls you can tweak. It is also a normal agent: reachable from plain chat, usable as an orchestrator worker ("produce a dashboard for this task's results"), and composable into flows. A 3D or web request that reaches it is passed on to the `modeler_3d` or `web_view_builder` agent, so any chat agent can send every "show me" request here.

Bind a conversation to a view by passing `view_id` on the chat request (the Studio does this automatically); without one, `create_view` still lets it produce a standalone view.

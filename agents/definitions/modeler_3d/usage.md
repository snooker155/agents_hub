# Usage

The 3D Modeler powers the **Visualization Studio** for `scene3d` views and the chat on a 3D view's own page. Open a new 3D view, or ask any chat agent for an object, and talk:

- "Model a coffee mug, 9 cm tall, with a handle."
- "Add a chimney on the left side of the roof and a window on the front wall."
- "Bevel the edges slightly and show it from the front and the top."
- "Export it as glb."

The agent models one operation at a time on the canvas, previews and validates the result, and reports the `view_id`. From plain chat it is reached through the Visualizer, which hands 3D requests over to it; it is also a normal agent, usable as a worker ("build the part described in spec.md") and composable into flows.

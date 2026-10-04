# Usage

The Web View Builder powers the **Visualization Studio** for `html` and `code` views and the chat on such a view's own page. Open a new html view, or ask any chat agent for a page, and talk:

- "Build a page with six CSS loader animations and a slider for their speed."
- "Make a landing page for this project from README.md."
- "Add a dark mode and make the cards wrap on a phone."
- "Give me a Python script that parses these logs into a CSV."

The agent writes the files into the workspace, binds them into the view and reports the `view_id`; a snippet comes back as a code view with run, versions and diff. From plain chat it is reached through the Visualizer, which hands html and code requests over to it; it is also a normal agent, usable as a worker and composable into flows.

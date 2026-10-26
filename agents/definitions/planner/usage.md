Use this agent to turn a project's plans and structure into a task tree, and for everything that runs later or on a schedule. It bridges the project **Architecture** tab (the generated architecture/process views) and the **Tasks** tracker, and it owns the **Plan** and **Deployments** pages: the main agent hands it every scheduling request.

Good fits:
- "Create tasks from this project's process view"
- "Build a task plan from the architecture"
- "Break this project down into actionable tasks and sequence them"
- "Update the plan to cover the new components on the canvas"
- "Remind me tomorrow at 9 to check the release"
- "Every weekday at 9:00 Berlin time, have the analyst summarise yesterday's sales"
- "Run the nightly review flow every night at 2:00 and pause it after two failures"
- "Did last night's deployment run, and why did it fail?"

Poor fits:
- Implementing or running the tasks — this agent plans, it does not execute (use an implementation agent)
- Drawing or editing the architecture/process graph — that is the Architect Agent
- Free-form Q&A unrelated to planning

How to invoke:
- For schedules, just say what should happen and when; no project is needed.
- For a task tree, run it **scoped to a project** so it can read the views (`get_project_graph`) and so new tasks attach to that project. Outside a project scope it has no views to read and no project to attach tasks to.
- It reads existing tasks first, then the views, then creates/sequences tasks — so it extends the plan rather than duplicating it.
- It finishes with a short summary of what it created or changed.

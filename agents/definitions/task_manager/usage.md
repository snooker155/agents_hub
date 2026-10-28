Use this agent to turn plans into a task tree and to keep the tracker in order. It sits beside the board on the **Tasks** page (the whole workspace) and on a project's **Tasks** tab, where it also reads the project's architecture and process views.

Good fits:
- "Create tasks from this project's process view"
- "Build a task plan from the architecture"
- "Break the payments task into steps and put them in order"
- "Which tasks are blocked, and by what?"

Poor fits:
- Doing the tasks: use an implementation agent, or assign the task
- Anything that runs later or on a schedule: that is the Planner
- Drawing or editing the architecture/process graph: that is the Architect Agent

How to invoke:
- Open it from the Tasks page or a project's Tasks tab, or let the main agent hand it the request.
- It reads the existing tasks first, then the views when there is a project, and then creates or changes tasks, so it extends the plan rather than duplicating it.
- It finishes with a short summary of what it created or changed.

# Projects

A project is one codebase or one effort inside a workspace. It gives work a
folder, a structure graph and a task tree.

## What a project adds over a bare workspace

- **A folder** inside the workspace, so several efforts can share a workspace
  without sharing files.
- **A repository binding**, when the project is backed by git.
- **Structure graphs**: a technical architecture view and a business process
  view, built by the Architect Agent from the real source, not from a
  description of it.
- **A task tree**, built by the Planner from those graphs plus any
  documentation.

## The usual sequence

1. Create the project and point it at its folder or repository.
2. Let the **Architect Agent** read the source and build the structure graph.
   It uses read-only tools and updates the canvas as it goes, so you can watch
   the shape appear.
3. Let the **Planner** turn the graph and the docs into tasks and subtasks, with
   ordering and dependencies.
4. Work the tasks, through the orchestrator or by hand.

## Gotchas

- The architect reads what is there. A project pointed at an empty folder
  produces an empty graph, which is correct and unhelpful.
- The planner creates tasks; it does not do them. Nothing starts until a task is
  assigned and started.
- Deleting a project does not delete its folder.

Related: [workspaces](workspaces.md), [tasks](tasks.md), [agents](agents.md).

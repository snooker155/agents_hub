---
title: "Projects"
description: "Organize multiple codebases and initiatives in one workspace"
---

# Projects

A project is a folder and a delivery unit inside a workspace. Use projects to keep several initiatives separate while sharing workspace infrastructure: the same agents, the same models, the same members.

## What you get

Two workspaces, each with one project. Agents run inside project-specific folders while the platform keeps tasks, metadata and structure graphs organized. See how files and outputs stay isolated per project even within a shared workspace.

## Before you start

- `AGENT_EXECUTION_MODE=local`
- Workspace-specific model defaults (optional)
- Project-linked tasks

## Steps

1. Create two workspaces: `example-client-a` and `example-client-b`.
2. In the first workspace, go to **Projects** and create a project named "Marketing Site".
3. Point it at a folder or git repository if you have one. Leave it empty for now.
4. In the second workspace, create a project named "Internal Admin Tool".
5. In each workspace, go to **Tasks** and create project-linked tasks:
   - Under Marketing Site: "Create landing page content structure"
   - Under Internal Admin Tool: "Design admin audit log API"
6. Assign agents and run them. Watch the run logs.
7. Check that files created by agents land in the correct project folder.
8. Optionally, use the **Architect Agent** to analyze a real codebase and build a structure graph.

## Where to read more

Learn about projects, workspaces and how they organize work in [Projects](/guide/projects) and [Workspaces](/guide/workspaces).

![Projects workflow](/screenshots/recipes/projects.png)

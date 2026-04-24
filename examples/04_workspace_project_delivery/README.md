# Example 04: Workspace-Scoped Project Delivery

## Goal

Use separate workspaces and projects to organize multiple initiatives cleanly inside the same Agents Hub instance.

## Best For

- multiple apps or clients
- isolated experiments
- per-project task management

## Main Idea

A workspace gives you an execution boundary, and a project gives you a delivery unit inside that workspace. Agents then operate inside project-specific folders while the platform keeps tasks and metadata organized.

## Recommended Settings

- `AGENT_EXECUTION_MODE=local`
- workspace-specific model defaults where useful
- project-linked tasks

## How To Run

### 1. Create two workspaces

Examples:

- `example-client-a`
- `example-client-b`

### 2. Create one project in each workspace

Example projects:

- `Marketing Site`
- `Internal Admin Tool`

### 3. Add tasks linked to each project

Examples:

- `Create landing page content structure`
- `Design admin audit log API`

### 4. Configure workspace-level model behavior

Optionally give each workspace:

- a different default provider
- a different default model
- different environment values

### 5. Run agents per project

Assign tasks and verify that files and outputs stay organized under the correct workspace/project structure.

## Expected Outcome

You should see clear separation between workspaces, projects, tasks, and outputs.

# Examples

This folder contains step-by-step usage examples for Agents Hub, organized from basic to advanced.

Each example lives in its own directory and includes a dedicated `README.md` with:

- the goal of the example
- when to use it
- recommended settings
- how to run it
- expected outcome

## Examples List

| Order | Folder | Scenario |
|---|---|---|
| 1 | `01_basic_task_assistant` | Create a workspace, create a task, assign an agent manually |
| 2 | `02_managed_multi_agent_delivery` | Run a multi-step orchestrated task flow |
| 3 | `03_human_in_loop_approval` | Use the orchestrator with manual approval before execution |
| 4 | `04_workspace_project_delivery` | Organize several projects and tasks across workspaces |
| 5 | `05_interactive_agent_chat` | Use direct chat with an agent for analysis or guidance |
| 6 | `06_flow_based_automation` | Run a flow-based multi-agent pipeline |
| 7 | `07_docker_isolated_agent_runs` | Execute agents in Docker-managed containers |
| 8 | `08_local_model_lab` | Run the system with Ollama or LM Studio |

## Before You Start

Use the setup from [README.md](../README.md).

For most examples you will need:

1. A configured `.env`
2. Backend running on `http://localhost:8000`
3. Frontend running on `http://localhost:5173`

For Docker-specific examples you will also need Docker installed and running.

## Suggested Order

If you are new to the project, go through the examples in this order:

1. `01_basic_task_assistant`
2. `03_human_in_loop_approval`
3. `02_managed_multi_agent_delivery`
4. `04_workspace_project_delivery`
5. `05_interactive_agent_chat`
6. `06_flow_based_automation`
7. `07_docker_isolated_agent_runs`
8. `08_local_model_lab`

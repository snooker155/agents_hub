# Agent Hub Specification

This document defines the specification for adding and connecting agents to the Unified AI Software Development Hub.

## Overview

The Hub supports three types of agents:
1.  **Local Agents**: Python-based agents running within the same environment as the Orchestrator.
2.  **Remote Agents**: External services that expose a standard HTTP API.
3.  **Custom Agents**: Dynamically created agents based on existing templates (e.g., LangChain tool-calling agents).

## Agent Metadata

Every agent must provide the following metadata:

- `id`: A unique string identifier for the agent.
- `name`: A human-readable name.
- `description`: A brief explanation of what the agent does.
- `domain`: The functional domain of the agent (e.g., `development`, `marketing`, `research`, `operations`).
- `type`: The agent implementation type (`langchain`, `factory`, `http`).
- `entrypoint`:
    - For local agents: `module.path:attribute` (a callable).
    - For remote agents: Set to `remote`.
- `tools`: A list of tool IDs available to the agent (e.g., `read_file`, `search_text`, `assign_and_start_agent_tool`).
- `capacity`: The maximum number of concurrent runs allowed for this agent.
- `is_remote`: Boolean indicating if the agent is served externally.
- `agent_url`: The base URL for remote agents.

## Remote Agent Interface

Remote agents must implement the following standard HTTP endpoints:

### 1. Start a Run
`POST /run`

**Request Body:**
```json
{
  "task_id": "string",
  "instruction": "string",
  "workspace": "string",
  "params": {}
}
```

**Response:**
```json
{
  "run_id": "string"
}
```

### 2. Check Status
`GET /status/{run_id}`

**Response:**
```json
{
  "status": "running | completed | failed",
  "output": "string (optional)",
  "error": "string (optional)",
  "usage": {
    "tokens": 1234,
    "cost": 0.01
  }
}
```

### 3. Stop a Run
`POST /stop/{run_id}`

**Response:**
```json
{
  "status": "stopped"
}
```

### 4. Health Check
`GET /health`

**Response:**
```json
{
  "status": "up",
  "version": "1.0.0"
}
```

## Adding an Agent

### Via `agents.json`
You can manually add an agent by editing `orchestrator/agents/agents.json`.

### Via API
Use the following endpoints to dynamically add agents:
- `POST /api/agents/connect`: Connect a remote agent.
- `POST /api/agents/create`: Create a custom agent from a template.
- `POST /api/agents/clone`: Clone an existing agent with modifications.

## Resource Management

The Hub tracks agent availability based on the `capacity` field. When an agent's active runs reach its capacity, further assignments will be blocked until a run completes.

Future versions will support tracking of CPU, Memory, and Token usage across all domains.

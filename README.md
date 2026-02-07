# Unified AI Software Development Agency

This repository combines two powerful AI agent projects into a single, unified framework for automated software development. It features a sophisticated Orchestrator, specialized SWE agents for codebase modification, and a complete "Agent Factory" with a visual graph-based workflow.

## Key Features

- **Unified Orchestrator**: Manage complex tasks and subtasks with a centralized agent that coordinates specialized executors.
- **SWE Agent**: An expert developer agent equipped with filesystem and patching tools to solve specific engineering tasks.
- **Agent Factory**: A multi-agent system comprising PM, BA, SD, TL, Dev, QA, and Ops agents that can follow a full development lifecycle.
- **Visual Graph Editor**: Design and execute custom agent workflows via a React Flow-powered dashboard.
- **Workspace Management**: Isolated environments for each task where all artifacts (docs, code, logs) are stored.

## Repository Structure

- `orchestrator/`: Core task management and agent coordination logic.
- `swe_agent/`: Specialized SWE agent with filesystem and patching tools.
- `agents/`: Factory agents (PM, BA, SD, TL, Backend, Frontend, QA, DevOps).
- `dashboard/`: Unified FastAPI backend and React/Vite/Tailwind frontend.
- `tasks/`: Task data models and storage.
- `common/`: Shared utilities and configuration.
- `workspaces/`: (Generated) Isolated working directories for agent tasks.

## Setup

### Prerequisites

- Python 3.10+
- Node.js & npm
- OpenAI API Key (set as `OPENAI_API_KEY` environment variable)

### Installation

1. **Install Python dependencies:**
   ```bash
   pip install -r dashboard/backend/requirements.txt
   pip install -r requirements.txt
   ```

2. **Install Frontend dependencies:**
   ```bash
   cd dashboard/frontend
   npm install
   ```

## Running the Project

### 1. Start the Dashboard

**Backend:**
```bash
export PYTHONPATH=$PYTHONPATH:.
python3 dashboard/backend/main.py
```
The API will be available at `http://localhost:8000`.

**Frontend:**
```bash
cd dashboard/frontend
npm run dev
```
The dashboard will be available at `http://localhost:5173`.

### 2. CLI Usage

You can also run agents directly from the CLI:

**SWE Agent:**
```bash
python3 -m swe_agent.cli text "Add a README to the project" --workspace /path/to/ws
```

**Factory Graph:**
```bash
python3 run_graph.py --desc "Build a simple weather app" --workspace /path/to/ws
```

## Agent Factory Workflow

The default Factory Graph follows this process:
1. **PM Intake**: Clarifies project requirements.
2. **BA Generate BRD**: Produces a Business Requirements Document.
3. **SD Generate Spec**: Creates technical specifications and OpenAPI contracts.
4. **TL Choose Stack & Split**: Decisions on tech stack and task breakdown.
5. **Dev Execution**: Backend and Frontend implementation.
6. **QA & Ops**: Automated testing and deployment artifacts.

You can customize this flow in the **Agent Factory** tab of the dashboard.

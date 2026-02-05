# Telecom AI Agent Orchestrator

This project is a network orchestration and monitoring system that uses AI agents for diagnostics. It consists of a FastAPI backend that manages and simulates a network topology, and a React frontend for visualization and control.

## Project Structure

- `node_orchestrator/`: The Python backend.
  - `app.py`: Main FastAPI application with WebSocket support for real-time updates.
  - `agent_service.py`: Multi-agent service using LangChain and Ollama for node diagnostics.
  - `topology.json`: Storage for the network topology.
- `telecom-agents-fe/`: The React frontend.
  - Built with Vite, ReactFlow for graph visualization, and Tailwind CSS.
- `architecture.md`: Visual diagram of the system architecture.

## Features

- **Dynamic Topology**: Add, remove, link, and unlink nodes (routers, switches, access points).
- **Network Simulation**: Real-time simulation of traffic (speed, volume) and spontaneous faults.
- **AI Diagnostics**: Dedicated AI agents for each node that analyze logs and metrics to identify problems.
- **Real-time UI**: WebSocket-driven dashboard showing the network state, logs, and agent reasoning.

## Getting Started

### Backend Setup

1. Navigate to the `node_orchestrator/` directory.
2. Install dependencies:
   ```bash
   pip install -r ../requirements.txt
   ```
3. Run the backend:
   ```bash
   uvicorn app:app --reload --port 8000
   ```

### AI Agent Setup

1. Ensure [Ollama](https://ollama.ai/) is installed and running.
2. Pull the required model:
   ```bash
   ollama pull llama3.1:8b
   ```
3. Run the agent service:
   ```bash
   python node_orchestrator/agent_service.py
   ```

### Frontend Setup

1. Navigate to the `telecom-agents-fe/` directory.
2. Install dependencies:
   ```bash
   npm install
   ```
3. Run the frontend:
   ```bash
   npm run dev
   ```
   The frontend will be available at `http://localhost:5174`.

## Simulation Controls

In the UI, you can select nodes and toggle their "Simulate" status. When enabled, nodes will periodically update their traffic metrics and may occasionally transition to "degraded" or "error" states based on configured probabilities in the backend.

# Autonomous Network Self-Evolution Framework

This repository implements a closed-loop system where an **Autonomous Network (AN)** manages itself in real-time and **evolves its own code and capabilities** when encountering limitations.

## 🏗️ Unified Architecture

The framework is organized into two primary domains: the **Operational Domain** (Telecom) and the **Evolutionary Domain** (Software Development), linked by a shared core and a unified dashboard.

Detailed documentation: [UNIFIED_ARCHITECTURE.md](./UNIFIED_ARCHITECTURE.md)

---

## 📂 Repository Structure

```text
/
├── core/                   # Shared libraries & base classes
│   ├── agents/             # Unified Agent base classes (BaseAgent)
│   ├── communication/      # Unified Event Bus (sync/async)
│   └── models/             # Shared Pydantic models (Task, Incident, Requirement, etc.)
├── domains/                # Domain-specific logic
│   ├── telecom/            # Operational Domain (Network)
│   │   ├── simulator/      # ai-ran-sim: High-fidelity O-RAN Simulator
│   │   ├── topology/       # telekom-an-simulator: Topology & Diagnostics
│   │   └── controller/     # an_agent: Sifakis-inspired Cognitive Controller
│   └── swe/                # Evolutionary Domain (Software Engineering)
│       ├── factory/        # code_dev: Multi-agent Development Factory (LangGraph)
│       └── orchestrator/   # test-swe-agent: Task Orchestrator & SWE Agent
├── dashboard/              # Unified Web UI
│   ├── backend/            # Consolidated FastAPI service
│   └── frontend/           # Unified React/Tailwind Dashboard
└── workspace/              # Shared scratchpad for code evolution
    └── tasks/              # Global task storage
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.12+
- Node.js 18+
- Ollama (for local LLM execution)

### 1. Backend Setup
Install core dependencies and domain-specific requirements:
```bash
export PYTHONPATH=$PYTHONPATH:.
pip install -r domains/swe/orchestrator/requirements.txt
pip install -r domains/telecom/simulator/backend/requirements.txt
```

Run the unified dashboard API:
```bash
python dashboard/backend/main.py
```

### 2. Frontend Setup
```bash
cd dashboard/frontend
npm install
npm run dev
```

---

## 🛠️ Modules

### Core
Shared infrastructure that ensures consistency across domains. The `core.models` package unifies how Tasks and Incidents are represented, allowing the Evolutionary domain to react to Operational anomalies.

### Telecom Domain
- **Simulator**: Provides the environment for base stations and users.
- **Topology**: Manages the network graph and distributed diagnostic agents.
- **Controller**: Implements proactive and reactive loops for network optimization.

### SWE Domain
- **Factory**: A LangGraph-based team of agents (PM, BA, SD, Dev, QA) that designs and implements features.
- **Orchestrator**: Manages the lifecycle of evolution tasks, coordinating between the network's needs and the SWE agents' actions.

---

## 📜 License
Internal Research Project.

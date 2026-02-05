                   ┌────────────────────────────┐
                   │       Frontend (UI)        │
                   │  - Graph (D3.js / Cytoscape)│
                   │  - Node Info               │
                   │  - Agent Logs              │
                   └────────────┬───────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ Central Backend (API)  │
                    │ - FastAPI / gRPC       │
                    │ - Orchestration        │
                    │ - Agent log routing    │
                    └────┬─────────┬─────────┘
                         │         │
          ┌──────────────┘         └──────────────┐
          ▼                                       ▼
┌──────────────────────┐               ┌──────────────────────┐
│   Database (SQLite)  │               │   Agent Communication│
│ - Logs               │               │  (Redis pub/sub)     │
│ - State              │               └────────┬─────────────┘
└──────────────────────┘                        │
                                               ▼
                                      ┌─────────────────────┐
                                      │ AI Agent Engine     │
                                      │ - LangChain + LLM   │
                                      │ - Decision Making   │
                                      │ - Messaging         │
                                      └────────┬────────────┘
                                               │
                            ┌──────────────────┴────────────────────┐
                            ▼                                       ▼
                ┌──────────────────────┐               ┌──────────────────────┐
                │  Node Simulator 1    │               │  Node Simulator N    │
                │ - Simulated state    │               │ - Events / failures  │
                │ - Fault injection    │               │ - Metrics            │
                └──────────────────────┘               └──────────────────────┘

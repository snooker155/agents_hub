# AN-Agent Dashboard

Local dashboard that visualizes the execution flow and live telemetry/decision data.

## Quick start (dev, separate frontend and backend)

1) Install backend dependencies:

```bash
pip install -r dashboard/backend/requirements.txt
```

2) Start the backend API (no static assets served):

```bash
uvicorn dashboard.backend.server:app --reload --port 8080
```

3) Start the React frontend dev server:

```bash
cd dashboard/frontend
npm install
npm run dev
```

4) Open in your browser:

```
http://127.0.0.1:5173
```

Notes:
- The Vite dev server proxies `/api` requests to `http://127.0.0.1:8080` (see `dashboard/frontend/vite.config.js`).
- The backend does not serve static assets in dev. Use `npm run dev` for the UI.
- For a production build, run `npm run build` and serve the built assets with any static server or wire them into the backend if desired.

## Frontend

The UI is a React application located in `dashboard/frontend`.

Key features:
- **Overview**: Real-time summary metrics (BLER, Throughput, Latency) and active process list.
- **Experiments**: Library of available experiments with options to launch them with specific configurations.
- **Analytics**: Visualization of action distribution, service modes, and KPI trends (using Recharts).
- **Recent Decisions**: Detailed log of agent decisions and their rationales.

## Data source

The dashboard reads from the local sqlite DB at `./data/an_agent.db` by default.
To override:

```bash
AN_AGENT_DB=/path/to/your.db uvicorn dashboard.backend.server:app --reload
```

## API endpoints

- `GET /api/health`
- `GET /api/experiments` — list available experiments (scripts/configs)
- `GET /api/processes` — list currently running experiment-related processes
- `POST /api/experiment/run` — body: `{ "script": "...", "config": "...", "ticks": 500 }`
- `POST /api/experiment/stop` — body: `{ "pid": 12345 }`
- `GET /api/metrics`
- `GET /api/analytics`
- `GET /api/observations?limit=100`
- `GET /api/decisions?limit=50`

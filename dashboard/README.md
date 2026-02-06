# Unified Dashboard

This directory contains the unified dashboard for the Autonomous Network Self-Evolution Framework.

## Structure

- `backend/`: FastAPI unified backend service.
- `frontend/`: React + Tailwind unified frontend.

## Integration

The dashboard integrates the following components:

1. **Operational View**: Real-time network telemetry from `ai-ran-sim` and topology diagnostics from `telekom-an-simulator`.
2. **Cognitive Control**: Insights from `an_agent`'s reactive and proactive loops.
3. **Evolutionary View**: Tracking of development tasks and code evolution from `code_dev` and `test-swe-agent`.

## Running the Dashboard

```bash
cd dashboard/backend
python main.py
```

```bash
cd dashboard/frontend
npm install
npm run dev
```

# Remote Agent Example

This is a standalone example of a remote agent service that can be connected to the Unified AI Software Development Hub.

## How it works

This service implements the standard Remote Agent Interface defined in `AGENT_HUB_SPEC.md`:
- `GET /health`: Health check and metadata.
- `POST /run`: Start a new task execution.
- `GET /status/{run_id}`: Poll for status and results.
- `POST /stop/{run_id}`: Terminate a running task.

## Running the Agent

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Start the service:
   ```bash
   python main.py
   ```
   The agent will be available at `http://localhost:8080`.

## Connecting to the Hub

Once the agent is running, you can connect it to the Hub via the Dashboard:

1. Go to the **Agent Nodes** page.
2. Click **Connect Remote**.
3. Enter the following details:
   - **ID**: `research-remote-1`
   - **Name**: `Remote Research Specialist`
   - **Domain**: `research`
   - **Agent URL**: `http://localhost:8080`
   - **Capacity**: `5`
4. Click **Establish Connection**.

Alternatively, you can apply a YAML manifest via the **Apply YAML** page:

```yaml
kind: Agent
metadata:
  name: research-remote-1
spec:
  displayName: Remote Research Specialist
  domain: research
  description: "External agent specializing in market research and data analysis."
  type: http
  isRemote: true
  agentUrl: "http://localhost:8080"
  capacity: 5
  capabilities:
    - web.search
    - data.analysis
```

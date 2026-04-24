# Example 07: Docker-Isolated Agent Runs

## Goal

Run agents in Docker-managed containers instead of local subprocesses.

## Best For

- runtime isolation
- reproducible agent environments
- containerized execution testing

## Main Idea

The application can run agents either locally or inside Docker containers. This example focuses on Docker-based agent execution, not just running the dashboard itself in containers.

## Recommended Settings

- `AGENT_EXECUTION_MODE=docker`
- `AGENT_DOCKER_IMAGE=agents-hub/base:latest`
- `AGENT_DOCKER_NETWORK=agents-hub` if needed

## How To Run

### 1. Build the base agent image

```bash
docker build -t agents-hub/base:latest -f Dockerfile.agents .
```

### 2. Update `.env`

Example:

```env
AGENT_EXECUTION_MODE=docker
AGENT_DOCKER_IMAGE=agents-hub/base:latest
AGENT_DOCKER_NETWORK=agents-hub
```

### 3. Start the app

Run backend and frontend normally.

### 4. Open the Containers page

Inspect:

- base image availability
- per-agent image status
- container lifecycle

### 5. Trigger an agent run

Create a task and assign an agent.

### 6. Observe container behavior

Review:

- container list
- container logs
- task run status

## Expected Outcome

You should see the assigned agent run inside a managed Docker container instead of directly on the host.

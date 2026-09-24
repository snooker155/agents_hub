---
title: "Docker"
description: "Run agents in isolated containers instead of local subprocesses"
---

# Docker

Docker mode runs each agent in its own container. The container has its own filesystem, network and lifetime. Use Docker for isolation, reproducibility and safety.

## What you get

An agent run inside a managed Docker container instead of a local subprocess. The Containers page shows the container lifecycle. The agent's logs are captured the same way as a local run.

## Before you start

- Docker installed and running
- These environment variables:
  - `AGENT_EXECUTION_MODE=docker`
  - `AGENT_DOCKER_IMAGE=agents-hub/base:latest`
  - `AGENT_DOCKER_NETWORK=agents-hub` (if needed)

## Steps

1. Build the base agent image:

```bash
docker build -t agents-hub/base:latest -f Dockerfile.agents .
```

2. Update `.env` with the settings above.

3. Start the backend and frontend normally.

4. Go to **Containers** and check that the base image is available.

5. Create a task and assign an agent.

6. Go to **Containers** to watch the agent's container appear, run and finish.

7. Check the container log to see the full output.

8. Inspect the **Containers** page to see running and stopped containers.

9. Click a container to read its full log.

10. Verify that the task result is the same as with local execution.

## Where to read more

Learn how Docker isolation works, including images, mounts and state transport in [Containers](/guide/containers). See execution modes and settings in [Settings](/guide/settings).

![Docker workflow](/screenshots/recipes/docker.png)

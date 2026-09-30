# syntax=docker/dockerfile:1
# The two Python images of Agents Hub, built from one file so the dependency
# stack is installed once and shared between them:
#
#   backend   the service (API and worker roles, docker-compose.yml, the Helm
#             chart):          docker build --target backend -t agents-hub-backend .
#   agents    the base image agents run from in their own containers,
#             agents-hub/base:latest (managers/container_manager.py builds it):
#                              docker build --target agents -t agents-hub/base:latest .
#
# Stages: deps-false installs the pinned stack (requirements.lock plus the
# Postgres driver); deps-true adds the RAG extras on top of it (torch,
# sentence-transformers, chromadb, the remote vector stores); WITH_RAG chooses
# which of the two the final images build on. The RAG stack is a gigabyte of
# wheels that only the embedding and vector-store features import (docs/memory.md),
# so it is off by default; the release workflow publishes both flavours of
# each image, X.Y.Z and X.Y.Z-rag (docs/deployment.md "Releases"), and a local
# build turns it on with --build-arg WITH_RAG=true (docker-compose.yml reads
# WITH_RAG from .env for that).
ARG WITH_RAG=false

# ---------- the shared dependency stack ----------
FROM python:3.11-slim AS deps-false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# build-essential stays in the image: an agent environment (an image derived
# from the agents target with extra pip packages, managers/container_manager.py
# ensure_environment_image) may need a compiler for a package without a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# requirements.lock is the pinned resolution of requirements-agents.txt,
# dashboard/backend/requirements.txt and requirements-cli.txt (it is
# universal, so the same file installs cleanly on every platform); refresh it
# with the command in its header when one of those three files changes.
# The Postgres driver rides along: a few megabytes, and inert until
# AGENTS_HUB_DATABASE_URL names a postgresql:// database (docs/scaling.md).
# It is not part of requirements.lock (see that file's header), so it is
# still installed from its own requirement file.
COPY requirements.lock requirements-postgres.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.lock -r /tmp/requirements-postgres.txt

# ---------- the same stack plus the RAG extras ----------
# Torch comes from the CPU-only index first so the image does not carry the
# CUDA runtime it can never use; the fallback covers architectures that index
# does not publish.
FROM deps-false AS deps-true

COPY requirements-rag.txt /tmp/requirements-rag.txt
RUN (pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
        || pip install --no-cache-dir torch) \
    && pip install --no-cache-dir -r /tmp/requirements-rag.txt

# ---------- Docker CLI, for the backend only ----------
# Only the client, so that with /var/run/docker.sock bind-mounted the backend
# can drive the host daemon (managers/container_manager.py) and run agents in
# their own containers. The daemon itself is never installed. Its own stage,
# so a source change never downloads it again.
FROM python:3.11-slim AS docker-cli

ARG TARGETARCH
ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    case "${TARGETARCH:-amd64}" in \
        amd64) docker_arch=x86_64 ;; \
        arm64) docker_arch=aarch64 ;; \
        *) docker_arch="${TARGETARCH}" ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp docker/docker; \
    mv /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker /tmp/docker.tgz; \
    docker --version

# ============================================================
# backend: the service
# ============================================================
FROM deps-${WITH_RAG} AS backend

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

# ---------- security: non-root user ----------
# Root is only needed for apt-get and pip install above. The docker CLI is
# still usable from here: docker-compose.yml adds this user to the socket's
# host group via group_add, so the backend can keep driving the daemon
# without running as root itself.
RUN groupadd -g 1000 hub \
    && useradd -u 1000 -g hub -m hub --shell /bin/bash

COPY --chown=hub:hub . /app

USER hub

# The release this image is (common/version.py): set by the release workflow
# from the tag, so /api/system/version answers correctly even though a
# compose deployment bind mounts a checkout over /app.
ARG AGENTS_HUB_VERSION=""
ARG AGENTS_HUB_GIT_SHA=""
ENV AGENTS_HUB_VERSION=$AGENTS_HUB_VERSION \
    AGENTS_HUB_GIT_SHA=$AGENTS_HUB_GIT_SHA
LABEL org.opencontainers.image.title="agents-hub-backend" \
      org.opencontainers.image.version=$AGENTS_HUB_VERSION \
      org.opencontainers.image.revision=$AGENTS_HUB_GIT_SHA

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/ || exit 1

# No --reload: the reloader restarts the process on any write under /app,
# and the image ships the source rather than mounting it, so there is
# nothing to watch. Restart the container to pick up new code.
CMD ["python", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================
# agents: the base image every agent container runs from
# ============================================================
# The agent identity (--agent-id) is supplied at runtime via CMD arguments.
# Per-agent images are derived from this one by the ContainerManager
# (managers/container_manager.py): they just set AGENT_ID and CMD, no
# additional layers are required. At runtime only what the agent needs is
# mounted, over the source baked into the image:
#   /app/.agents_hub  <- state, run records, logs
#   /app/tasks        <- task storage
#   /workspace        <- the run's workspace, when it has one
#
# Security: the Docker socket is NEVER mounted inside these containers, and
# the docker CLI is not installed in them. Agents can talk to other containers
# on the "agents-hub" bridge network (by container name) but cannot spawn new
# ones.
FROM deps-${WITH_RAG} AS agents

LABEL agents-hub.managed="true"
LABEL agents-hub.image-type="base"

# Containers must NOT try to launch further containers.
ENV AGENT_EXECUTION_MODE=local
# A one-shot run container mounts the root filesystem read-only (see
# managers/container_manager.py build_run_command); /home/agent below then
# reads fine but cannot be written to. /tmp is the one place every container
# (hardened or not) can always write, so HOME lives there instead: the
# entrypoint below creates it, since a read-only container's tmpfs starts
# empty and nothing baked into the image at that path would survive it.
ENV HOME=/tmp/agent-home

# ---------- security: non-root user ----------
RUN groupadd -g 1001 agent \
    && useradd -u 1001 -g agent -m agent --shell /bin/bash

# The source, so the image is self-contained. In production the live source
# is volume-mounted over /app, which takes precedence over these layers.
COPY --chown=agent:agent . /app

USER agent

ARG AGENTS_HUB_VERSION=""
ARG AGENTS_HUB_GIT_SHA=""
ENV AGENTS_HUB_VERSION=$AGENTS_HUB_VERSION \
    AGENTS_HUB_GIT_SHA=$AGENTS_HUB_GIT_SHA
LABEL org.opencontainers.image.title="agents-hub-agents" \
      org.opencontainers.image.version=$AGENTS_HUB_VERSION \
      org.opencontainers.image.revision=$AGENTS_HUB_GIT_SHA

# Creates $HOME (on tmpfs when the container is read-only, on the writable
# layer otherwise) before handing off to the real command; no package added,
# /bin/sh ships with the base image. "--" separates the entrypoint's own
# argv from "$@", which is CMD or whatever `docker run <image> ...` appended.
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p \"$HOME\" && exec \"$@\"", "--"]

# Default command, overridden by per-agent images (they add --agent-id);
# runtime.instance_run is the entrypoint of a run inside a container.
CMD ["python", "-m", "runtime.instance_run"]

# The dashboard frontend has its own image: dashboard/frontend/Dockerfile
# (Vite dev server, or nginx serving the production bundle). It shares nothing
# with these two, so it also keeps its own build context. The model runtime
# and the browser service likewise: deploy/models/Dockerfile and
# deploy/browser/Dockerfile.

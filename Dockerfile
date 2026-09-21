FROM python:3.11-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# ---------- Docker CLI ----------
# Only the client, so that with /var/run/docker.sock bind-mounted this
# container can drive the host daemon (managers/container_manager.py) and run
# agents in their own containers. The daemon itself is never installed here.
ARG TARGETARCH
ARG DOCKER_CLI_VERSION=27.5.1
RUN set -eux; \
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

COPY dashboard/backend/requirements.txt /tmp/requirements-backend.txt
COPY requirements-agents.txt /tmp/requirements-agents.txt

RUN pip install --no-cache-dir -r /tmp/requirements-backend.txt \
    && pip install --no-cache-dir -r /tmp/requirements-agents.txt

# ---------- RAG extras ----------
# Embeddings and vector stores. Torch comes from the CPU-only index first so
# the image does not carry the CUDA runtime it can never use; the fallback
# covers architectures that index does not publish.
# Build with --build-arg WITH_RAG=false to leave them out.
ARG WITH_RAG=true
COPY requirements-rag.txt /tmp/requirements-rag.txt
RUN if [ "$WITH_RAG" = "true" ]; then \
        (pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
            || pip install --no-cache-dir torch) \
        && pip install --no-cache-dir -r /tmp/requirements-rag.txt; \
    fi

# ---------- security: non-root user ----------
# Root is only needed for apt-get and pip install above. The docker CLI is
# still usable from here: docker-compose.yml adds this user to the socket's
# host group via group_add, so the backend can keep driving the daemon
# without running as root itself.
RUN groupadd -g 1000 hub \
    && useradd -u 1000 -g hub -m hub --shell /bin/bash

COPY . /app
RUN chown -R hub:hub /app

USER hub

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/ || exit 1

# No --reload: the reloader restarts the process on any write under /app,
# and the image ships the source rather than mounting it, so there is
# nothing to watch. Restart the container to pick up new code.
CMD ["python", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# The dashboard frontend has its own image: dashboard/frontend/Dockerfile
# (Vite dev server, or nginx serving the production bundle). It shares nothing
# with this one, so it also keeps its own build context.

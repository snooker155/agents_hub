# agents-hub (Helm)

The HA topology (docs/deployment.md) for a real cluster: a `backend`
Deployment in the `api` role, a `worker` Deployment that claims launches from
the queue, and the `frontend` dashboard, all pointed at an external Postgres,
an external Redis and an S3 compatible bucket. This chart installs none of
those three itself — see "What this chart does not run" below.

## Install

```bash
# 1. Build and push the two images this chart deploys (or point image.repository
#    / frontend.image.repository at images you already built and pushed):
docker build -t your-registry/agents-hub-backend:1.0.0 --target backend .
docker build -t your-registry/agents-hub-frontend:1.0.0 --target nginx dashboard/frontend
docker push your-registry/agents-hub-backend:1.0.0
docker push your-registry/agents-hub-frontend:1.0.0

# 2. Install, pointing at your own Postgres, Redis and object store. Keep the
#    values file with real secrets out of version control.
cat > my-values.yaml <<'EOF'
image:
  repository: your-registry/agents-hub-backend
  tag: "1.0.0"
frontend:
  image:
    repository: your-registry/agents-hub-frontend
    tag: "1.0.0"
secret:
  OPENAI_API_KEY: "sk-..."
  AGENTS_HUB_DATABASE_URL: "postgresql://agents_hub:password@postgres.example.internal:5432/agents_hub"
  AGENTS_HUB_BROKER_URL: "redis://redis.example.internal:6379/0"
  AGENTS_HUB_BLOB_URL: "s3://agents-hub/"
  AGENTS_HUB_BLOB_ENDPOINT: "https://s3.example.com"
  AWS_ACCESS_KEY_ID: "..."
  AWS_SECRET_ACCESS_KEY: "..."
EOF

helm install agents-hub deploy/helm/agents-hub -f my-values.yaml
```

Upgrading after a values or image change:

```bash
helm upgrade agents-hub deploy/helm/agents-hub -f my-values.yaml
```

Rendering without installing, to review what would be applied:

```bash
helm template agents-hub deploy/helm/agents-hub -f my-values.yaml
```

## What this chart does not run

No Postgres, no Redis, no MinIO or S3: `docs/scaling.md` and
`docs/workers.md` already assume Postgres and Redis are reachable URLs, not
processes this deployment owns, and a cluster is exactly the place that
assumption pays off. Bring your own managed services, or install them
alongside with their own charts (Bitnami's `postgresql` and `redis` charts
are the usual choice), and pass the resulting connection strings into
`secret.AGENTS_HUB_DATABASE_URL`, `secret.AGENTS_HUB_BROKER_URL` and
`secret.AGENTS_HUB_BLOB_URL` / `secret.AGENTS_HUB_BLOB_ENDPOINT`.

## Roles

| Deployment | `AGENTS_HUB_ROLE` | What it needs |
|---|---|---|
| `backend` | `api` | Reaches Postgres and Redis; serves `/livez` and `/readyz`, which the probes in `values.yaml` (`backend.livenessProbe`, `backend.readinessProbe`) already point at. |
| `worker` | `worker` | Reaches Postgres and Redis; no Docker socket (see below), so `worker.workerModes` defaults to `local`. |
| `frontend` | — | Only needs the backend Service's cluster DNS name, wired in automatically. |

## Docker-in-cluster is not available

`AGENT_EXECUTION_MODE=docker` and a worker's Docker launch mode both need a
Docker socket to drive. There is no single host daemon in a cluster the way
there is on one compose host, and mounting a node's socket into a pod is a
privilege escalation (root-equivalent access to that node) most clusters
correctly refuse to allow. This chart does not attempt it: `worker.workerModes`
defaults to `local`, so every launch runs as a subprocess of the worker pod
instead of a container. Running agents in containers under Kubernetes is a
separate design (a Job or Pod per agent run, not a `docker run` from inside
another pod) and is not what this chart does.

## The shared state volume

`.agents_hub/` (run logs, workspaces, view assets, generated Dockerfiles —
docs/installation.md's "Where state lives") is still files on disk, and
every backend and worker pod needs to see the same ones. `templates/pvc.yaml`
creates one PVC (`persistence.*` in `values.yaml`) mounted at
`AGENTS_HUB_ROOT=/data/agents_hub` in every pod. This has to be
**ReadWriteMany**, from a storage class that actually supports being mounted
on several nodes at once (NFS, EFS, Filestore, CephFS, …); the default block
storage classes on most clouds are ReadWriteOnce and a second pod scheduled
on another node will sit in `ContainerCreating` forever.

Once `AGENTS_HUB_BLOB_URL` names an S3 compatible bucket (`common/blobs.py`),
these files move there instead, and the PVC stops being load-bearing for
correctness — it can shrink to a small per-pod cache or be turned off
(`persistence.enabled: false`) once that migration is complete. The database
is not one of these files: with `AGENTS_HUB_DATABASE_URL` set, it already
lives in Postgres, off this volume, exactly as in the compose `ha` profile.

## Values

See `values.yaml` for the full set with comments; the ones you are most
likely to change on a real install:

- `image.repository` / `image.tag`, `frontend.image.repository` / `frontend.image.tag`
- `backend.replicas`, `worker.replicas`
- `secret.AGENTS_HUB_DATABASE_URL`, `secret.AGENTS_HUB_BROKER_URL`
- `secret.AGENTS_HUB_BLOB_URL`, `secret.AGENTS_HUB_BLOB_ENDPOINT`, `secret.AWS_ACCESS_KEY_ID`, `secret.AWS_SECRET_ACCESS_KEY`
- `persistence.storageClassName`, `persistence.size`
- `ingress.enabled`, `ingress.host`, `ingress.className`

Related: [docs/deployment.md](../../../docs/deployment.md),
[docs/scaling.md](../../../docs/scaling.md),
[docs/workers.md](../../../docs/workers.md).

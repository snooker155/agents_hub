# Deploying for high availability

[scaling](scaling.md) and [workers](workers.md) describe the pieces one at a
time: the broker bridge, Postgres, roles, the launch queue, checkpoints,
leases. This page is the pieces put together, as two things you can actually
run: the compose `ha` profile for one machine (or a small cluster of
machines sharing one bind mount), and a Helm chart for a real cluster. Both
land on the same shape — an `api` backend, `worker` processes, one shared
database — because that shape is what [workers](workers.md) already defines;
nothing here changes it, it just wires it up.

Nothing on this page is needed for the default deployment: one `backend`
replica, `ah up` or `docker compose up`, SQLite on disk. Read this only once
you are actually running more than one backend, or backends and workers on
different hosts.

## The compose `ha` profile

```bash
docker compose --profile ha up --build
```

This starts, in one command, everything [scaling](scaling.md) and
[workers](workers.md) ask for: `postgres`, `redis` (the broker bridge), a
bundled `minio` (an S3 compatible object store) with a `minio-init` job that
creates its bucket, two `backend-api` replicas, and two `worker` replicas.
It also still starts the file's default `backend` and `frontend` — a compose
profile can only add services, not remove the ones that have no profile at
all — so scale the single-role backend to zero once the rest is up if you
want a clean HA topology and nothing else:

```bash
docker compose --profile ha up --build --scale backend=0
```

### `.env` for this profile

```env
AGENTS_HUB_DATABASE_URL=postgresql://agents_hub:agents_hub@postgres:5432/agents_hub
AGENTS_HUB_BROKER_URL=redis://redis:6379/0
AGENTS_HUB_BLOB_URL=s3://agents-hub/
AGENTS_HUB_BLOB_ENDPOINT=http://minio:9000
AWS_ACCESS_KEY_ID=agents_hub
AWS_SECRET_ACCESS_KEY=agents_hub_minio
BACKEND_SERVERS=backend-api:8000
```

The MinIO credentials are `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in
`docker-compose.yml` (defaulted to `agents_hub` / `agents_hub_minio`); the
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` pair above has to match
whatever you set them to, since `common/blobs.py` reads the object store
through the ordinary S3 client credential chain, and MinIO speaks the S3
API. `BACKEND_SERVERS=backend-api:8000` is what makes `frontend`'s nginx
balance over the `backend-api` replicas instead of the file's single
`backend`; without it, the dashboard would still talk to the one non-HA
backend even with the rest of the profile running.

### What each service in the profile needs

| Service | Role | Needs |
|---|---|---|
| `backend-api` | `api` | Postgres, Redis (for the broker bridge), the repo bind mount and `HOST_PROJECT_ROOT` (same as `backend`), the Docker socket (to build/run agent images the way `backend` does) |
| `worker` | `worker` | Postgres, Redis, the repo bind mount, the Docker socket unless `AGENTS_HUB_WORKER_MODES=local` |
| `minio` / `minio-init` | — | Nothing external; `minio-init` just waits for `minio` to answer healthy and creates the bucket |
| `frontend` | — | `BACKEND_SERVERS` pointed at `backend-api:8000` |

`backend-api` and `worker` share their build and mounts through one YAML
anchor (`x-backend` at the top of `docker-compose.yml`), so they stay in
step with `backend`'s Dockerfile target and volumes instead of drifting.
Health: `backend-api` keeps the image's own `HEALTHCHECK`
(`curl http://localhost:8000/`); dedicated `/livez` and `/readyz` routes are
landing separately, and the compose file will switch to them once they are
in rather than depend on an endpoint that might not exist yet. `worker`'s
inherited healthcheck is disabled outright, since it serves no HTTP and
would otherwise sit reported "unhealthy" for a reason that means nothing.

Validate the profile without starting anything:

```bash
docker compose --profile ha config     # renders the full ha topology
docker compose config                  # the default profile: unchanged
```

## The Helm chart

`deploy/helm/agents-hub/` is the same shape for a cluster: a `backend`
Deployment (`AGENTS_HUB_ROLE=api`, liveness `/livez`, readiness `/readyz`),
a `worker` Deployment, and a `frontend` Deployment, wired to Services and an
optional Ingress. Unlike the compose profile, it runs **no** Postgres, Redis
or object store of its own — a cluster is exactly the place to point those
at managed services or a separately installed chart instead of running them
as pods here.

```bash
helm install agents-hub deploy/helm/agents-hub -f my-values.yaml
```

See `deploy/helm/agents-hub/README.md` for the full install walkthrough,
including building and pushing the two images. The short version of what
differs from the compose profile:

- **No Docker socket.** There is no one host daemon in a cluster, and
  mounting a node's socket into a pod is a privilege escalation most
  clusters refuse. `worker.workerModes` defaults to `local`: every launch
  runs as a subprocess of the worker pod, never a container.
- **Shared state is a PersistentVolumeClaim**, not a bind mount. Every
  backend and worker pod mounts the same PVC at `AGENTS_HUB_ROOT`, which
  means the storage class has to support **ReadWriteMany** (NFS, EFS,
  Filestore, CephFS, …) — the default block storage classes on most clouds
  are ReadWriteOnce and will not schedule a second pod onto another node.
  Once `AGENTS_HUB_BLOB_URL` is set, the PVC stops being load-bearing for
  correctness (see the next section) and can shrink or be turned off.

## The shared-files caveat

Both shapes above run into the same limit [scaling](scaling.md) already
names: `AGENTS_HUB_DATABASE_URL` takes the database off the one shared
bind mount, but `.agents_hub/` (run logs, workspaces, view assets, generated
Dockerfiles) is still files on disk. On the compose profile that mount is
one host, guaranteed by every service sharing the same bind mount; in the
cluster it is a ReadWriteMany PVC instead, which not every storage class
provides.

`AGENTS_HUB_BLOB_URL` (`common/blobs.py`) is the way past that limit in
either shape: point it at an S3 compatible bucket (the bundled MinIO under
compose, your own bucket or a MinIO deployment in a cluster) and those files
move off local disk entirely, so backend and worker processes no longer
need to be on the same host, or the same ReadWriteMany volume, to see the
same state. Until it is set, both shapes still need the shared mount: the
compose profile because SQLite-adjacent files are still there, the Helm
chart because the PVC is still where they land.

Related: [scaling](scaling.md), [workers](workers.md),
[installation](installation.md).

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

## The cluster map

Once processes run on several hosts, one page has to say where everything
is. `GET /api/cluster` (also served as `/api/deployment`; the Cluster page
in the dashboard, `ah deployment` in a terminal) is that page:

- **Members.** Every backend replica and worker registers itself in the
  `members` table on start and refreshes a heartbeat every 15 seconds with
  a small load snapshot (SSE clients served, runs carried, launches
  tracked). A member is live, stale (no beat for a minute, the process went
  away without saying so) or stopped (a clean shutdown). Each row shows the
  role, host, pid, commit, uptime and the singleton roles the member holds
  (scheduler, watchdog, outbox, telegram, publisher).
- **Queue and outbox.** How many launches wait, which worker holds each,
  and how many webhook deliveries are pending.
- **Entity runs by host.** Active agent runs with the age of their heartbeat
  and their checkpoint step; entity runs (flows, loops, teams, scenarios) with
  kind, host, heartbeat age and resume attempts. Resident instances (with
  their carrier status and heartbeat) and containers are listed separately,
  each with the host it lives on.
- **Services.** Every [service](services.md) with its agent (or runner),
  workspace, status and live replicas against its minimum and maximum; the
  `services` lease names the replica of the backend that supervises them.
- **Logs.** Each member writes its own log to `service_logs/<member>.log`
  under the state root (rotating, 5 MB by 3), mirrored to the object store
  when one is configured, and served as
  `GET /api/cluster/members/<id>/logs` with a live tail over the stream on
  `logs:member:<id>`, the same way an instance's carrier log is.

Stale rows older than a day are pruned by the maintenance sweep; a stale
or stopped row can also be dropped from the page. `AGENTS_HUB_INSTANCE_ID`
gives a member a stable name across restarts; without it the name is
`host:pid`.

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

## Releases

A release is a semver version (`X.Y.Z`, or `X.Y.Z-rc.1` for a prerelease)
that names the same thing everywhere: `pyproject.toml`, the dashboard's
`package.json`, the Helm chart's `version` and `appVersion`, the images and
the git tag `vX.Y.Z`. While the major version is 0, a minor release may change
the API or configuration and says so in `CHANGELOG.md` under **Changed**; a
patch release never does. A release that adds schema migrations lists them
under **Upgrade notes**, because that is what decides how it rolls back.

Cutting one is two commands, and nothing leaves the machine until the push:

```bash
python scripts/release.py plan minor     # the new version and its notes, nothing written
python scripts/release.py cut minor      # bump every file, move Unreleased into the
                                         # new section, commit, tag vX.Y.Z
git push origin dev && git push origin v0.2.0
```

`cut` refuses a dirty tree, an existing tag and a version that is not newer.
When nobody wrote anything under **Unreleased** in `CHANGELOG.md`, it fills the
section from the commit subjects since the previous tag, so edit the commit
before pushing if the notes need words.

Pushing the tag starts `.github/workflows/release.yml`: it checks that the tag
matches the files and has a CHANGELOG section, runs the whole CI suite on the
tagged commit, builds the backend, frontend, agents, models and browser images
for amd64 and arm64, pushes them to GHCR as
`ghcr.io/<owner>/agents-hub-<image>:X.Y.Z` (and `:X.Y` and `:latest` for a
final release), and creates the GitHub release from the CHANGELOG section. A
tag that fails the check or the tests publishes nothing.

Each platform is built natively, on an amd64 and an arm64 runner, and the two
results are joined into one multi-platform manifest per tag; nothing runs
under QEMU. The backend and agents images come in two flavours from the one
`Dockerfile`: the default leaves out the RAG stack (torch,
sentence-transformers, chromadb and the remote vector stores, a gigabyte of
wheels that only the embedding features import, docs/memory.md), and
`:X.Y.Z-rag` (`:X.Y-rag`, `:latest-rag`) carries it. Pick the `-rag` tag when
`RAG_VECTOR_DB` is anything but `none`; a local build gets the same with
`WITH_RAG=true` in `.env` (docker-compose.yml passes it to the image).

Which release is running: `ah version` (the client, and the service when
`AGENTS_HUB_URL` points at one) or `GET /api/system/version`, which also names
the newest schema migration the build knows. Images carry the version and
commit as `AGENTS_HUB_VERSION` and `AGENTS_HUB_GIT_SHA`, and as OCI labels.

## Upgrading

1. Read the release's **Upgrade notes**.
2. Take a backup with the build you are running now, before switching:
   `ah db backup --to /backups` (docs/backup.md). A newer build migrates the
   database the moment it opens it, backup included. On SQLite the
   first process of the new build also archives the database by itself
   before it migrates (below), but that copy holds the database only, not the
   workspaces and logs beside it.
3. Move to the release. Compose: `git fetch --tags && git checkout vX.Y.Z`,
   then `docker compose up -d --build` (the backend bind mounts the checkout,
   so the checkout is what runs). Helm: `helm upgrade` with the chart of that
   release; its image tags default to the chart's `appVersion`.
4. Migrations run when the first process opens the database. Check with
   `ah version`, `ah doctor` and the Health page.

## Rolling back

An older build cannot open a database a newer one has migrated: its migration
runner refuses a ledger that names versions it does not know, on purpose,
since old code writing to a newer schema is how data goes quietly wrong. So a
rollback over a release with migrations is a restore, and anything written
after the upgrade is lost. A rollback over a release without migrations is
only the checkout or the image tag.

The copy to restore:

- **SQLite.** Before a process applies new migrations, it writes
  `.agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz`: the database
  as the previous release left it, in the archive format `ah db restore`
  reads, with that release's version in its manifest as `app_version`. The
  newest five are kept. `AGENTS_HUB_BACKUP_BEFORE_MIGRATE=0` turns it off. A
  failed copy is logged and the upgrade goes on.
- **Postgres.** The backup from step 2 of the upgrade. Nothing is taken
  automatically: copying a large Postgres database on startup would hold
  every replica back.

Then, with the previous release's build (checked out, or its image):

```bash
docker compose down                      # or scale the Deployments to 0
git checkout v<previous>                 # or helm rollback / the previous image tag
ah db verify .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
mkdir -p .agents_hub/rolled-back && mv .agents_hub/agents_hub.db* .agents_hub/rolled-back/
ah db restore --no-files .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
docker compose up -d --build
ah version                               # the previous release, and it opens the database
```

The migrated database has to be out of the way first: the previous build
refuses to open it even to overwrite it, so `ah db restore --force` over it
fails. On SQLite, move the file aside as above (keep it until the rollback is
confirmed). On Postgres, restore into a new empty database and point
`AGENTS_HUB_DATABASE_URL` at it, or drop and recreate the old one.

Use `--no-files` with a pre-migrate archive (it holds no files); a full
backup from step 2 restores the files too. `ah db restore` writes to the
database the process is configured with, so run it with the same `.env` (or
`AGENTS_HUB_DATABASE_URL`) as the service. This sequence was run end to end
from 0.1.0 to the current build and back.

Related: [scaling](scaling.md), [workers](workers.md),
[installation](installation.md), [backup](backup.md).

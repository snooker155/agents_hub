# Running with Docker Compose

Agents Hub is one backend and one dashboard, all on your machine. This page
covers the compose file, and how to upgrade and roll back. State is SQLite and files
on disk, so there is nothing else to run.

## The shapes

Every way of running it is the same code and the same records.

| Shape | Start it | Made of | For |
| --- | --- | --- | --- |
| **Compose from a checkout** | `docker compose up --build` ([installation](installation.md), Path A) | The backend and dashboard images built from source, the checkout bind mounted, the Docker socket for per-agent containers | Containers for agents and the sandbox |
| **Local install** | `./install.sh`, then `ah up` (Paths B and C) | A virtualenv and the Vite dev server | Working on the code |

## The compose file

`docker-compose.yml` has two services.

- **backend**: the API, with every agent as a subprocess inside it. It
  bind mounts the checkout (state lives in `.agents_hub/` there) and, by
  default, the Docker socket so it can build and run agent containers
  ([containers](containers.md)). Drop that line to forbid containers and set
  `AGENT_EXECUTION_MODE=local`. Set `DOCKER_GID` in `.env` if the socket's
  group differs from the default (on macOS with Docker Desktop it is `0`).
- **frontend**: the built dashboard behind nginx, which also proxies `/api`.
  A source change needs a rebuild of this service.

Both published ports listen on `127.0.0.1` by default (`BACKEND_PORT` 8000,
`WEB_PORT` 8080). There is no login, so set `HUB_BIND` in `.env` only to an
address of a network you alone can reach.

```bash
docker compose up --build                       # backend and dashboard
docker compose restart backend                  # picks up code changes
```

## Version and the RAG stack

The `Dockerfile` leaves out the RAG stack (torch, sentence-transformers,
chromadb and the remote vector stores, a gigabyte of wheels that only the
embedding features import, docs/memory.md). Build with it when `RAG_VECTOR_DB`
is anything but `none`: `WITH_RAG=true` in `.env` (docker-compose.yml passes it
to the image).

Which build is running: `ah version` or `GET /api/system/version`, which also names
the newest schema migration the build knows. Images built from the checkout carry the version and
commit as `AGENTS_HUB_VERSION` and `AGENTS_HUB_GIT_SHA`, and as OCI labels.

## Upgrading

1. Take a backup with the build you are running now, before switching:
   `ah db backup --to /backups` (docs/backup.md). A newer build migrates the
   database the moment it opens it, backup included. The
   first process of the new build also archives the database by itself
   before it migrates (below), but that copy holds the database only, not the
   workspaces and logs beside it.
2. Move to the new version. Compose: `git pull` (or check out the commit or tag you want),
   then `docker compose up -d --build` (the backend bind mounts the checkout,
   so the checkout is what runs).
3. Migrations run when the first process opens the database. Check with
   `ah version`, `ah doctor` and the Health page.

## Rolling back

An older build cannot open a database a newer one has migrated: its migration
runner refuses a ledger that names versions it does not know, on purpose,
since old code writing to a newer schema is how data goes quietly wrong. So a
rollback over a version with migrations is a restore, and anything written
after the upgrade is lost. A rollback over a version without migrations is
only the checkout.

The copy to restore:

Before a process applies new migrations, it writes
  `.agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz`: the database
  as the previous version left it, in the archive format `ah db restore`
  reads, with that version in its manifest as `app_version`. The
  newest five are kept. `AGENTS_HUB_BACKUP_BEFORE_MIGRATE=0` turns it off. A
  failed copy is logged and the upgrade goes on.

Then, with the previous build (checked out):

```bash
docker compose down                      
git checkout <previous-commit-or-tag>
ah db verify .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
mkdir -p .agents_hub/rolled-back && mv .agents_hub/agents_hub.db* .agents_hub/rolled-back/
ah db restore --no-files .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
docker compose up -d --build
ah version                               # the previous version, and it opens the database
```

The migrated database has to be out of the way first: the previous build
refuses to open it even to overwrite it, so `ah db restore --force` over it
fails. Move the file aside as above (keep it until the rollback is
confirmed).

Use `--no-files` with a pre-migrate archive (it holds no files); a full
backup from step 2 restores the files too. `ah db restore` writes to the
database the process is configured with, so run it with the same `.env` as
the service.

Related: [installation](installation.md), [backup](backup.md), [containers](containers.md).

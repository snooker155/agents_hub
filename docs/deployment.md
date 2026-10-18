# Running with Docker Compose

Agents Hub is one backend, one dashboard and, if you want it, one browser
service, all on your machine. This page covers the compose files, the
published images, and how to upgrade and roll back. State is SQLite and files
on disk, so there is nothing else to run.

## The shapes

Every way of running it is the same code and the same records.

| Shape | Start it | Made of | For |
| --- | --- | --- | --- |
| **Published images** | `deploy/quickstart/docker-compose.yml` ([installation](installation.md), Path A) | `ghcr.io/snooker155/agents-hub-backend` and `-frontend`, a named volume, agents as subprocesses | Nothing to clone or build |
| **Compose from a checkout** | `docker compose up --build` (Path B) | The same two images built from source, the checkout bind mounted, the Docker socket for per-agent containers | Containers for agents and the sandbox |
| **Local install** | `./install.sh`, then `ah up` (Paths C and D) | A virtualenv and the Vite dev server | Working on the code |

The release workflow publishes the images for amd64 and arm64 in two flavours
("Releases" below). The quickstart compose file pulls them by tag. Only the
checkout-based shapes build.

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

## Releases

A release is a semver version (`X.Y.Z`, or `X.Y.Z-rc.1` for a prerelease)
that names the same thing everywhere: `pyproject.toml`, the dashboard's
`package.json`, the images and
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
tagged commit, builds the backend, frontend, agents and browser images
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

Which release is running: `ah version` or `GET /api/system/version`, which also names
the newest schema migration the build knows. Images carry the version and
commit as `AGENTS_HUB_VERSION` and `AGENTS_HUB_GIT_SHA`, and as OCI labels.

## Upgrading

1. Read the release's **Upgrade notes**.
2. Take a backup with the build you are running now, before switching:
   `ah db backup --to /backups` (docs/backup.md). A newer build migrates the
   database the moment it opens it, backup included. The
   first process of the new build also archives the database by itself
   before it migrates (below), but that copy holds the database only, not the
   workspaces and logs beside it.
3. Move to the release. Compose: `git fetch --tags && git checkout vX.Y.Z`,
   then `docker compose up -d --build` (the backend bind mounts the checkout,
   so the checkout is what runs).
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

Before a process applies new migrations, it writes
  `.agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz`: the database
  as the previous release left it, in the archive format `ah db restore`
  reads, with that release's version in its manifest as `app_version`. The
  newest five are kept. `AGENTS_HUB_BACKUP_BEFORE_MIGRATE=0` turns it off. A
  failed copy is logged and the upgrade goes on.

Then, with the previous release's build (checked out, or its image):

```bash
docker compose down                      
git checkout v<previous>
ah db verify .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
mkdir -p .agents_hub/rolled-back && mv .agents_hub/agents_hub.db* .agents_hub/rolled-back/
ah db restore --no-files .agents_hub/backups/pre-migrate_<time>_to_<version>.tar.gz
docker compose up -d --build
ah version                               # the previous release, and it opens the database
```

The migrated database has to be out of the way first: the previous build
refuses to open it even to overwrite it, so `ah db restore --force` over it
fails. Move the file aside as above (keep it until the rollback is
confirmed).

Use `--no-files` with a pre-migrate archive (it holds no files); a full
backup from step 2 restores the files too. `ah db restore` writes to the
database the process is configured with, so run it with the same `.env` as
the service. This sequence was run end to end
from 0.1.0 to the current build and back.

Related: [installation](installation.md), [backup](backup.md), [containers](containers.md).

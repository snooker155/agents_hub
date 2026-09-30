# Backup and recovery

One archive, one command in, one command out: `ah db backup` writes it, `ah
db restore` loads it back, `ah db verify` checks it without touching the live
database. Whatever backend is configured (SQLite or Postgres,
[scaling.md](scaling.md#postgres)), the archive is always a plain `.tar.gz`
with a self-contained SQLite file inside, so restoring needs nothing beyond
this checkout: no `pg_dump`, no server-specific tool.

## What the archive holds

- **`database.sqlite`** — every table `ah db migrate` would copy: runs,
  tasks, sessions, nodes, flows, identity, and the rest. When the live
  database is already SQLite this is a `VACUUM INTO` copy (fast, no row-by-row
  Python); when it is Postgres, it is copied across with the same
  `common/db_transfer.py` machinery `ah db migrate` uses.
- **`manifest.json`** — when the backup was made, the app version, the
  source's dialect and location, its schema version, and a row count per
  table. `ah db verify` and `ah db restore` compare against it; a hand-edited
  manifest is exactly what "tampered" means to `verify`.
- **The state directories beside the database**, unless `--no-files`: run,
  node and flow logs, `workspaces/` (including each workspace's own `.git`,
  since a checkout's history is part of the workspace), view assets,
  generated Dockerfiles, imported-agent checkouts, and the shared-memory
  pools (episodes, graphs, extractions). `__pycache__/`, `node_modules/` and
  `.venv/` are pruned wherever they turn up inside those, since they are
  disposable and an install rebuilds them anyway.

What is **not** in the archive:

- Running processes. A restore does not resume in-flight runs; anything
  `running` at backup time comes back in the database as a `running` row
  with nobody actually running it. Stop or let long-running work finish
  first where you can, or reconcile it after restoring.
- Redis (`AGENTS_HUB_BROKER_URL`, [scaling.md](scaling.md#the-broker-bridge)).
  It holds in-flight cross-replica pub/sub only, nothing durable.
- The `.env` file and any other secrets. The archive is state, not
  configuration, and something you may want to hand to someone else or store
  less carefully than your API keys.
- A configured object store's own copies, if one is in front of workspace or
  attachment files instead of the local disk. Back that up with its own
  tooling.

## The three commands

```bash
# Write one archive. --to is a directory (a timestamped name goes inside it)
# or the archive's own path. Add --no-files for the database alone.
ah db backup --to /backups

# Check an archive without touching the live database.
ah db verify /backups/agents_hub_backup_20260101T030000Z.tar.gz

# Load an archive into the database this process is configured with, and
# extract its files into AGENTS_HUB_ROOT (existing files overwritten,
# nothing else deleted). Refuses a database that already holds rows unless
# --force. --no-files restores the database alone.
ah db restore /backups/agents_hub_backup_20260101T030000Z.tar.gz --force
```

All three are direct-mode only ([cli.md](cli.md)): they open the database
this process is configured with, so run them where the state lives, not
against a backend over `AGENTS_HUB_URL`.

## Losing the host

1. Provision the new host and install the same way as before
   ([installation.md](installation.md)): the checkout, `./install.sh` or
   `pip install -e ".[backend,agents]"`, and the Postgres driver too if
   `AGENTS_HUB_DATABASE_URL` will point at Postgres.
2. Copy the archive over (it is a plain file — scp, an object store, whatever
   moved it off the old host in the first place).
3. Set `AGENTS_HUB_DATABASE_URL` and `AGENTS_HUB_ROOT` in `.env` to where this
   host's state should live, the same as the old host unless you are moving
   backends too.
4. `ah db restore /path/to/the.tar.gz --force` if `AGENTS_HUB_ROOT` is not
   empty (a fresh host usually is, so `--force` is not needed).
5. Start the backend (and any workers, [scaling.md](scaling.md#workers-on-other-hosts)).
   `ah db status` first is a quick sanity check: dialect, schema version and
   row counts should match what `ah db verify` reported for the archive.

## How often

Daily, off-peak, is enough for most deployments; hourly if a day of lost task
history would actually hurt. Keep a handful of rotations and copy at least
one off the host it was taken on — a backup that lives next to the data it
protects survives everything except the one failure it exists for. A plain
cron entry does it:

```cron
# 03:00 daily, keep 14 days, prune older ones.
0 3 * * * cd /opt/agents_hub && ah db backup --to /backups >> /var/log/agents_hub_backup.log 2>&1 && \
  find /backups -name 'agents_hub_backup_*.tar.gz' -mtime +14 -delete
```

Verify a backup occasionally, not just take it: `ah db verify` is cheap
(it never touches the live database) and catches a corrupted archive long
before the day you need it.

Related: [scaling.md](scaling.md), [workers.md](workers.md)

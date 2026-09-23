# Running more than one backend

The default deployment is a single backend replica, and nothing here changes
that or is required for it. This page is for `docker compose --scale
backend=N`: what stays safe on its own, what needs the cross-replica broker
bridge, how to put the database in Postgres, and where the boundary is.

## Per replica vs. shared

Each backend replica is its own process, so anything it keeps in memory is
per replica:

- The session broker (`common/session_broker.py`): SSE clients, their
  channel subscriptions, and their per-client replay buffers. A browser tab
  is served by whichever replica nginx sent its connection to, and an event
  published in-process only reaches that replica's own subscribers.
- The live-state publisher (`common/live_state.py`): polls Docker/node
  status once per replica and republishes it, but only for channels that
  replica actually has subscribers for, so the duplication across replicas
  costs nothing a browser notices.
- The plan scheduler's job tick and the run watchdog: each replica ticks on
  its own, but scheduled jobs are claimed under a database lease
  (`plans/storage.py`'s `claim_due_jobs`), so two replicas racing the same
  due job is already safe without any change here.
- Alert-rule evaluation (`notify/rules.py`) and outbound webhook/Slack
  delivery (`notify/outbound.py`): both are called directly, in-process, at
  the point a run reaches a terminal status or a notification is created
  (`managers/runs/notifications.py`, `plans/service.create_notification`).
  Neither subscribes to the session broker, so neither is affected by
  replica count or by the bridge below: a run finishes on exactly one
  replica, and that replica alone evaluates its rules and dispatches its
  webhooks, exactly like a single-replica deployment.

Everything else is shared, and must stay shared:

- **The database and the rest of `.agents_hub/`** (workspaces, logs, agent
  definitions state) are on a bind mount every replica reads and writes
  through. This works because `docker-compose.yml` mounts the same host
  directory into every replica: it requires all replicas to run **on one
  host**. SQLite's WAL mode is what makes several processes on that one host
  safe together; it is not a network protocol, so replicas on different
  hosts sharing "the same" directory over NFS or similar is not a supported
  configuration. Postgres (below) takes the database off that mount; the
  files still need it.
- The Docker socket (`/var/run/docker.sock`), for replicas that launch agent
  containers or drive `docker ps` for the Containers page. Also host-local
  by nature.

With `AGENTS_HUB_DATABASE_URL` set (the Postgres section below) the database
is no longer on that mount, and only the files above keep the replicas on one
host.

## The broker bridge

`common/broker_bridge.py` closes the session-broker gap. It is configured by
one setting, `AGENTS_HUB_BROKER_URL` (`common.config.Settings.broker_url`),
empty by default, which keeps it off entirely, no import of `redis`, no
connection attempt, no behaviour change from a single-replica deployment.

Set it to a Redis URL and, once the backend starts:

- Every genuinely local event the broker publishes (`apublish` /
  `publish_threadsafe`) is appended to one shared Redis **Stream**
  (`agents_hub:events`, `XADD ... MAXLEN ~ N`), tagged with this replica's id
  (`AGENTS_HUB_INSTANCE_ID`, or `hostname:pid` if that is unset) as its
  `origin`. `N` is `AGENTS_HUB_BROKER_STREAM_MAXLEN` (default 10000) — a
  count, not a duration: the stream keeps approximately the last N entries
  regardless of how long that took to accumulate, because what actually
  bounds a useful replay is "how much just happened", which a count tracks
  directly and a clock does not. Redis hands back the id it assigned the
  entry, and that id becomes the event's `id` for every subscriber on this
  replica too (including the ones on the very replica that published it) —
  not the old per-client counter — so a browser's `Last-Event-ID` means the
  same thing on every replica.
- A background task reads that stream with `XREAD BLOCK`, picking up from
  wherever it last left off. An entry whose `origin` is a *different*
  replica is re-published into the local broker, so this replica's own SSE
  clients receive it too, carrying the same id it arrived with. An entry
  whose `origin` is this replica's own id is skipped: this replica already
  delivered it directly, at publish time, above. An event delivered this way
  is never written back onto the stream, so three or more replicas never
  echo one event forever.
- A browser that reconnects to a *different* replica than the one that
  issued its client id (or to the same one after a restart) is no longer
  limited to a refetch: if its `Last-Event-ID` is still a live Redis stream
  id, `routes/stream.py` asks `BrokerBridge.replay` to read the stream with
  `XRANGE` from just after it, filtered to the channels that browser tab
  actually wants (its usual defaults plus a `channels` query parameter the
  frontend sends on every reconnect), and answers `_meta ready` with
  `resumed: true, source: "stream"` instead of starting the tab over. This
  only reaches as far back as `MAXLEN` still holds — see Limits below.
- `common/live_runs.py`'s live-turn snapshots (what a run in progress is
  saying right now, used by a page that opens mid-run) are mirrored into
  Redis the same way, keyed by run id with a short TTL, so a run watched
  from a different replica than the one running it still shows live
  progress rather than nothing until the run finishes. Bridge off, or no
  mirror yet for that run: unaffected, exactly as before.

If `AGENTS_HUB_BROKER_URL` is set but the `redis` package is not installed,
or the initial connection fails, the backend logs one error and keeps
running with the bridge off: a Redis outage is never a backend outage. A
dropped stream read reconnects with backoff and resumes from where it left
off rather than skipping straight to "now".

## Running it

```bash
# .env
AGENTS_HUB_BROKER_URL=redis://redis:6379/0

# start Redis (profile "scale") and three backend replicas
docker compose --profile scale up --build --scale backend=3
```

Without `--profile scale`, the `redis` service is not started at all, so a
plain `docker compose up` is unaffected by any of this. `redis` publishes no
port: only backend replicas on the compose network reach it.

The `redis>=5,<6` package itself is commented out in
`dashboard/backend/requirements.txt` (a plain requirements file has no
"optional extra" mechanism); install it explicitly, or in a custom image
layer, on a deployment that actually sets `AGENTS_HUB_BROKER_URL`.

## Postgres

SQLite is one file on one host, so replicas on different hosts cannot share
it. `common/db.py` has a second backend for that: set
`AGENTS_HUB_DATABASE_URL` to a `postgresql://` URL and the same schema, the
same `get_conn()` / `transaction()` API and the same migrations run against
Postgres. Left empty, nothing changes; SQLite stays the default and the
laptop needs no server.

What is different under Postgres:

- **Connections.** One pool per process (`psycopg_pool`, size
  `AGENTS_HUB_DB_POOL_SIZE`, default 10); a run subprocess or a CLI call
  can set it to 1 or 2. Ten seconds is the wait on a full pool or a lock,
  like SQLite's busy timeout.
- **Write transactions are still one at a time.** SQLite's `BEGIN
  IMMEDIATE` gives every `with transaction()` block exclusive write access,
  and a lot of code reads then writes inside one block counting on that.
  On Postgres every write transaction takes one transaction-scoped advisory
  lock (`pg_advisory_xact_lock`), which reproduces the discipline across
  every process and host. Reads outside a transaction are ordinary MVCC
  reads and never wait. Throughput of writers is therefore the same as with
  SQLite; the gain is location, not parallel writers. Row-level locking on
  hot paths is a later step.
- **Run containers use the HTTP state transport by default.** With
  `AGENT_RUN_STATE_TRANSPORT` unset, a run container is not handed the
  database URL and password just to update its own record; it posts to the
  backend instead (docs/containers.md). Setting the variable explicitly wins.
- **Schema.** The numbered migrations in `common/migrations/` are written
  once, in SQLite syntax, and the runner rewrites the handful of type names
  that differ (`INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL`, `INTEGER`
  → `BIGINT`, `REAL` → `DOUBLE PRECISION`). JSON documents stay `TEXT`
  columns on both: the code reads them as strings, and the two JSON lookups
  the queries need (`common.db.json_text`, `json_truthy`) cast on the fly.

Moving an existing installation:

```bash
# 1. start a server: the compose one, or your own
docker compose --profile postgres up -d postgres

# 2. copy the database across (both directions work; --force empties the target)
ah db migrate --to postgresql://agents_hub:agents_hub@localhost:5432/agents_hub

# 3. point every process at it and restart
# .env
AGENTS_HUB_DATABASE_URL=postgresql://agents_hub:agents_hub@postgres:5432/agents_hub
docker compose --profile postgres --profile scale up --build --scale backend=3
```

`ah db migrate` creates the target schema, copies every table in one
transaction, compares the row counts, and carries the legacy-JSON markers
across so the target never re-imports `agent_runs.json` and friends. `ah db
status` shows which backend a checkout is using, its schema version and row
counts. The way back is the same command with a SQLite path as `--to`.

The compose `postgres` service publishes no port (only the compose network
reaches it) and keeps its data in the `postgres_data` volume; to run `ah db
migrate` from the host against it, publish 5432 in an override file or run
the command inside the backend container (`docker compose exec backend ah db
migrate --to ...`).

The driver (`requirements-postgres.txt`: `psycopg[binary]`, `psycopg_pool`)
is in the backend image; on a host install add it with `pip install -e
".[postgres]"`. The test suite runs against both backends in CI
(`python-postgres` job); locally, `AGENTS_HUB_TEST_DATABASE_URL=postgresql://...
python -m pytest tests/` does the same against a database the suite may
empty.

## Workers on other hosts

Postgres takes the database off the host; the launch queue takes the agent
processes off it. With `AGENTS_HUB_ROLE=api` a backend prepares a run and
puts the launch on `run_queue`; `ah worker` on any host claims it and spawns
it there. Runs report a heartbeat instead of a pid, write a checkpoint after
every tool call and are resumed from it when their process dies, and the
singleton loops (scheduler, watchdog, Telegram poller, outbox drainer) run
under database leases so N replicas never run them twice. All of it is on
its own page: [workers](workers.md).

## Limits

- **State beside the database is still one host.** Run logs, workspaces,
  view assets, memory pools and agent definitions live under `.agents_hub/`
  on a bind mount every replica shares, and with SQLite the database itself
  is there too. Postgres moves the database off that mount; the files are
  the next boundary (an object store behind `common/blobs.py` is the planned
  step), so replicas on different hosts today would each see their own
  copies of them.
- **Replay after a reconnect that lands on a different replica reaches back
  as far as the shared stream, not the per-process ring buffer.** A
  browser's `EventSource` reconnect sends back its `client_id` and
  `Last-Event-ID`; `SessionBroker.resume_client` can only find that client
  id if it lands back on the *same* replica that issued it (the client id
  and its in-memory ring buffer are per-process state, and stay that way —
  sharing them would mean shipping every client's buffered event history
  through Redis too, for a reconnect race the stream catch-up below already
  covers more generally). When the reconnect lands on a different replica
  (or the same one after a restart), `resume_client` reports the client id
  as unknown, and `routes/stream.py`'s `stream()` falls back to
  `BrokerBridge.replay`: if the bridge is on and `Last-Event-ID` is still a
  live Redis stream id, it reads the shared stream with `XRANGE` instead,
  filtered to the channels this client asked for, and answers
  `{"channel": "_meta", "type": "ready", "resumed": true, "source": "stream", ...}`
  — a real resume, just from Redis instead of memory. Only when *that* also
  cannot help — no bridge running, or `Last-Event-ID` predates what
  `AGENTS_HUB_BROKER_STREAM_MAXLEN` still holds — does it open a fresh
  client and answer `resumed: false`, and the frontend (`StreamContext.jsx`)
  treats that as a signal to refetch rather than trust a stream that may
  have skipped a beat. This is a probabilistic guarantee, not an absolute
  one: the stream is trimmed by count, so a replica that is down, or a
  browser tab that is backgrounded, for longer than it takes the rest of the
  deployment to produce `AGENTS_HUB_BROKER_STREAM_MAXLEN` events will still
  fall through to a refetch — the same outcome the single-replica case
  already had for a restarted backend, just pushed further out.

## Staying on one replica (the default)

Nothing above needs to be touched for the default deployment: one `backend`
replica, `AGENTS_HUB_BROKER_URL` unset, no `--scale`, no `--profile scale`.
The session broker, live-state publisher and job scheduler all behave
exactly as documented elsewhere in this corpus. Turn to this page only when
actually running `--scale backend=N`; without the broker URL set in that
case, live updates (a run finishing, a chat token, a notification) only ever
reach the replica that produced them, and every browser tab pinned to a
different replica silently stops updating until its next full refetch,
so `--scale` without the bridge configured is not a supported configuration.

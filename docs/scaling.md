# Running more than one backend

The default deployment is a single backend replica, and nothing here changes
that or is required for it. This page is for `docker compose --scale
backend=N`: what stays safe on its own, what needs the cross-replica broker
bridge, and where the boundary is.

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

- **SQLite** (`.agents_hub/agents_hub.db`, WAL) and the rest of
  `.agents_hub/` (workspaces, logs, agent definitions state) are on a bind
  mount every replica reads and writes through. This works because
  `docker-compose.yml` mounts the same host directory into every replica —
  it requires all replicas to run **on one host**. SQLite's WAL mode is what
  makes several processes on that one host safe together; it is not a
  network protocol, so replicas on different hosts sharing "the same"
  directory over NFS or similar is not a supported configuration.
- The Docker socket (`/var/run/docker.sock`), for replicas that launch agent
  containers or drive `docker ps` for the Containers page. Also host-local
  by nature.

A full move to Postgres (so replicas could run on different hosts) is out of
scope here; SQLite on one host shared by N replicas is the supported ceiling.

## The broker bridge

`common/broker_bridge.py` closes the session-broker gap. It is configured by
one setting, `AGENTS_HUB_BROKER_URL` (`common.config.Settings.broker_url`),
empty by default, which keeps it off entirely, no import of `redis`, no
connection attempt, no behaviour change from a single-replica deployment.

Set it to a Redis URL and, once the backend starts:

- Every event the local broker publishes (`apublish` / `publish_threadsafe`)
  is also published onto one shared Redis channel, tagged with this
  replica's id (`AGENTS_HUB_INSTANCE_ID`, or `hostname:pid` if that is
  unset) as its `origin`.
- A background task subscribes to that channel. A message whose `origin` is
  a *different* replica is re-published into the local broker, so this
  replica's own SSE clients receive it too. A message whose `origin` is this
  replica's own id is dropped (Redis pub/sub echoes a publisher's own
  message back to its own subscription). An event delivered this way is
  never sent back out, so three or more replicas never echo one event
  forever.

If `AGENTS_HUB_BROKER_URL` is set but the `redis` package is not installed,
or the initial connection fails, the backend logs one error and keeps
running with the bridge off: a Redis outage is never a backend outage. A
subscription that later drops reconnects with backoff.

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

## Limits

- **One host.** Every replica must share the same bind mount for the repo
  and `.agents_hub/`. This is what `docker-compose.yml` already gives you;
  it only breaks if the volumes are changed to per-replica ones.
- **No Postgres.** SQLite plus WAL is the whole story; there is no plan here
  to move to a network database. That is the ceiling on how far this scales.
- **Replay after a reconnect that lands on a different replica is a lagged
  refetch, not a seamless resume.** A browser's `EventSource` reconnect
  sends back its `client_id` and `Last-Event-ID`; `SessionBroker.resume_client`
  can only find that client id if it lands back on the *same* replica that
  issued it (the client id and its replay ring buffer are per-process state,
  and the bridge deliberately does not try to share them, since doing so would
  mean shipping every client's buffered event history through Redis too,
  for a reconnect race that is already handled). When the reconnect lands
  on a different replica, `resume_client` reports the client id as unknown,
  `routes/stream.py`'s `stream()` opens a fresh client and answers with
  `{"channel": "_meta", "type": "ready", "resumed": false, ...}` instead of
  replaying anything, and the frontend (`StreamContext.jsx`) treats
  `resumed: false` as a signal to refetch rather than trust a stream that
  may have skipped a beat. This behaviour already exists for the
  single-replica case (a restarted backend also does not recognize an old
  client id) and needed no change for multiple replicas; it just now also
  covers "reconnected to a different replica."

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

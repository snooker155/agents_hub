# Files and the object store

The database now runs on Postgres across hosts ([scaling](scaling.md)), and a
run's process can already spawn on any worker host ([workers](workers.md)).
Both of those took the *records* off one host; the *files* a run, a node, a
flow or a view writes alongside its record did not move with them. This page
is about those files: what still lives under `AGENTS_HUB_ROOT`, how
`common/blobs.py` mirrors it into an object store so a replica that did not
write a file can still read it, and what is deliberately left needing one
host for now.

## What lives where

| Files | Path | Written by |
|---|---|---|
| Agent run logs | `run_logs/agent_run_<run_id>.log` | the run's own process (`runtime/agent_run.py`), or the launcher for a Docker run |
| Node logs | `node_logs/node_<node_id>.log` | the node process (`managers/node_manager.py`) |
| Flow run logs | `flow_logs/<flow_id>/<run_group>.json` | the flow orchestrator (`flow/run_store.py`) |
| View assets | `<workspace>/.views/<view_id>/` (workspace-scoped) or `views/<view_id>/` (global) | `views/store.py`: `view.json`, `base.json`, copied assets, snapshots, clips, checkpoints |
| Generated Dockerfiles | `dockerfiles/` | `managers/container_manager.py`, read back on the same host that builds the image |

Every path above is relative to `AGENTS_HUB_ROOT`, the same root the database
used to share on one host before Postgres. A single-replica deployment (the
default) needs none of this: everything is already local.

## The setting

`AGENTS_HUB_BLOB_URL` (`common.config.Settings.blob_url`), empty by default.
Set it to `s3://bucket/prefix` (real AWS S3) or point it at MinIO with
`AGENTS_HUB_BLOB_ENDPOINT`:

```bash
# .env
AGENTS_HUB_BLOB_URL=s3://agents-hub/state
AGENTS_HUB_BLOB_ENDPOINT=http://minio:9000
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

Region and credentials are the usual AWS environment variables, not a
setting of their own: `common/blobs.py` hands them to `boto3` unchanged.
Leaving `AGENTS_HUB_BLOB_URL` empty keeps `common.blobs.store()` on
`LocalBlobStore`, a no-op: nothing is uploaded, nothing is downloaded, every
read is served straight off disk, exactly as before this module existed.

The `boto3` dependency is not installed by default (`requirements-blobs.txt`,
the `blobs` extra: `pip install -e ".[blobs]"`), since a deployment that
never sets the URL never imports it — `S3BlobStore` imports it lazily, the
same way `common/broker_bridge.py` imports `redis` lazily for the broker
bridge.

## What is mirrored, and when

Every write goes to disk first, exactly as before; the mirror to the object
store is an extra step after, and it never blocks or fails the write it
follows:

- **Run logs.** `runtime/agent_run.py`'s `main()` mirrors its own log file in
  the same `finally` block that stops the heartbeat. `runtime/worker.py`'s
  reap loop mirrors a launched child's log once the child is found dead, for
  the case where the worker (not the run) noticed the process end.
- **Flow run logs.** `flow/run_store.close_flow_run` mirrors the run's log
  file once the run reaches a terminal status.
- **View files.** `views/store.py` mirrors after every write that touches a
  view's directory: `create_view` (the base document, the envelope and every
  copied asset), `add_asset`, `set_snapshot`, `save_clip` and
  `save_checkpoint`. `delete_view` removes the mirrored copies too. Live
  Studio edits (`append_ops`, `revert_to`, op-log compaction) are not
  mirrored on every op — only the checkpoints above are — so a view still
  being actively edited on its origin host is not yet fully portable
  mid-edit; it is once one of those save points is hit.
- **Retention.** `common/maintenance.py` deletes the mirrored copy of a run
  log alongside the local file, both when a terminal run ages out
  (`prune_old_runs`) and when an orphaned log file is found
  (`prune_orphan_files`), so a deleted run's log does not linger in the
  object store after it is gone from disk.

Reading falls back to the store wherever the corresponding writer mirrors:
the `/api/logs/{run_id}` and `/api/nodes/{node_id}/logs` routes, view asset
serving (`view_asset_path`, `get_view`) and `flow/run_store.read_flow_logs`
(which lists the store by the `flow_logs/<flow_id>/` prefix to pick up a
per-run file this host never wrote) all try the local file first and only
reach for the store when it is missing. A store outage never turns into a
failed request: every `common/blobs.py` function swallows its own errors and
behaves as "not there" instead of raising.

## What still needs a shared mount

- **Node logs are read with the same local-then-store fallback as run logs,
  but nothing currently mirrors a node's log file as it runs.** A node is a
  long-lived process, not a one-shot run with a clear finish line to mirror
  at, so this is left for a later pass; today a node's log is only fully
  available on the host running it.
- **Generated Dockerfiles** are not mirrored: a Dockerfile is read back only
  by the Docker build on the same host that generated it, in the same
  request, so there is no cross-host read to serve.
- **Workspaces** (agent working directories, knowledge files, `.plans/`) are
  unaffected by any of this and still need a shared mount across hosts
  running the same workspace.
- **Live log tails** — a run's log while it is still writing — are not
  served from the store: mirroring happens at the end of a run (or when a
  worker reaps a dead child), not continuously, so a live "tail -f"-style
  view of a run in progress still needs to reach the host actually running
  it, the same as before this module existed.

Related: [scaling](scaling.md), [workers](workers.md).

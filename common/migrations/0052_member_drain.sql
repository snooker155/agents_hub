-- A request that a worker finish what it runs and exit (the Cluster page's
-- Stop, or the autoscaler taking one away): the worker reads it on its beat,
-- stops claiming, and goes once nothing of its own is left running. See
-- common/members.py request_drain and runtime/worker.py.
ALTER TABLE members ADD COLUMN drain_requested_at TEXT

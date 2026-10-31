-- How busy the machine under each member is and the agent containers on the
-- Docker daemon it uses (common/host_metrics.py), refreshed with every beat:
-- the Cluster page's Machines card and the container columns read it.
ALTER TABLE members ADD COLUMN metrics TEXT

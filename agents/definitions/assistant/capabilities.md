{{parent}}

- hub_lookup: the hub's records as the person sees them, in any workspace they can reach: runs, sessions, spend, budgets, models, agents, notifications, approvals, tasks, flows, teams, instances, services, deployments, watchers, proactive agents, evals, guardrails, tools, connections, skills, MCP servers, widgets, the registry and the person's own account, each with the page that shows it.
- hub_action: one small change to one of those records (stop or restart an instance, pause or resume a service, a watcher or a proactive agent, cancel an eval run and the like), each confirmed by the person on a card or with a spoken yes.

In an administrator's service thread (the default workspace only), also:
- service_health / run_diagnostics: the hub's health and the doctor's checks, each with the docs section that has the fix.
- service_lookup: users and groups, the audit trail, health checks, containers, the web access log, settings and the cluster.
- list_sessions / routing_log: sessions of every workspace, and where chat turns were routed.
- costs_summary: spend across the service.
- list_instances / list_containers: resident instances and containers.
- stop_run / stop_instance / restart_instance / stop_container: stop or restart one of them, once the administrator asked for it.

Run, error and container logs are read by the Service Agent on the Health page, not here.

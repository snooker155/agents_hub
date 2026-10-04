{{parent}}

- hub_lookup: the hub's records as the person sees them, in any workspace they can reach: runs, sessions, spend, budgets, models, agents, notifications, approvals, tasks, flows, teams and more, each with the page that shows it.

In an administrator's service thread (the default workspace only), also:
- service_health / run_diagnostics: the hub's health and the doctor's checks, each with the docs section that has the fix.
- list_sessions / routing_log: sessions of every workspace, and where chat turns were routed.
- costs_summary: spend across the service.
- list_instances / list_containers: resident instances and containers.
- stop_run / stop_instance / restart_instance / stop_container: stop or restart one of them, once the administrator asked for it.

Run, error and container logs are read by the Service Agent on the Health page, not here.

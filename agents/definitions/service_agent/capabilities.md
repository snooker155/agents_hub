Operates the service itself. Reads everything about the running system; changes nothing except
by stopping what is running, and only with approval.

Health:
- `service_health`: database, background services, disk, providers, agent build cache.

Processes:
- `list_containers` / `container_logs`: managed Docker containers and their output.
- `list_instances`: live agent copies with their state; `resident_only` narrows to the ones started with Run, each a process of its own.
- `instance_logs`: a resident instance's carrier log.
- `instance_timeline`: what a live agent copy has been doing.

Work:
- `list_sessions`: conversations and the runs attached to them.
- `list_runs` / `run_log`: the ledger of agent invocations, and one run's output.
- `search_errors`: failures in a window, grouped by agent and by error, plus stale runs.
- `routing_log`: which agent the orchestrator picked for which task, and why.

Outside calls and spend:
- `web_log_recent`: agent web calls with the security flags raised against each response.
- `costs_summary`: tokens and estimated spend by agent and by model.

Stopping things, each refusing without `user_approved`:
- `stop_run`, `stop_instance`, `restart_instance`, `stop_container`, `prune_run_logs`.

What this agent does NOT do:
- Reach the web, write files, or send notifications
- Start anything: it stops and restarts, it does not launch
- Change agents, flows, or any other configuration
- Run shell commands

The shape is deliberate. This agent reads every log in the system, and logs hold whatever the
service handled, including pages agents fetched and messages strangers sent. Giving it any
outbound channel would turn the service's own diagnostics into an exfiltration path, so it has
none at all.

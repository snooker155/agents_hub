# SLO: run start time and error rate

Two service level objectives, judged over a rolling window (default one
hour): how long a run waits between being recorded and actually starting,
and what share of the runs that finish come back failed. Both read every
kind of run alike, agent runs (`runs`) and container runs (`entity_runs`:
flow, loop, team, scenario), the same union `common/run_status.py` gives
every other reader of run state.

## The two objectives

**Run start p95** (`AGENTS_HUB_SLO_START_P95_SECONDS`, default 30). The 95th
percentile of `started_at - created_at` across every run that started in the
window. `created_at` is written the moment the launcher records the
placeholder (`preopen_run` / `entity_runs.upsert`), before a process exists
for it; `started_at` is written once a process actually claims it
(`open_run` / `entity_runs.mark_running`). A launch stuck behind a full queue,
a worker that never picks up its lease, or a slow container pull all show up
here before anything else notices.

**Error rate** (`AGENTS_HUB_SLO_ERROR_RATE`, default 0.05 = 5%). `failed /
finished` across every run that finished in the window (`completed`,
`stopped` and `failed` all count as finished; `awaiting_input` and anything
still active do not). Below `min_sample` (20 finished runs) the objective
reports `no_data` instead of a rate: one failure out of two runs is not a
useful percentage.

Both thresholds are hub-wide toggles
(`common.config.live_setting`, the same `.env`-backed pattern every other
`AGENTS_HUB_...` setting uses), so an operator can tighten or loosen them
without a restart.

## Reading it

- `GET /api/support/slo`: `{status, checked_at, window_seconds, objectives:
  {start_p95, error_rate}}`. Each objective is `ok`, `breach` or `no_data`
  with the number behind it and its sample size; the top level is the worst
  of the two (`no_data` counts as worse than `ok`, `breach` worse than
  either).
- The Health page's **SLO** card shows both, refreshed on load and on demand.
- `slo.json` in every [support bundle](runbook.md) is the same payload, so a
  bundle attached to a support request already answers "was the service
  inside its own SLO at the time."
- `GET /metrics` publishes `agents_hub_run_start_seconds` (the p95, as a
  Prometheus summary quantile) and `agents_hub_slo_breach{objective=}` (1
  while an objective is in breach, 0 while it holds, omitted while there is
  not yet enough data to judge it). `deploy/prometheus/alerts.yml` has two
  alert rules over that gauge; `deploy/helm/agents-hub`'s
  `prometheusRule.enabled` (off by default) ships the same two rules as a
  `PrometheusRule` CRD for a cluster that runs the Prometheus Operator.

## Alerts inside the hub

Two alert rule kinds, alongside `run_failed` and the spend rules
([notifications](notifications.md)): `slo_start_latency` and
`slo_error_rate`. Add one to a workspace's webhook/notification rules (the
same "Rules" panel `run_failed` lives on) and pick its channels; there is no
threshold to set on the rule itself, since the threshold is the hub-wide one
above.

They are evaluated differently from every other rule kind: not per finished
run, but once per plan-scheduler tick (self-throttled to a 60s cadence
inside `notify.rules.evaluate_slo_alerts`, the same "cheap to tick, throttled
internally" shape `evals/batch.py`'s batch poll uses), against the one
hub-wide `common.slo.evaluate()` result, fanned out to every workspace that
has a matching rule. A rule remembers the objective's last known status
(`ok` / `breach`) on itself, so a breach fires once when it starts and once
more on recovery, never on every tick in between.

Related: [runbook](runbook.md), [service-health](service-health.md),
[notifications](notifications.md).

# Observability

How to watch an Agents Hub from outside: Prometheus metrics at `/metrics`,
OpenTelemetry export of runs and metrics, a Grafana dashboard and alert rules.
For the hub's own health page and probes see [service-health](service-health.md),
for the two service level objectives see [slo](slo.md), for what to do when an
alert fires see the [runbook](runbook.md).

## Prometheus: GET /metrics

Prometheus text format, hand-written in `common/metrics.py`, built from
aggregate queries and small in-memory counters only: a scrape every fifteen
seconds never loads the run table. Point a scrape job at `/metrics` of the
`api` (or `all`) role. If the hub is protected by a token, give Prometheus the
same bearer token.

| Metric | Labels | Meaning |
|---|---|---|
| `agents_hub_runs_finished_total` | `agent`, `status` | Finished runs, by final status (`completed`, `stopped`, `failed`, `error`) |
| `agents_hub_model_tokens_total` | `provider`, `model`, `direction` | Tokens: `input` (without cached), `cached`, `output` |
| `agents_hub_model_cost_usd_total` | `provider`, `model` | Estimated spend from the price catalog (left out when the catalog cannot be read) |
| `agents_hub_run_duration_seconds` | histogram | How long finished runs took; buckets 1 s to 1 h |
| `agents_hub_tool_calls_total` | `tool`, `status` | Tool calls (`ok` or `error`) of runs finished since this process started |
| `agents_hub_runs_total`, `agents_hub_runs_running` | `status` | Run records by status, and running now |
| `agents_hub_run_queue`, `agents_hub_outbox`, `agents_hub_lease_*` | | Launch queue, outbox, singleton leases |
| `agents_hub_tokens_total`, `agents_hub_cost_usd_total` | `workspace` | Tokens and spend per workspace |
| `agents_hub_run_start_seconds`, `agents_hub_slo_breach` | | The SLO numbers, see [slo](slo.md) |
| `agents_hub_database_up`, `agents_hub_info` | | Database answers; role and instance of this process |

Label cardinality is bounded. The 20 busiest agents (and models, and tools)
keep their name and everything else adds up under `other`, so a hub with
hundreds of agents still has a small, steady number of series.

Tool call counts are the one metric that is not read from the database on each
scrape: the tool calls live in each run's payload, so a scrape walks forward
through the runs finished since the last scrape (at most 200 per scrape) and
adds their calls to counters kept in the process. They start from zero when the
process starts, as Prometheus counters may. Use `rate()` or `increase()`.

## OpenTelemetry export

Off by default. Set one of these and the hub exports; nothing here runs
otherwise, and nothing is ever done on a run's own thread: spans and metrics
are built and posted from background threads, a collector that is down costs
telemetry and never a run.

| Variable | Meaning |
|---|---|
| `AGENTS_HUB_OTEL_EXPORT_URL` | OTLP/HTTP traces URL, for example `http://collector:4318/v1/traces` |
| `AGENTS_HUB_OTEL_EXPORT_HEADERS` | Extra headers as `k=v,k=v` |
| `AGENTS_HUB_OTEL_METRICS_URL` | OTLP/HTTP metrics URL. When empty, the traces URL with `/v1/traces` swapped for `/v1/metrics` |
| `AGENTS_HUB_OTEL_METRICS_INTERVAL` | Seconds between metric exports, default 60, at least 10 |
| `AGENTS_HUB_OTEL_MAX_CHILD_SPANS` | Most model and tool call spans under one run, default 40, 0 for none |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | The standard variable, used when the ones above are empty: traces go to `<endpoint>/v1/traces`, metrics to `<endpoint>/v1/metrics` |
| `OTEL_EXPORTER_OTLP_HEADERS` | The standard headers, used when `AGENTS_HUB_OTEL_EXPORT_HEADERS` is empty |

### Spans

Every run that reaches a terminal status becomes one span named
`run <agent>` (`gen_ai.operation.name` `invoke_agent`), with the run, task,
workspace, agent, status, provider, model, token counts, duration and cost as
attributes. Under it, following the OpenTelemetry GenAI conventions:

- one `chat <model>` span per model call, with `gen_ai.operation.name` `chat`,
  `gen_ai.request.model` and the tokens of that call
  (`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`);
- one `execute_tool <tool>` span per tool call, with `gen_ai.operation.name`
  `execute_tool`, `gen_ai.tool.name`, and an error status when the call failed.

Prompts, answers and tool inputs and outputs are not exported. The run records
the calls in order with their tokens and durations but not their clock times,
so the child spans are laid end to end inside the run's window; their order and
lengths are right, their exact start times are approximate. A run with more
calls than the limit exports the first ones and says how many it left out in
`agents_hub.child_spans_dropped`. Runs finished before this feature, or run
without call records, export as the one span.

### Metrics

Every interval the hub posts the same numbers `/metrics` serves as OTLP
metrics (gauges, cumulative monotonic sums for the counters, one histogram for
run duration), with `service.name` `agents-hub` and the instance id as
resource attributes. With several `api` replicas, each exports the numbers it
sees, which for database-backed metrics are the same: aggregate by dropping
`service.instance.id`.

## Grafana dashboard and alerts

- `deploy/grafana/agents-hub-dashboard.json`: import it in Grafana and pick
  your Prometheus data source. It shows health, runs by status and agent,
  failure share, duration p50 and p95, the start-time SLO, queue and outbox,
  tokens and spend by model, and tool calls and errors by tool.
- `deploy/prometheus/alerts.yml`: the SLO rules plus scrape down, an agent
  failing more than half its runs, slow runs, a failing tool, a spend spike and
  dead outbox rows. Load it with `rule_files`.

## Gotchas

- Counters from the database (`agents_hub_runs_finished_total` and the model
  metrics) drop when old runs are pruned; Prometheus treats that as a reset.
- Spend is an estimate from the price catalog, like the Costs page. Models
  without a price show zero.
- Tool call counts begin at process start; right after a restart they are zero.

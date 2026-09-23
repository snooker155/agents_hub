# Notifications

The dashboard inbox (the bell icon) is always the source of truth for a
notification: everything else is a best-effort side channel that reaches the
same event somewhere else. This page covers the two outbound side channels
that leave the hub (a webhook, a Slack incoming webhook), the alert rules that
raise a notification on their own, and the inbound signing scheme that lets an
external system talk back: the node runner, a flow's webhook trigger, and a
webhook that files a task.

## Outbound: webhooks and Slack

**Connect → Connectors → Webhooks** in the sidebar. An endpoint is configured
per [workspace](workspaces.md):

```json
{
  "id": "e1",
  "kind": "webhook",
  "url": "https://example.com/hook",
  "secret": "a-shared-secret",
  "events": ["notification"],
  "enabled": true
}
```

`kind` is `webhook` or `slack`. A webhook's `secret` is optional but signs
every delivery; a Slack endpoint has none, since Slack's own incoming-webhook
URL is the secret. `events` is a list of event names the endpoint subscribes
to; an endpoint created before this list existed, or created with
`events: ["notification"]` explicitly, only ever sees `notification`.

Two events exist:

- **`notification`**: everything the inbox raises, a fired [alert
  rule](#alert-rules) or an explicit `create_notification` call.
- **`audit`**: every row of the [audit trail](audit.md), append-only, one
  event per recorded action. A row with no workspace of its own (a login, an
  account change: identity is not workspace-scoped) is offered to the
  `default` workspace's endpoints, which is where an installation's global
  integrations live. This is how a SIEM collects the trail without polling
  `GET /api/audit` itself.

The Connectors page offers both as a checkbox pair when adding an endpoint.

A notification's channels decide which side channels fire: `create_notification`
(the same call the inbox uses) accepts `channels` including `"telegram"`,
`"slack"` and `"webhook"`, and fans out to every enabled endpoint of that kind.
An [alert rule](#alert-rules)'s own `channels` field is what a fired rule
passes through.

### The event shape

Every delivery, webhook or Slack, carries the same envelope, `type` naming
which of the two events it is:

```json
{
  "id": "5b3e...",
  "type": "notification",
  "workspace": "default",
  "created_at": "2026-09-22T10:00:00+00:00",
  "data": {
    "title": "Run failed: swe_agent",
    "body": "The run failed.",
    "severity": "error",
    "source": {"rule_id": "...", "rule_kind": "run_failed"}
  }
}
```

An `audit` delivery carries the same envelope with `type: "audit"`, `id` set
to `"audit-<row id>"`, and `data` set to the row exactly as
[`GET /api/audit`](audit.md) returns it:

```json
{
  "id": "audit-482",
  "type": "audit",
  "workspace": "acme",
  "created_at": "2026-09-22T10:00:00+00:00",
  "data": {
    "id": 482, "at": "2026-09-22T10:00:00+00:00",
    "actor_id": "u1", "actor_kind": "user", "actor_name": "alice",
    "action": "workspace.policy", "object_type": "workspace", "object_id": "acme",
    "workspace": "acme", "ip": "10.0.0.4", "method": null, "path": null,
    "result": "ok", "details": {"keys": ["require_tool_approval"]}
  }
}
```

A webhook endpoint receives this exact JSON object as its request body. A
Slack endpoint receives a Slack incoming-webhook payload built from it instead:
`{"text": ..., "blocks": [...]}`.

### Headers

A webhook delivery carries:

| Header | Meaning |
| --- | --- |
| `X-AgentsHub-Event` | The event's `type`, e.g. `notification` |
| `X-AgentsHub-Delivery` | A UUID unique to this delivery attempt |
| `X-AgentsHub-Timestamp` | Unix seconds when the request was signed |
| `X-AgentsHub-Signature` | `sha256=<hex hmac>`, present only when a secret is configured |

### The signature scheme

The signature is an HMAC-SHA256 of the raw request body (not the parsed JSON,
the exact bytes on the wire) with the endpoint's secret, hex-encoded and
prefixed `sha256=`. Verify it like this:

```python
import hmac, hashlib

expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, header_signature):
    raise ValueError("bad signature")
```

Also check `X-AgentsHub-Timestamp` is within a few minutes of now, and treat
`X-AgentsHub-Delivery` as an idempotency key: retries and rule re-fires reuse
neither, so seeing the same id twice means a replay.

### Retry policy

A delivery that fails (connection error, or an HTTP 5xx) is retried exactly
once, after a short backoff. A non-5xx response, including 4xx, is treated as
delivered and not retried: a webhook that rejects the payload will keep
rejecting it. Delivery runs on a background thread so raising a notification
or firing a rule never waits on somebody else's server; a delivery still in
flight when the process stops is simply lost, the same way anything else
in-memory is.

## Alert rules

**Connect → Connectors → Webhooks**, the rules list below the endpoints. A
rule raises a notification on its own, without an agent or a person asking for
one, evaluated whenever a run reaches a terminal status:

```json
{
  "id": "r1",
  "kind": "run_failed",
  "threshold_usd": 0,
  "agent_id": null,
  "channels": ["dashboard", "slack"],
  "enabled": true
}
```

Three kinds:

- **`run_failed`** fires when a run finishes `failed` or `error`. `agent_id`,
  if set, restricts it to that agent.
- **`spend_run_over`** fires when a single run's cost (the same pricing the
  [Costs page](costs.md) uses) meets or exceeds `threshold_usd`.
- **`spend_daily_over`** fires when the workspace's total spend for the
  current UTC day meets or exceeds `threshold_usd`. It fires at most once per
  day: the rule remembers the date it last fired and stays quiet until
  tomorrow, however many more runs finish today.

`channels` is the same list `create_notification` accepts: any of
`dashboard`, `telegram`, `slack`, `webhook`. The inbox entry is written
regardless; the rest are best-effort side channels.

## Inbound: signing and idempotency

The same scheme protects three inbound webhooks this hub accepts:

- **POST `/api/external/{token}/run`** — the exposed node runner. When the
  node has an `inbound_secret` configured, the request must be signed; a node
  with none configured keeps accepting an unsigned request, unchanged from
  before this feature.
- **POST `/api/flows/{flow_id}/trigger`** — a flow's webhook trigger. Same
  rule: signed when the flow record carries a `webhook_secret`, open
  otherwise.
- **POST `/api/webhooks/tasks`** — see below. This one has no "open" fallback:
  a workspace with no inbound secret configured cannot be posted to at all.

All three verify the signature the same way outbound deliveries produce it,
and reject a repeated `X-AgentsHub-Delivery` with `409` for 24 hours. A
missing or invalid signature, or a timestamp more than 5 minutes old, is
rejected with `401`.

## Filing a task from outside

`POST /api/webhooks/tasks` lets an external system create a task without going
through the dashboard. Set the workspace's inbound secret once:

```bash
curl -X POST http://localhost:8000/api/notify/inbound-secret?workspace=default \
  -H 'Content-Type: application/json' \
  -d '{"secret": "a-shared-secret"}'
```

Then sign each request the same way as any other webhook:

```bash
BODY='{"title": "File the expense report", "workspace": "default"}'
SECRET='a-shared-secret'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"
TS=$(date +%s)

curl -X POST http://localhost:8000/api/webhooks/tasks \
  -H 'Content-Type: application/json' \
  -H "X-AgentsHub-Signature: $SIG" \
  -H "X-AgentsHub-Timestamp: $TS" \
  -H "X-AgentsHub-Delivery: $(uuidgen)" \
  -d "$BODY"
```

The body accepts `title` (required), `workspace` (required), `description`,
`priority` (`low`/`medium`/`high`/`critical`), `due_at` (ISO 8601) and
`agent_id` (assigns the task immediately, the same as the Tasks page's Assign
action). The response is the created task, in the same shape
[Tasks](tasks.md) returns everywhere else. The task is created with
`created_by: "external"`, exactly like a task filed through
`/api/external/{token}/run`.

## Gotchas

- **A webhook's secret is optional; a task-filing workspace's is not.** An
  outbound endpoint with no secret still delivers, unsigned. The inbound task
  webhook fails closed: no secret configured means no request gets through,
  because there is no token or existing trust to fall back on the way the node
  runner has.
- **Secrets are masked on the way out.** GET `/api/notify/endpoints` returns a
  webhook's secret as its last four characters, the same convention
  [MCP servers](mcp.md) use for headers and env values. Sending that masked
  form back on a PATCH is read as "unchanged".
- **The replay window is 24 hours, not forever.** A delivery id can be reused
  once its window has passed; idempotency here is about a retry or a flaky
  network landing twice, not a permanent id registry.

Related: [connectors](connectors.md), [scheduling](scheduling.md), [costs](costs.md), [tasks](tasks.md), [nodes](nodes.md), [flows](flows.md), [audit](audit.md).

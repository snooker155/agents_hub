# Tools and capabilities

Tools are granted to an agent by name. Nothing is granted by default, including
the calculator.

## The capability model

Every tool is classified by what it grants, not by what it is called:

- **`ingests_untrusted`** — pulls text the operator does not control into the
  agent's context (web fetches, page content, run logs, inbound chat messages).
- **`reads_private`** — can read data the operator would not want published
  (files, memory, tasks, run records).
- **`can_exfiltrate`** — can move agent-chosen bytes outside the system (a
  fetched URL carries its query string; a notification carries its payload).

An agent holding **all three** is a data-exfiltration primitive: text it ingests
can instruct it to collect secrets and send them out, with nothing in the loop to
stop it. That combination is **blocked**, not warned about.

Two of the three produce a warning instead. A web agent inherently ingests and
can exfiltrate; that is what a web agent is.

## Why capabilities and not tool names

`run_shell` alone is the whole trifecta: `curl` ingests and exfiltrates, `cat`
reads. A rule that only fires when three *named* tools co-occur would do nothing
against the single most dangerous tool.

## Consequences you will actually hit

- **A web agent cannot also read your files or memory.** Split the work: one
  agent fetches, another reasons over the result. That is exactly why the Web
  Search Agent and the Researcher Agent are separate.
- **What a web tool actually read is recorded.** Every `web_search` and
  `fetch_url` call, with the text handed back and the flags raised against it,
  lands in the [web request log](web-logs.md).
- **Log readers count as ingesting untrusted content.** A run log holds whatever
  that run handled, including pages it fetched. So the Service Agent, which
  reads every log, is given no outbound channel at all.
- **Adding a tool in the editor can be refused.** The message names the
  capabilities that collided.

There is an override for a combination you have deliberately accepted, and under
a stricter setting it is only honoured for container-isolated, network-free runs.

## Delegation counts as holding the capability

An agent that cannot itself exfiltrate, but can hand a request to an agent
that can, effectively can. `run_agent_tool`, `wait_for_agent_tool`,
`run_flow_tool`, `run_team_tool`, `run_loop_tool` and `run_scenario_tool` are
the edges of that graph: each one hands work (and, for the wait/poll tools,
eventually the result) to another agent whose own tools are not in the
caller's tool list at all.

So the guard evaluates the *effective* set: an agent's own grants, unioned
with the grants of every agent it can reach through one of those tools. An
agent's `delegates` field decides reach — a non-empty allowlist restricts it
to exactly those ids; empty or absent means unrestricted, and it can reach
every agent in the registry. `create_agent_tool` and `modify_agent_tool` are
not delegation edges: whatever they create or change is re-validated by this
same guard at save time, so there is nothing extra to add on top.

A combination that only closes through delegation is reported as a warning,
never a block: an agent whose own list is clean keeps saving and building, and
the editor and the audit show the path. Blocking it would refuse the seed
orchestrator, whose unrestricted `delegates` reaches the web searcher. To
remove the warning, narrow the agent's `delegates` list. A combination the
agent's own tools form still blocks as before. The report names the path, not
just the tools:

> Lethal trifecta: ingests untrusted content (via run_agent_tool -> swe_agent:
> run_shell) + reads private data (get_task) + can send data outside (via
> run_agent_tool -> swe_agent: run_shell).

The tables backing all of this live in `tools/capabilities.py`: `CAPABILITY_GRANTS`
(what a tool grants directly), `REVIEWED_NO_GRANT` (classified, grants nothing —
a write into the hub's own store, a control action, a piece of configuration),
and `DELEGATING_TOOLS` (the six edges above). Every catalog tool id ends up in
exactly one of them; a test enforces that, and `python -m agents.capability_guard`
reports zero unclassified tools alongside the current roster's violations.

## The browser tools

`browser_open`, `browser_read`, `browser_act`, `browser_screenshot` and
`browser_close` drive a real headless Chromium. Use them for what `fetch_url`
cannot read: pages rendered by JavaScript, content behind a click, a search
form. `fetch_url` stays the first choice for a plain page: it is faster and
has nothing to execute.

- `browser_open(url)` opens a page and returns its title.
- `browser_read(max_chars)` returns the page text as it is now, after scripts
  and after any action. Text is extracted exactly as `fetch_url` extracts it
  (scripts, comments and hidden elements dropped, links followed by their
  URL) and wrapped in the same untrusted-content envelope.
- `browser_act(action, selector, text)` clicks, fills, presses a key, scrolls
  or selects an option. A selector is CSS, or `text=Sign in` to match by
  visible text.
- `browser_screenshot(full_page)` saves a PNG under `screenshots/` in the
  run's workspace and returns its path.
- `browser_close()` ends the session. Idle sessions also close on their own.

Each run gets its own browser session (its own cookies and storage), keyed by
the run id.

### Enabling it

The browser runs as a separate service, `deploy/browser/`, in its own
container. With compose:

```
# .env
AGENTS_HUB_BROWSER_URL=http://browser:3000
AGENTS_HUB_BROWSER_TOKEN=<a long random string>

docker compose --profile browser up --build
```

Without those two settings the tools answer that the browser service is not
configured, and the agent carries on. The service's own limits are
environment variables on its container: `BROWSER_MAX_SESSIONS` (8),
`BROWSER_IDLE_TIMEOUT` in seconds (300), `BROWSER_NAV_TIMEOUT_MS` (20000).

### Security model

The same rules as `fetch_url`, enforced twice:

- **In the hub.** Every URL the agent names goes through `validate_url` (scheme,
  the workspace's domain policy, the private-network block) before the service
  is called at all, and the address the page ends up on after an open or an
  action is checked again. A landing page that fails closes the session.
- **In the service.** The session is created with the workspace's domain
  policy (deny list, opt-in allow list, subdomain matching identical to
  `fetch_url`). Every request the page makes, not only the one the agent asked
  for, is routed through a filter: images, scripts, XHR, WebSockets and each
  redirect hop. Redirects are fetched one hop at a time and the `Location` is
  checked before the browser follows it. A request to a loopback, RFC1918,
  link-local or cloud metadata address is refused on every address the host
  resolves to. The check is the hub's own `common/ssrf.py`, copied into the
  image at build time rather than rewritten. Service workers are blocked so
  no request can bypass the filter, and downloads are off.
- **Reachability.** The service requires the shared token on every call and
  refuses to start without one. In compose it sits alone on its own network
  with no published port, so pages cannot reach the database, Redis or any
  other of our services.

What remains: the host is resolved by the filter and then again by the
browser, so a DNS answer that changes in between (rebinding) is not caught by
the service alone. For a hard guarantee, add an egress firewall rule for the
browser's network that drops private ranges.

Capabilities: `browser_open`, `browser_read`, `browser_act` and
`browser_screenshot` are classified exactly like `fetch_url`, ingesting
untrusted content and able to send data out (the URL, and anything typed into
a form). `browser_close` grants nothing. `browser_act` is not idempotent: an
interrupted click is reported to a resumed run, never repeated blindly. With
the approval gate on, it needs a yes every time.

## run_code

`run_code(language, code, timeout, stdin, mount_workspace)` runs a Python,
Node or Bash snippet in a throwaway container and returns its exit code,
duration, stdout and stderr, truncated like `run_shell`'s.

The container has no network, a read-only filesystem apart from a small
`/tmp`, no capabilities, runs as `nobody`, and is limited in memory, CPU and
process count. It receives no environment from the hub. The workspace is not
mounted unless the agent asks: `mount_workspace=True` mounts it read-only at
`/work`, so the code can analyse files but not change them.

### Why prefer it over run_shell

`run_shell` runs on the hub's host, in the workspace, with the network: the
right tool for building and testing a project the agent owns, and the wrong
one for a snippet whose content came from a web page, a user or another
model. `run_code` gives that snippet nothing to damage and nowhere to send
what it finds. Its default timeout is 60 seconds, capped by
`CODE_RUNNER_MAX_TIMEOUT` (300).

### Settings

- `CODE_RUNNER_IMAGES`: image per language, as JSON (`{"python":
  "python:3.13-slim"}`) or `python=...,node=...`. Defaults: `python:3.12-slim`,
  `node:20-slim`, `bash:5`. Pull them ahead of time: a pull counts against the
  snippet's timeout.
- `CODE_RUNNER_MEMORY` (`512m`), `CODE_RUNNER_CPUS` (`1`),
  `CODE_RUNNER_PIDS_LIMIT` (`128`).
- `CODE_RUNNER_FALLBACK`: what happens when docker is unavailable. `none`
  (default) returns an error. `local` runs the snippet as a plain subprocess
  in a temporary directory with the provider keys scrubbed from its
  environment and the same timeout. That has no network or filesystem
  isolation at all, so it is an opt-in for development machines.

Capabilities: in its container `run_code` only reads private data (the
optional workspace mount), since nothing can come in or go out without a
network. With `CODE_RUNNER_FALLBACK=local` it is classified like `run_shell`,
the whole trifecta, because the snippet then has the network and the host's
filesystem. It is not idempotent, and with the approval gate on it needs a
yes every time, like `run_shell`.

## Group aliases

A tool list may name a group (`filesystem`, `task_management`, `service_ops`,
`entity_runs`, …) instead of its members. A group grants the union of what its
members grant.

## Approval gates, which are a different thing

Some tools refuse until `user_approved=True`: starting a flow, scenario, team or
loop, and every Service Agent action that stops something. That gate is about
spending money and destroying state, not about data. The refusal carries the
estimate or names what would be destroyed, so you approve with the number in
front of you.

That one is advisory: the agent carries the refusal to you and calls again once
you agree. A workspace can also *enforce* it, holding a destructive call until a
person answers on the task page, and run its own code around every tool call.
See [hooks](hooks.md).

Related: [agents](agents.md), [hooks](hooks.md), [system-agents](system-agents.md), [service-health](service-health.md), [web-logs](web-logs.md).

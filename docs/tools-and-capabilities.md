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
  Search Agent and the Researcher are separate.
- **What a web tool actually read is recorded.** Every `web_search` and
  `fetch_url` call, with the text handed back and the flags raised against it,
  lands in the [web request log](web-logs.md).
- **Log readers count as ingesting untrusted content.** A run log holds whatever
  that run handled, including pages it fetched. So the Service Agent, which
  reads every log, is given no outbound channel at all.
- **Adding a tool in the editor can be refused.** The message names the
  capabilities that collided.

## Lifting the block: per agent, or for every agent

An agent of an [isolated workspace](isolation.md) is not judged at all: its
shell and code have no network and its tools are the perimeter's allowlist, so
nothing it combines can send data out.

Two switches turn a refusal into a warning, and both keep the combination
visible on the agent page and in the log rather than pretending it is gone.

- **Per agent.** The Tools tab of an agent has a checkbox, "Lift the block for
  this agent: warn only" (`capability_override` on the record,
  `POST /api/agents/{id}/capability-override`). With it on, a blocked
  combination on that agent, in its own tools or reached through an agent it
  delegates to, is saved and returned as `capability_warning`. Turning it off
  again never fails: the record keeps what it already holds, and the next tool
  or delegate that would widen it is refused again. A system workspace agent
  never gets the switch.
- **For every agent.** Settings, "Agent execution", "Capability guard":
  `block` (default), `warn` or `off`, written to `.env` as `CAPABILITY_GUARD`
  and read live, so the change applies to the next save or build without a
  restart. `warn` saves and builds everything and logs the combination; `off`
  stops checking, warnings included.

A stricter setting sits under the mode: "A per-agent exemption needs a
no-network container" (`CAPABILITY_OVERRIDE_REQUIRES_CONTAINER`, on by
default). In block mode, an agent's override is then honoured at build time
only when the agent runs in a container with the network set to `none`; a
local run of that agent is still refused, and its page says so next to the
checkbox. Turn the setting off to honour the exemption everywhere, or switch
the guard to `warn`.

## Delegation counts as holding the capability

An agent that cannot itself exfiltrate, but can hand a request to an agent
that can, effectively can. `run_agent_tool`, `delegate_task_tool`,
`wait_for_agent_tool`, `run_flow_tool`, `run_team_tool`, `run_loop_tool` and
`run_scenario_tool` are the edges of that graph: each one hands work (and, for
the wait/poll tools, eventually the result) to another agent whose own tools
are not in the caller's tool list at all.

So the guard evaluates the *effective* set: an agent's own grants, unioned
with the grants of every agent it can reach through one of those tools. An
agent's `delegates` field decides reach — a non-empty allowlist restricts it
to exactly those ids; empty or absent means unrestricted, and it can reach
every agent in the registry. `create_agent_tool` and `modify_agent_tool` are
not delegation edges: whatever they create or change is re-validated by this
same guard at save time, so there is nothing extra to add on top.

A combination that only closes through delegation blocks at the rule's own
severity, exactly like one the agent's own tools form: an agent that reads
private data and can notify the user cannot be given a web searcher as a
delegate, since it then effectively holds all three. The rule id carries a
`_via_delegation` suffix and the report names the path, not just the tools:

> Lethal trifecta via delegation: ingests untrusted content (via
> run_agent_tool -> web_searcher: fetch_url, web_search) + reads private data
> (read_file) + can send data outside (notify_user).

Saving such a `delegates` list is refused with a 409 that carries this report,
and the Delegation card shows it under the toggles. To remove it, narrow the
agent's `delegates` list, take the offending tool off the delegate, or lift the
block for the agent (above). A record that already held the combination when
the guard was turned on is grandfathered: it saves unchanged or narrower, but
cannot gain a new violating capability.

The tables backing all of this live in `tools/capabilities.py`: `CAPABILITY_GRANTS`
(what a tool grants directly), `REVIEWED_NO_GRANT` (classified, grants nothing —
a write into the hub's own store, a control action, a piece of configuration),
and `DELEGATING_TOOLS` (the seven edges above). Every catalog tool id ends up in
exactly one of them; a test enforces that, and `python -m agents.capability_guard`
reports zero unclassified tools alongside the current roster's violations.

## Web search provider

`web_search` calls an external search API; the hub ships no search engine of
its own. Settings, "Web search" picks the provider (Brave
Search, Tavily or Exa), takes its API key and the number of results a call
returns, and writes them to `.env` as `WEB_SEARCH_PROVIDER`,
`WEB_SEARCH_API_KEY` and `WEB_SEARCH_MAX_RESULTS`. They are read live, so a
change applies to the next call in the backend and in every runner without a
restart. With no provider or no key the tool performs no search and answers
that it is not configured, an ordinary tool answer the agent relays to the
user. `fetch_url` and the browser tools need none of this.

The same page holds the `fetch_url` limits (`WEB_FETCH_MAX_CHARS`,
`WEB_FETCH_TIMEOUT`, `WEB_FETCH_MAX_REDIRECTS`) and the global domain policy:
a deny list that always applies to fetches and to search results
(`WEB_DENY_DOMAINS`), and an opt-in allow list (`WEB_DOMAIN_POLICY_ENABLED`,
`WEB_ALLOW_DOMAINS`) that, once on, is the only set of hosts a fetch may reach.
A host matches itself and its subdomains. The file holds each list as a JSON
array, which is how pydantic-settings reads a list field; a comma-separated
line typed by hand is accepted too. A workspace's own lists replace the
global ones for runs in that workspace: the workspace page, Settings tab,
"Web access" (`GET`/`PUT /api/workspaces/{name}/web-policy`), stored in the
workspace's metadata; an empty list there keeps the global one.

## Domain lists per agent

An agent can carry its own `allowed_domains` and `blocked_domains` (the Web
domains card on its Behavior tab, or `PUT /api/agents/{id}/web-domains`). They
merge with the workspace's lists and the global ones above:

- the workspace's deny list (or the global one, when the workspace sets none)
  plus the agent's `blocked_domains` always apply;
- the workspace's allow list, when that policy is on, comes next;
- the agent's `allowed_domains`, when set, narrows further: a host must be on
  it as well. It cannot widen what the workspace allows;
- a blocked host wins over an allowed one, and every entry covers its
  subdomains.

`fetch_url` refuses a host outside the merged lists with a tool result that
names the list (`host 'x' is on this agent's blocked_domains`). `web_search`
hands the lists to the search backend as its own filters (Tavily
`include_domains` and `exclude_domains`, Exa `includeDomains` and
`excludeDomains`, Brave `site:` and `-site:` in the query) and then filters the
results by host whatever the backend did. The browser's session policy takes
the agent's blocked hosts into its deny list and its allowed hosts into its
allow list. `GET /api/agents/{id}/web-domains?workspace=` also answers the
merged lists for that workspace.

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

A person can watch that session live from the run's output, take control of it
(clicks, typing, an address bar, all under the same policy), browse on their
own from the Browser page and hand a page to an agent, whose browser tools then
continue in it. See [The browser](browser.md).

### Enabling it

The quickest way is **Settings → Browser**: address, token, local process or
container, Start, Chromium install, all without a restart (see
[The browser](browser.md), "Setting it up"). By hand, the browser runs as a
separate service, `deploy/browser/`, in its own container. With compose:

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
Node or Bash snippet in a throwaway sandbox and returns its exit code,
duration, which [sandbox provider](sandboxes.md) ran it, stdout and stderr,
truncated like `run_shell`'s. The provider is resolved by
`sandbox/registry.py`: the run's environment (`sandbox_provider`) first, else
`CODE_RUNNER_PROVIDER` (default `docker`); see [sandboxes](sandboxes.md) for
docker, local, e2b and modal.

The default (docker) container has no network, a read-only filesystem apart
from a small `/tmp`, no capabilities, runs as `nobody`, and is limited in
memory, CPU and process count. It receives no environment from the hub. A
run whose environment sets network `limited` relaxes that to the
environment's own allowed hosts instead of a blanket refusal; everything
else (no environment, or `unrestricted`/`none`) keeps the historical
no-network sandbox. The workspace is not mounted unless the agent asks:
`mount_workspace=True` mounts it read-only at `/work` (docker and local
only; the remote providers, e2b and modal, have no local filesystem to
mount from and refuse the request instead of silently ignoring it).

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
- `CODE_RUNNER_PROVIDER` (default `docker`): the sandbox provider a run gets
  when its environment does not name one. `E2B_API_KEY`/`E2B_TEMPLATE` and
  `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`/`MODAL_IMAGE` configure the e2b and
  modal providers; see [sandboxes](sandboxes.md).

Capabilities: in the default (docker, network none) sandbox `run_code` only
reads private data (the optional workspace mount), since nothing can come in
or go out without a network. With `CODE_RUNNER_FALLBACK=local` it is
classified like `run_shell`, the whole trifecta, because the snippet then has
the network and the host's filesystem. It is not idempotent, and with the
approval gate on it needs a yes every time, like `run_shell`.

In a run whose environment has a `limited` network the snippet reaches that
environment's hosts, whatever the provider (docker through the enforced
fence, e2b and modal through their own allowlists). It then gets no
workspace mount (`mount_workspace` is refused), and the guard classifies it
at build time, when the run's environment is known, as ingesting untrusted
text and able to send data out, like the web tools: an agent that also reads
private files is the whole trifecta and is refused. Any other network
setting, `unrestricted` included, still gives the snippet no network.

The Chat code panel's Run button (see [chat](chat.md#code-panel) and
[views](views.md#code)) goes through the same sandbox: `POST
/api/views/{id}/code/run` calls the same `run_snippet` machinery this tool
calls, with the same docker isolation and the same `CODE_RUNNER_FALLBACK`
behaviour. It is a dashboard route, not an agent tool, so it carries no
approval gate of its own, but it honours the view's own workspace scoping,
mounting read-only exactly the way `mount_workspace` does here.

## Connector tools

Tools that integrate with external services come from [connectors](connectors.md),
[channels](channels.md), [trackers](trackers.md) and [integrations](integrations.md).
Each grants one or more capabilities: reading typically grants `reads_private`
and `ingests_untrusted`, writing grants `can_exfiltrate`.

| Connector | Tools | Grants |
|---|---|---|
| Chat channels | `channel_send` | `can_exfiltrate` |
| Git | `git_publish` | `can_exfiltrate` |
| Issue trackers (Jira, Linear) | `tracker_list_issues`, `tracker_get_issue`, `tracker_sync` | `reads_private`, `ingests_untrusted` |
| | `tracker_comment`, `tracker_transition`, `tracker_create_issue` | `can_exfiltrate` |
| Google Workspace | `google_drive_search`, `google_drive_import`, `google_sheets_read`, `google_calendar_list` | `reads_private`, `ingests_untrusted` |
| | `google_sheets_append`, `google_docs_create`, `google_calendar_create` | `can_exfiltrate` |
| Microsoft Graph | `outlook_calendar_list` | `reads_private`, `ingests_untrusted` |
| | `outlook_calendar_create` | `can_exfiltrate` |
| Notion | `notion_search`, `notion_read_page`, `notion_import` | `reads_private`, `ingests_untrusted` |
| | `notion_create_page`, `notion_append` | `can_exfiltrate` |
| Confluence | `confluence_search`, `confluence_read_page`, `confluence_import` | `reads_private`, `ingests_untrusted` |
| | `confluence_create_page` | `can_exfiltrate` |
| Databases | `db_list_connections`, `db_schema`, `db_query` | `reads_private` |
| Connection proposals | `connection_options`, `propose_connection`, `connection_proposal_status` | none: names and flags only, a person applies the change ([connectors](connectors.md)) |

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

A [proactive agent](proactive.md)'s profile is checked the same way: a
webhook, Telegram or file trigger counts as ingesting untrusted content, a
delivery channel other than the inbox as sending data outside. `wake_agent`
grants nothing: it nudges another agent's pulse and gets only an
acknowledgement back.

Related: [agents](agents.md), [hooks](hooks.md), [system-agents](system-agents.md), [service-health](service-health.md), [web-logs](web-logs.md), [proactive](proactive.md).

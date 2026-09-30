# Changelog

Every release of Agents Hub, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/): while the major version is 0, a
minor release may change the API or the configuration, and says so under
**Changed**; a patch release never does. A release that adds schema
migrations says so under **Upgrade notes**, because it cannot be rolled back
without restoring a backup (docs/deployment.md, "Rolling back").

Work in progress goes under Unreleased; `python scripts/release.py cut minor`
turns that section into the next release.

## [Unreleased]

## [0.2.0] - 2026-09-30

### Added

- A spend report by API key, user, project, workspace, agent or model with
  CSV export (`GET /api/accounting/report`, the Costs page, `ah costs
  report`). Every new run records who launched it, the API key and the
  project, and runs created inside a launched run inherit both.
- Prices on the hub's own `/v1` answers, and a monthly money quota per API
  key, enforced on `/v1` and on every run launch with a 429.
- A registry of agents, flows, skills and MCP servers with an owner and a
  review status (the Agent registry page, `ah agent review`). With
  `AGENTS_HUB_REGISTRY_REQUIRE_REVIEW` on, publishing waits for an
  administrator and an edit reopens the review; with
  `AGENTS_HUB_MCP_ALLOWLIST_ONLY` on, only MCP servers from the approved
  catalog attach and load tools (`ah mcp catalog`).
- `ah support-bundle` and a Health page button: version, doctor, health,
  migrations, masked config, recent errors, log tails and SLO status in one
  zip with secrets scrubbed. A runbook of twelve typical failures.
- Service level objectives for run start time (p95) and error rate: the
  Health page card, `/metrics`, Prometheus alert rules and an optional Helm
  PrometheusRule, and notification rules that fire once on a breach and once
  on recovery.
- "To eval case" on any run (agent, chat, flow, team, loop, scenario), with a
  dialog that asks what should have happened for a failed run, and
  `ah eval add-case --from-run`.
- A proposed prompt fix from an eval run's failed cases: a diff with a
  rationale, applied as a new agent version and rerun on the same columns,
  on request or by itself when a set turns it on.
- Releases: one semver version across the backend, the dashboard and the Helm
  chart, `scripts/release.py`, a release workflow that publishes versioned
  images to GHCR, `ah version` and `GET /api/system/version`.
- An automatic database archive before a SQLite database takes new schema
  migrations, kept in `.agents_hub/backups/` for a rollback.
- Conversations and integrations: an embeddable chat widget, agents as models
  on `/v1`, conversation handoff with history filters, workspace files by id
  with cited RAG answers, batch eval runs at half price, versioned skills
  synced from `.claude/skills`, and sandbox providers (docker, local, e2b,
  modal) with an enforced docker network fence.
- The agent loop's policies: a per-call tool permission policy, task outcomes
  graded against a rubric, agent version pins, steering a running turn,
  in-loop compaction and tool search, schema-validated structured output,
  fallback models, memory versions and guardrails.
- Environments, deployments, a per-run money cap and a firing journal with
  automatic pause.
- Delegation inside a task on a chosen model.

### Changed

- Every outcome grading is a run of its own, with its own cost, instead of
  being added to the run it graded.
- Model calls made beside the agent loop (policy classifier, guardrail judge,
  schema repair) are priced and count against the run's money cap.

### Security

- Stream tickets instead of tokens in WebSocket and SSE URLs, API rate limits,
  credentials only with an explicit CORS origin list, a budget that can fail
  closed, and fencing tokens on leases.

### Upgrade notes

- Schema migrations 0017 to 0026 and 0028 are added. Take `ah db backup` first on
  Postgres; a SQLite database is archived automatically before it migrates.
- The frontend's `package.json` and the Helm chart now carry the same version
  as `pyproject.toml`; the chart's image tags default to its `appVersion`.

## [0.1.0] - 2026-09-24

The first versioned state of the hub, everything up to commit `c655dde`:
agents, tasks, projects and workspaces from the terminal or the dashboard;
flows, teams, loops and scenarios; memory with RAG; evals and online evals;
identity with OIDC, SCIM, audit and API keys; Postgres, workers, leases and
the high availability deployment; local models and the hub as an
OpenAI-compatible provider; the browser service and the lab.

[Unreleased]: https://github.com/snooker155/agents_hub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/snooker155/agents_hub/releases/tag/v0.1.0

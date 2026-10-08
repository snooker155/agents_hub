# Security policy

Agents Hub runs language-model agents that can execute shell commands, write
files, call external services and spend money on model providers. A flaw in
it can be worth more to an attacker than a flaw in an ordinary web app, so
reports are welcome and are answered first.

## Reporting a vulnerability

Please report privately, not in a public issue:

- through GitHub's private vulnerability reporting on this repository
  (Security tab, "Report a vulnerability"), or
- by email to izub.anton@gmail.com, with "Agents Hub security" in the subject.

Include what you found, the version or commit, the auth mode
(`single`, `token` or `multi`), the execution mode (`local` or `docker`) and
the steps to reproduce. A proof of concept against your own installation is
enough; do not test against installations you do not run.

What happens next:

| Step | Within |
| --- | --- |
| Acknowledgement | 3 working days |
| First assessment (accepted, needs more, or not a vulnerability) | 10 working days |
| Fix released for a confirmed high or critical issue | 30 days, sooner when it is exploited |

A confirmed issue gets a GitHub security advisory and a line in
`CHANGELOG.md` when the fix ships. You are credited unless you
ask not to be. There is no bug bounty.

## Supported versions

While the major version is 0, only the latest minor release receives security
fixes. Upgrade to it before reporting, if you can.

| Version | Supported |
| --- | --- |
| latest 0.x minor | yes |
| older releases | no |

## What counts

In scope, for example:

- a way past authentication or role checks in `token` or `multi` mode,
  including a member reaching another workspace's records;
- a run escaping what its mode promises: an isolated workspace reaching the
  network, a run token reaching a route outside the relay routes, a container
  run writing outside its mounts;
- an agent tool set that holds all three capabilities (ingests untrusted,
  reads private, can exfiltrate) without the capability guard refusing it;
- secrets readable in the clear by someone without `AGENTS_HUB_SECRET_KEY`;
- server-side request forgery past `common/ssrf.py`;
- a widget visitor reaching anything beyond their own thread.

Not vulnerabilities by themselves, because they are documented choices (see
`docs/threat-model.md`):

- `single` mode has no login. It is meant for one person on a machine only
  they can reach; exposing it to a network is a configuration error.
- In `local` execution mode a run has the operator's own permissions on the
  host. Use `docker` mode or an isolated workspace for code you do not trust.
- A model can be talked into things by text it reads (prompt injection). The
  capability guard, approval gate, hooks and guardrails limit what follows
  from it; a report that shows an injection getting past those is in scope.
- Prompts and outputs are sent to the model provider an agent is configured
  with.

## Hardening

The short list for anything beyond one person's laptop:

1. Set `AUTH_MODE=multi` (or at least `token`) before the hub is reachable
   from a network, and put it behind TLS.
2. Set `AGENT_EXECUTION_MODE=docker`, and use environments with network
   `limited` or `none` for agents that read untrusted content.
3. Generate `AGENTS_HUB_SECRET_KEY` with `ah secrets keygen` and keep it apart
   from the database backups.
4. Leave `CAPABILITY_GUARD=block` (the default) and turn on the approval gate
   for workspaces whose agents act on the outside world.
5. Run `ah doctor`. Its `security` check flags a capability guard not on
   `block`, stored secrets without a key, and `token` or `multi` mode with
   local runs.

The full model of what the hub defends, against whom, and what it leaves to
the operator is in `docs/threat-model.md`.

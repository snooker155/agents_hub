# Security policy

Agents Hub runs language-model agents that can execute shell commands, write
files, call external services and spend money on model providers. A flaw in
it can be worth more to an attacker than a flaw in an ordinary web app, so
reports are welcome and are answered first.

## Reporting a vulnerability

Please report privately, not in a public issue:

- through a confidential issue on the GitLab project
  (https://github.com/snooker155/agents_hub/-/issues, tick "This issue is confidential"), or
- by email to izub.anton@gmail.com, with "Agents Hub security" in the subject.

Include what you found, the version or commit, the execution mode
(`local` or `docker`) and the steps to reproduce. A proof of concept against your own installation is
enough; do not test against installations you do not run.

What happens next:

| Step | Within |
| --- | --- |
| Acknowledgement | 3 working days |
| First assessment (accepted, needs more, or not a vulnerability) | 10 working days |
| Fix released for a confirmed high or critical issue | 30 days, sooner when it is exploited |

A confirmed issue gets a line in
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

- a run escaping what its mode promises: a container run writing outside its
  mounts;
- an agent tool set that holds all three capabilities (ingests untrusted,
  reads private, can exfiltrate) without the capability guard refusing it;
- secrets readable in the clear by someone without `AGENTS_HUB_SECRET_KEY`;
- server-side request forgery past `common/ssrf.py`.

Not vulnerabilities by themselves, because they are documented choices:

- The API has no authentication. The hub is meant for one person on a machine
  only they can reach; exposing it to a network is a configuration error.
- In `local` execution mode a run has the operator's own permissions on the
  host. Use `docker` mode for code you do not trust.
- A model can be talked into things by text it reads (prompt injection). The
  capability guard, approval gate, hooks and guardrails limit what follows
  from it; a report that shows an injection getting past those is in scope.
- Prompts and outputs are sent to the model provider an agent is configured
  with.

## Hardening

The API has no authentication: anyone who can reach the port can read every
record, use every key stored in Settings and run agents. The hub must listen
on `localhost` or on a private network that only you can reach, never on a
public address.

1. Keep the backend and the dashboard bound to `127.0.0.1`. If you need
   another machine, use an SSH tunnel or a private network, not an open port.
2. Set `AGENT_EXECUTION_MODE=docker` for agents that read untrusted content.
3. Generate `AGENTS_HUB_SECRET_KEY` with `ah secrets keygen` and keep it apart
   from the database backups.
4. Leave `CAPABILITY_GUARD=block` (the default) and turn on the approval gate
   for workspaces whose agents act on the outside world.
5. Run `ah doctor`. Its `security` check flags a capability guard not on
   `block` and stored secrets without a key.

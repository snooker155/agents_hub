# Secrets

Encrypted values that belong to a workspace, and optionally to one agent in
it, handed to an agent run as environment variables, and only under the names
that agent declares.

## Why this exists

Before secrets, a key lived in one of two places. `.env` holds one value for
the whole hub, so every workspace and every agent gets the same GitLab token.
A workspace's `env_vars` (Settings, per workspace) are stored in plain text in
the workspace metadata, so anyone holding a database backup holds the keys.
Neither can say "this token belongs to this agent", and both hand everything
to every run.

A secret is a row in the `secrets` table. Its value is encrypted before it is
written, the API never returns it, and a run receives it only when its agent
lists the name.

## The key

Values are encrypted with Fernet (AES with an HMAC, from the `cryptography`
package) under `AGENTS_HUB_SECRET_KEY`. Generate one and save it to `.env`:

```
ah secrets keygen --write
```

It only fills a key that is not set yet: replacing one would leave every stored
secret unreadable, so it refuses. Without `--write` it prints the key, for a
`.env` elsewhere or a secret manager; the line looks like this:

```
AGENTS_HUB_SECRET_KEY=3q2-7w...=
```

Any other string works as well: it is treated as a passphrase and stretched
with PBKDF2-HMAC-SHA256 (200,000 iterations). A generated key is stronger than
most passphrases, so prefer it.

With no key configured, storing a secret is refused with a message saying so,
existing secrets still list (names, scopes and hints need no key), and runs
receive none of them.

Keep the key somewhere other than the database backup. A backup and its key
together are the secrets in the clear; either alone is not.

## Scopes and precedence

Every secret has a name and a scope inside its workspace:

| scope | set with | used by |
|---|---|---|
| workspace-wide | no agent | any agent that declares the name |
| agent | `--agent` / agent select | that agent only |

The same name may exist at both scopes. For one run, the agent's own value
wins over the workspace-wide one.

## The agent's allowlist

An agent receives a secret only if its record lists the name in `secrets`:

```json
{ "id": "release_bot", "tools": ["git_publish"], "secrets": ["GITLAB_TOKEN"] }
```

An empty list, which is the default, means none. A workspace can hold many
secrets and an agent sees exactly the ones it asked for.

The list is edited with `PUT /api/agents/{id}/secrets` (body
`{"secrets": ["GITLAB_TOKEN"]}`).

**The capability guard counts a declared secret as reading private data.** An
agent that ingests untrusted input (a web fetch, an issue body) and can send
data out already forms a warning; add a secret and it forms the full trifecta,
which is refused at save time. The violation names the source as
`secrets:GITLAB_TOKEN`, next to the tools that supply the other two
capabilities. A delegate's declared secrets count for whoever can delegate to
it, the same way its tools do. See [tools and capabilities](tools-and-capabilities.md).

The guard's mode decides what "refused" means. In Settings → Capability guard,
**Block** refuses the save, **Warn only** saves it and shows the warning, and
**Off** skips the check. An agent can also be exempted on its own Behavior tab
("Lift the block for this agent"); its save then goes through with a warning
whatever the mode.

### A secret on a system agent, for every workspace

A system agent (`writer`, `analyst`, `researcher` and the rest) is one record
serving every workspace, so a name declared on it is declared everywhere. The
card on its Behavior tab says so. When a run of a system agent asks for a
declared name its workspace does not hold, the value comes from the
**default** workspace instead. An operator who wants one credential for all
workspaces (a read-only token for a shared data source, say) stores it once in
default and declares the name on the system agent. A workspace that stores its
own secret under the same name gets its own value for its runs.

Only system agents fall back this way: an agent made in a workspace never
reaches into default's secrets. Declaring secrets is not an edit of the agent's
design, so a system agent keeps receiving the updates of its tools and prompt
that come with new hub versions.

## How a run receives them

The launcher builds the run's environment (`common/subprocess_env.py`) and,
last, merges in the declared names resolved for this agent. A
declared secret therefore wins over a variable of the same name inherited
from the host. A docker run gets the same environment through the container
launcher, so it too sees only the allowed names.

A flow runs every node in one process, so it cannot keep one agent's value
from another. A flow run receives the union of the names its agent nodes
declare, resolved at workspace scope only; agent-scoped values stay with
single-agent runs.

A lookup failure (no key, a value that no longer decrypts) is logged by name
and hands out nothing. It never stops the launch and never falls back to
handing out more. The one exception is a secret bound to hosts, see below.

**In-process runs.** A chat turn, an entity build chat, the decomposer and a
delegated worker run inside the backend process, where there is no
environment to build. They enter the agent's *secret scope* instead
(`common.secrets.activate`): for the duration of the turn, a tool that asks
`common.secrets.get(name)` receives the declared names resolved for the
agent, and nothing else. The git connector reads the run's `GITLAB_TOKEN` this
way, so a merge request opened from chat carries the same identity as one
opened from a task run. A delegate gets a scope of its own, with its own
allowlist, in the same workspace.

## Managing them

In the dashboard: a workspace's Secrets tab lists them. It lists name,
scope, the last four characters of the value (only for values longer than
eight characters), the hosts it is bound to and when it was set. The agent side is on the agent's
Behavior tab: a Secrets card with the names this agent may receive, offering the
names the current workspace holds as one-click suggestions. Saving it goes
through the capability guard like the tools list does.

From the terminal:

```
ah secrets list   --workspace acme
ah secrets set    GITLAB_TOKEN --workspace acme --agent release_bot   # prompts, hidden
ah secrets set    OPENAI_API_KEY --workspace acme --value - < key.txt
ah secrets delete GITLAB_TOKEN --workspace acme --agent release_bot
```

Over REST: `GET /api/workspaces/{name}/secrets`,
`PUT /api/workspaces/{name}/secrets/{secret}` with `{value, agent_id?}`,
`DELETE /api/workspaces/{name}/secrets/{secret}?agent_id=`.

## Secrets bound to hosts

A secret may carry `allowed_hosts` (set with `allowed_hosts` in the `PUT`
body, or `PUT /api/workspaces/{name}/secrets/{secret}/hosts`). Inside the
backend process a tool receives such a secret only when it names one of those
hosts (a subdomain of a listed host matches). A run in a separate process
cannot be watched that way, so its launch is refused with an explanation
instead of handing the value out unbound. Setting
`AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK=1` hands the value over in plain text
anyway. This is the one secrets failure that stops a launch.

## Agent identity in external systems

A token is an identity. Stored as an agent-scoped `GITLAB_TOKEN`, a personal
access token for a machine account makes that agent's pushes and merge
requests come from its own account, with only that account's access. The git
tools read the token the run holds before falling back to the one configured
under Settings, Connectors.

## Gotchas

- **Rotating the key re-encrypts nothing.** Values written under the old key
  stop decrypting (the log names each one) and runs stop receiving them. Set
  each one again after changing `AGENTS_HUB_SECRET_KEY`:
  `ah secrets list` shows what there is, `ah secrets set` replaces it.
- **A name must be an environment variable name.** Upper case letters, digits
  and underscores, starting with a letter, at most 64 characters. `gitlab_token`
  is refused; the dashboard upper-cases what you type.
- **`env_vars` still work.** A workspace's plain `env_vars` remain as a
  fallback and are not migrated. Move anything sensitive out of them.
- **A value cannot be read back.** Not from the dashboard, the CLI or the API.
  Keep the original somewhere if you will need it again.
- **The host environment still leaks into runs.** A run's environment starts
  as a copy of the backend's. A `GITLAB_TOKEN` exported in the shell that
  started the backend reaches every run, declared or not; a declared secret
  replaces it, an undeclared one does not remove it.

## Related

- [tools and capabilities](tools-and-capabilities.md): the trifecta rule a declared secret takes part in
- [workspaces](workspaces.md): per-workspace `env_vars`, the plain fallback
- [settings](settings.md): where `.env` values and the git connector token live
- [containers](containers.md): how a docker run gets its environment
- [cli](cli.md): the `ah` command

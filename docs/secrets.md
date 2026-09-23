# Secrets

Encrypted values that belong to a workspace, and optionally to one agent or
one user in it, handed to an agent run as environment variables, and only
under the names that agent declares.

## Why this exists

Before secrets, a key lived in one of two places. `.env` holds one value for
the whole hub, so every workspace and every agent gets the same GitHub token.
A workspace's `env_vars` (Settings, per workspace) are stored in plain text in
the workspace metadata, so anyone holding a database backup holds the keys.
Neither can say "this token belongs to this agent", and both hand everything
to every run.

A secret is a row in the `secrets` table. Its value is encrypted before it is
written, the API never returns it, and a run receives it only when its agent
lists the name.

## The key

Values are encrypted with Fernet (AES with an HMAC, from the `cryptography`
package) under `AGENTS_HUB_SECRET_KEY`. Generate one:

```
ah secrets keygen
```

and put the printed line in `.env`:

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
| workspace-wide | neither agent nor user | any agent that declares the name |
| agent | `--agent` / agent select | that agent only |
| user | `--user` / user select | runs launched by that user |
| agent and user | both | that agent, in runs that user launched |

The same name may exist at several scopes. For one run, the most specific
wins: agent and user, then agent, then user, then workspace-wide. A run's user
is whoever started it (the signed-in account in `multi` mode, `local`
otherwise); a run started by a schedule or the system has no user, so only
agent and workspace-wide values apply.

## The agent's allowlist

An agent receives a secret only if its record lists the name in `secrets`:

```json
{ "id": "release_bot", "tools": ["git_publish"], "secrets": ["GITHUB_TOKEN"] }
```

An empty list, which is the default, means none. A workspace can hold many
secrets and an agent sees exactly the ones it asked for.

The list is edited with `PUT /api/agents/{id}/secrets` (body
`{"secrets": ["GITHUB_TOKEN"]}`); in `multi` mode only an administrator, or the
owner of the workspace the agent belongs to, may change it.

**The capability guard counts a declared secret as reading private data.** An
agent that ingests untrusted input (a web fetch, an issue body) and can send
data out already forms a warning; add a secret and it forms the full trifecta,
which is refused at save time. The violation names the source as
`secrets:GITHUB_TOKEN`, next to the tools that supply the other two
capabilities. A delegate's declared secrets count for whoever can delegate to
it, the same way its tools do. See [tools and capabilities](tools-and-capabilities.md).

## How a run receives them

The launcher builds the run's environment (`common/subprocess_env.py`) and,
last, merges in the declared names resolved for this agent and this user. A
declared secret therefore wins over a variable of the same name inherited
from the host. A docker run gets the same environment through the container
launcher, so it too sees only the allowed names.

A flow runs every node in one process, so it cannot keep one agent's value
from another. A flow run receives the union of the names its agent nodes
declare, resolved at workspace and user scope only; agent-scoped values stay
with single-agent runs.

A lookup failure (no key, Vault unreachable, a value that no longer decrypts)
is logged by name and hands out nothing. It never stops the launch and never
falls back to handing out more.

## Managing them

In the dashboard: a workspace's Agents tab has a Secrets card, in every mode.
It lists name, scope, the last four characters of the value (only for values
longer than eight characters), and when it was set. In `multi` mode only the
workspace owner or an administrator sees it.

From the terminal:

```
ah secrets list   --workspace acme
ah secrets set    GITHUB_TOKEN --workspace acme --agent release_bot   # prompts, hidden
ah secrets set    OPENAI_API_KEY --workspace acme --value - < key.txt
ah secrets delete GITHUB_TOKEN --workspace acme --agent release_bot
```

Over REST: `GET /api/workspaces/{name}/secrets`,
`PUT /api/workspaces/{name}/secrets/{secret}` with `{value, agent_id?, user_id?}`,
`DELETE /api/workspaces/{name}/secrets/{secret}?agent_id=&user_id=`. Setting
and deleting are written to the audit trail as `secret.set` and
`secret.delete`, with the name and the scope, never the value.

## Vault

Set `AGENTS_HUB_SECRET_BACKEND=vault` to keep values in HashiCorp Vault (KV
version 2) instead of the database:

| variable | meaning |
|---|---|
| `AGENTS_HUB_VAULT_URL` | e.g. `https://vault.internal:8200` |
| `AGENTS_HUB_VAULT_TOKEN` | a token with read, write and delete on the path below |
| `AGENTS_HUB_VAULT_MOUNT` | the KV v2 mount, `secret` by default |

Each value is stored at `agents-hub/<workspace>/<agent or _>/<user or _>/<name>`
under the mount. The `secrets` table keeps a metadata row (name, scope, hint)
with an empty ciphertext, so listing and precedence never need Vault; only
reading a value does. `AGENTS_HUB_SECRET_KEY` is not used with this backend.

## Agent identity in external systems

A token is an identity. Stored as an agent-scoped `GITHUB_TOKEN` (or
`GITLAB_TOKEN`), a GitHub fine-grained personal access token for a machine
account, or a GitHub App installation token, makes that agent's pushes and
pull requests come from its own account, with only that account's access.
Stored as a user-scoped one, the same agent acts on behalf of whoever launched
the run. The git tools read the token the run holds before falling back to
the one configured under Settings, Connectors.

## Gotchas

- **Rotating the key re-encrypts nothing.** Values written under the old key
  stop decrypting (the log names each one) and runs stop receiving them. Set
  each one again after changing `AGENTS_HUB_SECRET_KEY`:
  `ah secrets list` shows what there is, `ah secrets set` replaces it.
- **A name must be an environment variable name.** Upper case letters, digits
  and underscores, starting with a letter, at most 64 characters. `github_token`
  is refused; the dashboard upper-cases what you type.
- **`env_vars` still work.** A workspace's plain `env_vars` remain as a
  fallback and are not migrated. Move anything sensitive out of them.
- **A value cannot be read back.** Not from the dashboard, the CLI or the API.
  Keep the original somewhere if you will need it again.
- **The host environment still leaks into runs.** A run's environment starts
  as a copy of the backend's. A `GITHUB_TOKEN` exported in the shell that
  started the backend reaches every run, declared or not; a declared secret
  replaces it, an undeclared one does not remove it.
- **Chat turns do not receive secrets yet.** A chat runs its agent inside the
  backend process, where environment variables would be shared by every
  request. Code there reads a secret through `common.secrets.get(name)` inside
  `common.secrets.activate(...)`, which the chat paths do not set up yet.

## Related

- [identity](identity.md): who may manage secrets in each mode
- [tools and capabilities](tools-and-capabilities.md): the trifecta rule a declared secret takes part in
- [workspaces](workspaces.md): per-workspace `env_vars`, the plain fallback
- [settings](settings.md): where `.env` values and the git connector token live
- [containers](containers.md): how a docker run gets its environment
- [cli](cli.md): the `ah` command

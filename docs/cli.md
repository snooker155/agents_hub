# The CLI

`ah` drives the service from a terminal. Everything the dashboard does to
agents, tasks, workspaces, projects and nodes has a command here, and the
command is the application rather than a client of it.

## Two ways it reaches the service

**Direct** (the default). The CLI imports the service and calls its functions in
this process. No server has to be running, nothing is serialized, nothing
crosses a socket. It needs the repository, its dependencies and the state
directory reachable where you run it.

**Over REST.** Set `AGENTS_HUB_URL` and the same commands go to a running
backend instead. This is for the case direct calls cannot serve, most often a
service inside a container. `ah config` prints which one is in use.

Both are the same operations behind one interface, so a command behaves
identically either way. Each invocation is a fresh process, so expect about a
second and a half of import and bootstrap before anything happens.

A write from the CLI reaches an open dashboard the way a write from an agent
subprocess does: the change goes to the shared database and a `<resource>.changed`
event is relayed, so open tabs refetch instead of showing stale rows.

## Getting the command

`./install.sh` installs it. See [installation](installation.md). Without an
install, `python -m cli` from the checkout is the same program, and `ah` below
stands for either.

## The commands

```bash
ah up                                   # backend + dashboard together
ah agent list
ah agent run swe_agent "fix the failing test"
ah chat main-agent                      # interactive REPL, /exit to leave
ah task create "Build a REST API" --decompose
ah task list --status todo
ah workspace list
ah node list
ah db status                            # which database backend, schema version, row counts
ah db migrate --to postgresql://...     # copy the database into Postgres (or back to a file)
ah worker                               # claim launches from the run queue and spawn them here (see workers.md)
ah config
```

Listings print an 8-character ID prefix, and every command that takes an ID
accepts that prefix back. A call that runs an agent waits as long as the model
takes, up to `AGENTS_HUB_AGENT_TIMEOUT` seconds (default 900, `0` for no limit).

## Running both servers

```bash
ah up                  # API on :8000, built dashboard on :5173
ah up --dev            # backend reload + Vite dev server with HMR
ah up --rebuild        # force a fresh bundle
ah up --no-frontend    # just the API
```

Production is the default: no reloader, and the built bundle served with `/api`
forwarded to the API. The bundle is rebuilt whenever its sources are newer than
`dist/index.html`, so an edit never shows up as "nothing happened". Output from
both is interleaved, `Ctrl-C` stops both, and if one exits on its own the other
is stopped too.

There is no `--workers`: the backend starts singletons of its own (plan
scheduler, run watchdog, Telegram poller) and a second worker would run a second
copy of each. Scaling out is compose's job: `docker compose up --scale backend=3`,
where nginx balances `/api` across the replicas.

## Working in your own directories

A workspace normally lives under the state directory, but it can instead point
at a directory you already have. Nothing is copied: agents read and write the
real files in place, and a git checkout keeps its history.

```bash
cd ~/code/myapp
ah workspace init            # registers this directory, named after it
```

Or attach several repositories as projects inside one workspace:

```bash
ah workspace create dev --use
cd ~/code/myapp   && ah project add
cd ~/code/backend && ah project add
ah project get               # branch, uncommitted count, recent commits
```

A directory with a `.git` in it is registered as a repo, so `git-status` and
`git-pull` work on it immediately. An attached workspace or project is named
after its folder, because several code paths derive the name by resolving that
link. Detaching only drops the link: the directory is never deleted from here.

## Selecting a workspace

A process cannot change its parent shell's environment, so the selection is
stored rather than exported, and every later command inherits it:

```bash
ah workspace use dev
ah workspace current         # what is selected, and where it points
ah task create "no -w needed"
ah workspace unuse
```

Precedence is narrowest first: `--workspace` beats `AGENTS_HUB_WORKSPACE`, which
beats the working directory, which beats the stored selection in
`~/.config/agents-hub/cli.json`.

**The working directory selects on its own.** An attached workspace is the
directory it links to, so standing in it (or under it) already names a
workspace, and standing in an attached project names the project too. It is read
from the links under the state root, so it costs no call and simply finds
nothing when this machine's paths are not the ones the service resolves.
`AGENTS_HUB_AUTO_WORKSPACE=0` turns it off.

## The shell integration

`conda activate` is a shell function rather than a program, for the same reason:
only the shell can change its own environment. `shell-init` prints that kind of
function.

```bash
ah shell-init --install      # writes the block into ~/.zshrc
exec zsh
```

It picks the startup file from the shell (`~/.zshrc`, `~/.bashrc` or
`~/.bash_profile`, `~/.config/fish/config.fish`), writes between markers, and
rewrites that region on a re-run instead of stacking another copy.
`shell-init --uninstall` takes it back out, `shell-init [shell]` prints the block
to place yourself, and `--name hub` picks a different function name.

Afterwards the commands that change the selection export it into the current
shell, so two shells activated differently work in two workspaces at once.
Everything else passes straight through.

## Against a containerized backend

```bash
export AGENTS_HUB_URL=http://localhost:8000
ah agent list
```

What changes is that **paths are then resolved by the backend**, so a host path
means nothing inside the container. Bind mount the directory first and pass the
in-container path:

```yaml
# docker-compose.yml, backend service
volumes:
  - ~/code:/host/code
```

```bash
ah workspace init /host/code/myapp
```

Mount one parent directory rather than each project: a container cannot gain new
mounts while running, so anything not mounted at startup is unreachable, and
`workspace init` will correctly refuse a path the backend cannot see.

## Where it lives

The `cli` package in the repository: `cli/main.py` is the Typer application and
every command, `cli/backend.py` is the direct-or-REST layer underneath it.

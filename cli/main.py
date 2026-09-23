#!/usr/bin/env python3
"""
Agents Hub CLI — drive the service from the terminal.

Commands call the service's own functions in this process: no server has to be
running, and nothing is serialized or sent anywhere. Set AGENTS_HUB_URL only if
the service lives somewhere this process cannot import — most often a container
— and the same commands go over its REST API instead (see cli/backend.py).

Usage:
    ah --help                           # after ./install.sh
    python -m cli --help                # …or straight from a checkout
    ah workspace init                   # register the current directory
    ah shell-init zsh >> ~/.zshrc       # the shell function, by hand
    ah agent list
    ah agent run swe_agent "fix the failing test"
    ah task create "Build a REST API"
"""
import os
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.text import Text

# The checkout that holds this package: <repo>/cli/main.py. Resolve the
# service's modules before anything below imports them, so the CLI runs from any
# working directory — and so `python cli/main.py` finds its own package too.
# Computed from __file__, not imported from common.paths: this file can be the
# very first thing executed (a direct `python cli/main.py`), before the repo
# root is on sys.path at all, so common.paths itself would not yet be
# importable. The formula matches common.paths.PROJECT_ROOT exactly, and once
# the path insert below runs, everything downstream imports that one instead.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.backend import get_backend, BackendError  # noqa: E402

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="agents-hub",
    help="Agents Hub CLI — manage agents, tasks, workspaces, and nodes.",
    no_args_is_help=True,
)

agent_app = typer.Typer(help="Agent management commands.", no_args_is_help=True)
task_app = typer.Typer(help="Task management commands.", no_args_is_help=True)
workspace_app = typer.Typer(help="Workspace management commands.", no_args_is_help=True)
node_app = typer.Typer(help="Node management commands.", no_args_is_help=True)
project_app = typer.Typer(help="Project management commands.", no_args_is_help=True)
server_app = typer.Typer(help="Backend server commands.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands: which backend is in use, moving state between backends.",
                     no_args_is_help=True)
auth_app = typer.Typer(help="Who this CLI is acting as, and personal API keys.",
                       no_args_is_help=True)
auth_keys_app = typer.Typer(help="Personal API keys: list, create, revoke (docs/api-keys.md).",
                            no_args_is_help=True)
auth_app.add_typer(auth_keys_app, name="keys")

app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(workspace_app, name="workspace")
app.add_typer(project_app, name="project")
app.add_typer(node_app, name="node")
app.add_typer(server_app, name="server")
app.add_typer(auth_app, name="auth")
app.add_typer(db_app, name="db")

console = Console()

# ---------------------------------------------------------------------------
# Selected workspace / project (the `conda activate` model)
# ---------------------------------------------------------------------------
# A CLI cannot change its parent shell's environment, so the stored selection
# below is a file, not an export. Two shells on one machine may well want to
# work in different workspaces, which AGENTS_HUB_WORKSPACE covers: the function
# `shell-init` prints exports it, the way `conda activate` does, and the working
# directory selects on its own below that.
#
# Precedence, narrowest first:
#   --workspace  >  AGENTS_HUB_WORKSPACE  >  the working directory  >  file.


def _state_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "agents-hub" / "cli.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except Exception:
        # No file yet, or someone hand-edited it into invalid JSON. Either way an
        # unusable selection must not stop the command that asked for it.
        return {}


def _write_state(**changes) -> dict:
    state = _read_state()
    for key, value in changes.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return state


# ---------------------------------------------------------------------------
# Where you are standing (the `direnv` half of the model)
# ---------------------------------------------------------------------------
# Standing inside an attached directory is itself a selection: a workspace that
# links to ~/code/myapp *is* ~/code/myapp, so a command run from there has an
# obvious workspace whether or not one was ever activated. Read from the state
# root's links rather than from the service, so it costs no call and works with
# either backend; it simply finds nothing when the paths on this machine are not
# the paths the service resolves (a containerized backend with its own mounts).


def _auto_enabled() -> bool:
    """Whether the working directory may select a workspace. On by default."""
    raw = os.environ.get("AGENTS_HUB_AUTO_WORKSPACE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


_detected: Optional[tuple] = None


def _detect_from_cwd() -> tuple[Optional[str], Optional[str]]:
    """The (workspace, project folder) whose directory contains the cwd.

    The most specific match wins, so a project inside a workspace beats the
    workspace itself. Either half may be None, and anything unexpected — no
    state root, an unreadable link, a dangling one — resolves to (None, None)
    rather than stopping the command that asked.
    """
    global _detected
    if _detected is not None:
        return _detected

    _detected = (None, None)
    if not _auto_enabled():
        return _detected

    try:
        from common.paths import WORKSPACES_ROOT

        cwd = Path.cwd().resolve()
        best_depth = -1
        for ws_link in sorted(WORKSPACES_ROOT.iterdir()):
            if ws_link.name.startswith("."):
                continue
            # The workspace itself, then each candidate project folder in it.
            candidates = [(ws_link, None)]
            try:
                candidates += [(c, c.name) for c in ws_link.iterdir()
                               if not c.name.startswith(".") and c.is_dir()]
            except OSError:
                pass

            for path, folder in candidates:
                try:
                    target = path.resolve()
                except OSError:
                    continue
                if not target.is_dir():
                    continue
                if cwd != target and target not in cwd.parents:
                    continue
                depth = len(target.parts)
                if depth > best_depth:
                    best_depth = depth
                    _detected = (ws_link.name, folder)
    except Exception:
        _detected = (None, None)
    return _detected


def _detected_project_id(workspace: str, folder: str) -> Optional[str]:
    """The id of the project this folder holds, asking the service once."""
    try:
        for pr in hub().list_projects():
            if (pr.get("workspace") or "") == workspace and (pr.get("folder") or "") == folder:
                return str(pr.get("id") or "") or None
    except Exception:
        # The directory is still a perfectly good workspace selection without it.
        pass
    return None


# ---------------------------------------------------------------------------
# The effective selection
# ---------------------------------------------------------------------------
# Precedence, narrowest first:
#   --workspace  >  AGENTS_HUB_WORKSPACE  >  the working directory  >  file


def _active_workspace(explicit: Optional[str] = None) -> Optional[str]:
    """The workspace a command should act on."""
    return (
        explicit
        or os.environ.get("AGENTS_HUB_WORKSPACE")
        or _detect_from_cwd()[0]
        or _read_state().get("workspace")
    )


def _active_project(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    from_env = os.environ.get("AGENTS_HUB_PROJECT")
    if from_env:
        return from_env

    workspace = _active_workspace()
    ws_detected, folder = _detect_from_cwd()
    # Only when the effective workspace is the one the directory found: a project
    # id belonging to somewhere else is worse than no project at all.
    if folder and ws_detected and ws_detected == workspace:
        found = _detected_project_id(ws_detected, folder)
        if found:
            return found

    state = _read_state()
    if state.get("project") and state.get("workspace") in (None, workspace):
        return state.get("project")
    return None


def _workspace_source(explicit: Optional[str] = None) -> str:
    """Where the effective workspace came from, for messages."""
    if explicit:
        return "--workspace"
    if os.environ.get("AGENTS_HUB_WORKSPACE"):
        return "AGENTS_HUB_WORKSPACE"
    if _detect_from_cwd()[0]:
        return f"the working directory ({Path.cwd()})"
    if _read_state().get("workspace"):
        return str(_state_file())
    return "nothing selected"


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------
# Built on first use, not at import: constructing it runs bootstrap (seeding
# agents and the database schema), which `--help` has no business doing.

_backend = None


def hub():
    """The service handle — direct calls, or HTTP when AGENTS_HUB_URL is set."""
    global _backend
    if _backend is None:
        try:
            _backend = get_backend()
        except Exception as e:
            console.print(
                f"[red]Could not open the service:[/red] {e}\n"
                "Direct mode needs this repository importable and its dependencies "
                "installed. To use a service running elsewhere instead, set "
                "[bold]AGENTS_HUB_URL[/bold]."
            )
            raise typer.Exit(1)
    return _backend


def call(fn, *args, **kwargs):
    """Run one backend operation, turning a refusal into a clean exit."""
    try:
        return fn(*args, **kwargs)
    except BackendError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Status colour helpers
# ---------------------------------------------------------------------------

_TASK_STATUS_COLORS = {
    "todo": "white",
    "ready": "cyan",
    "in_progress": "yellow",
    "blocked": "red",
    "stopped": "dim",
    "resolved": "green",
    "done": "green",
}

_NODE_STATUS_COLORS = {
    "running": "green",
    "stopped": "dim",
    "completed": "blue",
    "failed": "red",
    "error": "red",
}


def _task_color(status: str) -> str:
    return _TASK_STATUS_COLORS.get(status, "white")


def _node_color(status: str) -> str:
    return _NODE_STATUS_COLORS.get(status, "white")


def _short(val: str | None, n: int = 38) -> str:
    if not val:
        return ""
    return val if len(val) <= n else val[: n - 1] + "…"


# ---------------------------------------------------------------------------
# ID resolution
# ---------------------------------------------------------------------------
# The listings print an 8-character prefix because a full UUID crowds out the
# columns that matter, so every command that takes an ID has to accept that
# prefix back. The API only takes the full UUID (`task_id: UUID` on the route),
# so the expansion happens here.


def _expand_id(value: str, candidates: list[str], noun: str) -> str:
    if not value:
        console.print(f"[red]Missing {noun} ID.[/red]")
        raise typer.Exit(1)
    try:
        return str(uuid.UUID(value))
    except ValueError:
        pass

    matches = [c for c in candidates if c.startswith(value)]
    if not matches:
        console.print(f"[red]No {noun} found matching '{value}'.[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(
            f"[red]Ambiguous prefix '{value}' — {len(matches)} {noun}s match. "
            "Use more characters.[/red]"
        )
        raise typer.Exit(1)
    return matches[0]


def _resolve_task_id(task_id: str) -> str:
    """Full UUID for a task ID given in full or as the prefix `task list` shows."""
    tasks = call(hub().list_tasks)
    return _expand_id(task_id, [str(t.get("id", "")) for t in tasks], "task")


def _resolve_node_id(node_id: str) -> str:
    """Full UUID for a node ID given in full or as the prefix `node list` shows."""
    nodes = call(hub().list_nodes)
    return _expand_id(node_id, [str(n.get("node_id", "")) for n in nodes], "node")


# ---------------------------------------------------------------------------
# server commands
# ---------------------------------------------------------------------------


@server_app.command("start")
def server_start(
    host: str = typer.Option("0.0.0.0", help="Host to bind to."),
    port: int = typer.Option(8000, help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)."),
):
    """Start the backend API server."""
    backend_dir = os.path.join(str(PROJECT_ROOT), "dashboard", "backend")
    cmd = [
        sys.executable, "-m", "uvicorn", "main:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    console.print(f"[bold green]Starting server[/bold green] → http://{host}:{port}")
    subprocess.run(cmd, cwd=backend_dir)


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------


@app.command("worker")
def worker(
    concurrency: Optional[int] = typer.Option(None, help="Launches kept alive at once."),
    modes: Optional[str] = typer.Option(None, help="What this host can run: local, docker, or both (comma-separated)."),
    once: bool = typer.Option(False, "--once", help="One tick, then exit."),
):
    """Claim launches from the run queue and spawn them on this host.

    The other half of a backend running with AGENTS_HUB_ROLE=api (docs/workers.md).
    Needs the same database and the same .env as the backend; serves no HTTP."""
    _require_direct_mode("ah worker")
    from runtime.worker import main as _worker_main
    argv = []
    if concurrency is not None:
        argv += ["--concurrency", str(concurrency)]
    if modes:
        argv += ["--modes", modes]
    if once:
        argv.append("--once")
    raise typer.Exit(code=_worker_main(argv))


@app.command("deployment")
def deployment(
    logs: Optional[str] = typer.Option(None, "--logs", help="Print the last lines of this member's log instead of the map."),
    tail: int = typer.Option(200, help="Lines to print with --logs."),
):
    """The deployment map: members (replicas and workers), the roles each
    holds, the launch queue, and where runs, nodes and containers live
    (docs/deployment.md)."""
    _require_direct_mode("ah deployment")
    from dashboard.backend.routes.deployment import build_map
    from common import members as _members

    if logs:
        text = _members.read_log(logs, tail=tail)
        if text is None:
            console.print(f"[red]no log for member {logs}[/red]")
            raise typer.Exit(code=1)
        console.print(text, markup=False, highlight=False)
        return

    m = build_map()
    console.print(f"[bold]this process[/bold]: {m['self']['member_id']} ({m['self']['role']})")
    table = Table(box=box.SIMPLE, show_header=True, title="members")
    for col in ("member", "role", "host", "status", "beat", "uptime", "leases", "load", "version"):
        table.add_column(col)
    for mem in m["members"]:
        age = mem.get("heartbeat_age_seconds")
        up = mem.get("uptime_seconds")
        table.add_row(
            mem["member_id"], mem.get("role") or "", mem.get("host") or "",
            mem.get("status") or "", f"{int(age)}s ago" if age is not None else "",
            f"{int(up // 60)}m" if up is not None else "",
            ",".join(mem.get("leases") or []),
            ", ".join(f"{k}={v}" for k, v in (mem.get("load") or {}).items()),
            mem.get("version") or "")
    console.print(table)
    q = m.get("queue") or {}
    console.print(f"queue: queued {q.get('queued', 0)}, leased {q.get('leased', 0)}, running "
                  f"{q.get('running', 0)}, failed {q.get('failed', 0)}; outbox pending "
                  f"{(m.get('outbox') or {}).get('pending', 0)}")
    hosts = Table(box=box.SIMPLE, show_header=True, title="by host")
    for col in ("host", "members", "runs", "flow runs", "nodes", "containers"):
        hosts.add_column(col)
    for h in m["hosts"]:
        hosts.add_row(h["host"], str(len(h["members"])), str(h["runs"]), str(h["flow_runs"]),
                      str(h["nodes"]), str(h["containers"]))
    console.print(hosts)
    for run in m["runs"]:
        age = run.get("heartbeat_age_seconds")
        console.print(f"  run {str(run['run_id'])[:8]} {run.get('agent_id')} {run.get('status')} "
                      f"on {run.get('host') or '?'}"
                      + (f", beat {int(age)}s ago" if age is not None else ""))
    for loop in m["loops"]:
        console.print(f"  loop {str(loop['loop_run_id'])[:8]} iteration {loop.get('iterations_done')} "
                      f"on {loop.get('owner') or '?'}")


# ---------------------------------------------------------------------------
# db commands
# ---------------------------------------------------------------------------
# Direct mode only: these open the database this process is configured with
# (AGENTS_HUB_DATABASE_URL, or the SQLite file under AGENTS_HUB_ROOT), so a
# CLI pointed at a remote backend over AGENTS_HUB_URL has nothing to open.


def _require_direct_mode(what: str) -> None:
    if os.environ.get("AGENTS_HUB_URL"):
        console.print(f"[red]{what} works on the local database only[/red]: unset "
                      "AGENTS_HUB_URL and run it where the state lives.")
        raise typer.Exit(code=1)


@db_app.command("status")
def db_status():
    """Which backend is in use, its schema version and row counts."""
    _require_direct_mode("ah db status")
    from common import db_transfer
    info = db_transfer.status()
    console.print(f"[bold]{info['dialect']}[/bold]  {info['location']}")
    console.print(f"schema_version {info['schema_version']}, migrations {info['migrations']}")
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("table")
    table.add_column("rows", justify="right")
    for name, n in sorted(info["counts"].items()):
        table.add_row(name, str(n))
    console.print(table)


@db_app.command("migrate")
def db_migrate(
    to: str = typer.Option(..., "--to", help="Target: a postgresql:// URL, or a path to a SQLite file."),
    force: bool = typer.Option(False, "--force", help="Empty the target's tables first."),
    batch: int = typer.Option(500, help="Rows per INSERT batch."),
):
    """Copy the whole database into another backend, table by table, in one
    transaction, and compare the counts. Works in both directions: SQLite to
    Postgres and back. Files beside the database (run logs, workspaces, view
    assets) are not moved. Then point AGENTS_HUB_DATABASE_URL at the target
    and restart."""
    _require_direct_mode("ah db migrate")
    from common import db_transfer
    console.print(f"copying into [bold]{db_transfer.describe(to)}[/bold]")
    try:
        report = db_transfer.transfer(to, force=force, batch=batch,
                                      log=lambda line: console.print(f"  {line}"))
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not recover
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(code=1)
    total = sum(v["target"] for v in report["tables"].values())
    console.print(f"[green]done[/green]: {total} row(s) in {len(report['tables'])} table(s) "
                  f"now in {report['target']}")
    if report["dialect"] == "postgres":
        console.print("next: set AGENTS_HUB_DATABASE_URL to that URL in .env and restart every "
                      "process that touches state (backend, workers, the CLI).")
    else:
        console.print("next: clear AGENTS_HUB_DATABASE_URL in .env (and, if the file is not "
                      "the default one, point AGENTS_HUB_ROOT at its directory) and restart.")


@db_app.command("backup")
def db_backup_cmd(
    to: str = typer.Option(".", "--to", help="Archive path, or a directory to name a timestamped archive in."),
    no_files: bool = typer.Option(False, "--no-files", help="Database only: skip run logs, workspaces, and the rest."),
):
    """Write one archive with the database and, by default, the state
    directories beside it (run logs, workspaces, views, and the rest;
    docs/backup.md). Works whatever backend is configured: the archive
    always holds a plain SQLite file."""
    _require_direct_mode("ah db backup")
    from common import db_backup
    try:
        report = db_backup.backup(Path(to), include_files=not no_files,
                                  log=lambda line: console.print(f"  {line}"))
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not recover
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(code=1)
    manifest = report["manifest"]
    console.print(f"[green]done[/green]: [bold]{report['archive']}[/bold]")
    console.print(f"source: {manifest['source']['dialect']} at {manifest['source']['location']}, "
                  f"schema_version {manifest['schema_version']}")
    total = sum(manifest["counts"].values())
    console.print(f"{total} row(s) across {len(manifest['counts'])} table(s)")
    if manifest["files"]:
        console.print(f"files: {', '.join(manifest['files'])}")
    else:
        console.print("files: none (database only)")


@db_app.command("restore")
def db_restore_cmd(
    archive: str = typer.Argument(..., help="An archive written by `ah db backup`."),
    force: bool = typer.Option(False, "--force", help="Overwrite a configured database that already holds rows."),
    no_files: bool = typer.Option(False, "--no-files", help="Database only: leave run logs, workspaces, and the rest untouched."),
):
    """Load an archive into the database this process is configured with,
    and, by default, extract its files into AGENTS_HUB_ROOT (existing files
    overwritten, nothing else deleted). Refuses a database that already
    holds rows unless --force."""
    _require_direct_mode("ah db restore")
    from common import db_backup
    try:
        report = db_backup.restore(Path(archive), force=force, include_files=not no_files,
                                   log=lambda line: console.print(f"  {line}"))
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not recover
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(code=1)
    total = sum(v["target"] for v in report["tables"].values())
    console.print(f"[green]done[/green]: {total} row(s) in {len(report['tables'])} table(s), "
                  f"{report['files_restored']} file(s) restored")
    if report["mismatches"]:
        console.print(f"[red]row counts do not match the manifest:[/red] {report['mismatches']}")
        raise typer.Exit(code=1)
    console.print("counts match the archive's manifest")


@db_app.command("verify")
def db_verify_cmd(archive: str = typer.Argument(..., help="An archive written by `ah db backup`.")):
    """Check an archive without touching the live database: the manifest
    parses, database.sqlite opens, and its row counts match the manifest."""
    _require_direct_mode("ah db verify")
    from common import db_backup
    try:
        report = db_backup.verify(Path(archive))
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it does not recover
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(code=1)
    manifest = report["manifest"]
    console.print(f"created_at {manifest['created_at']}, "
                  f"source {manifest['source']['dialect']} at {manifest['source']['location']}")
    if report["ok"]:
        console.print(f"[green]ok[/green]: {sum(report['counts'].values())} row(s) match the manifest")
    else:
        console.print(f"[red]mismatch:[/red] {report['mismatches']}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# auth commands: whoami, personal API keys
# ---------------------------------------------------------------------------
# Over AGENTS_HUB_URL these speak to /api/auth/me and /api/auth/keys as
# whoever AGENTS_HUB_API_KEY (or a session) authenticates — see
# common.auth.auth_headers and docs/api-keys.md. In direct mode there is no
# request and so no named user: DirectBackend answers as the local operator
# for whoami, and keys need an explicit --user naming the account to act on,
# an administrator operation against the local database.


@auth_app.command("whoami")
def auth_whoami():
    """Who this CLI is acting as."""
    info = call(hub().whoami)
    lines = [f"[bold]id:[/bold] {info.get('id', '-')}"]
    if info.get("username"):
        lines.append(f"[bold]username:[/bold] {info['username']}")
    if info.get("role"):
        lines.append(f"[bold]role:[/bold] {info['role']}")
    if info.get("via"):
        lines.append(f"[bold]via:[/bold] {info['via']}")
    if info.get("mode"):
        lines.append(f"[bold]mode:[/bold] {info['mode']}")
    console.print(Panel("\n".join(lines), title="whoami", border_style="cyan"))


@auth_keys_app.command("list")
def auth_keys_list(
    user: Optional[str] = typer.Option(
        None, "--user", help="Direct mode only: manage this account's keys, as an admin."),
):
    """List personal API keys."""
    if hub().kind == "http" and user:
        console.print("[yellow]--user is ignored over AGENTS_HUB_URL[/yellow]: "
                      "a key acts as whoever AGENTS_HUB_API_KEY authenticates.")
    keys = call(hub().list_api_keys, user)

    table = Table(title="API keys", box=box.ROUNDED, show_lines=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Hint")
    table.add_column("Workspaces")
    table.add_column("Expires")
    table.add_column("Last used")

    for k in keys:
        workspaces = k.get("workspaces")
        table.add_row(
            k.get("id", ""),
            k.get("name", "") or "-",
            f"…{k.get('hint', '')}",
            ", ".join(workspaces) if workspaces else "full reach",
            k.get("expires_at") or "never",
            k.get("last_used_at") or "never",
        )

    console.print(table)
    console.print(f"[dim]{len(keys)} key(s)[/dim]")


@auth_keys_app.command("create")
def auth_keys_create(
    name: str = typer.Option("", "--name", help="A label for the key, e.g. 'laptop CLI'."),
    workspace: List[str] = typer.Option(
        [], "--workspace", "-w",
        help="Repeatable; narrows the key to these workspaces. Omit for the owner's full reach."),
    expires_days: Optional[int] = typer.Option(
        None, "--expires-days", help="Days until expiry. Omit for a key that never expires."),
    user: Optional[str] = typer.Option(
        None, "--user", help="Direct mode only: cut a key for this account, as an admin."),
):
    """Cut a new personal API key. Shown once: store it now."""
    if hub().kind == "http" and user:
        console.print("[yellow]--user is ignored over AGENTS_HUB_URL[/yellow]: "
                      "a key acts as whoever AGENTS_HUB_API_KEY authenticates.")
    workspaces = list(workspace) or None
    record = call(hub().create_api_key, user, name, workspaces, expires_days)

    console.print(Panel(
        f"[bold]{record['key']}[/bold]\n\n"
        "Shown once. Store it now; it cannot be shown again.",
        title="New API key", border_style="green",
    ))
    scope = ", ".join(record.get("workspaces") or []) or "full reach"
    console.print(f"id {record.get('id')}, name {record.get('name') or '-'}, scope {scope}, "
                  f"expires {record.get('expires_at') or 'never'}")


@auth_keys_app.command("revoke")
def auth_keys_revoke(
    key_id: str = typer.Argument(..., help="The key's id, from `ah auth keys list`."),
    user: Optional[str] = typer.Option(
        None, "--user", help="Direct mode only: revoke this account's key, as an admin."),
):
    """Revoke a personal API key. Immediate: anything using it stops working at once."""
    if hub().kind == "http" and user:
        console.print("[yellow]--user is ignored over AGENTS_HUB_URL[/yellow]: "
                      "a key acts as whoever AGENTS_HUB_API_KEY authenticates.")
    call(hub().revoke_api_key, key_id, user)
    console.print(f"[green]revoked[/green] {key_id}")


# ---------------------------------------------------------------------------
# top-level up command
# ---------------------------------------------------------------------------
# The whole dashboard is two processes in two directories. Running them by hand
# means two terminals and a `cd` each; this is the same thing with neither.


def _dist_is_stale(frontend_dir: Path) -> bool:
    """Whether the built bundle is missing or older than what it was built from.

    Cheap and conservative: a rebuild that was not needed costs seconds, serving
    a stale bundle costs an afternoon of wondering why an edit did nothing.
    """
    index = frontend_dir / "dist" / "index.html"
    if not index.exists():
        return True
    built_at = index.stat().st_mtime

    sources = [frontend_dir / "package.json", frontend_dir / "vite.config.js", frontend_dir / "index.html"]
    for folder in (frontend_dir / "src", frontend_dir / "scripts", frontend_dir / "public"):
        if folder.is_dir():
            sources += [p for p in folder.rglob("*") if p.is_file()]
    return any(p.stat().st_mtime > built_at for p in sources if p.exists())


def _stop(procs: list) -> None:
    """Ask both children to stop, then insist."""
    for name, proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for name, proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            console.print(f"[yellow]{name} did not stop, killing it.[/yellow]")
            proc.kill()


@app.command("up")
def up(
    host: str = typer.Option("127.0.0.1", "--host", help="Host both servers bind to."),
    backend_port: int = typer.Option(8000, "--backend-port", help="Port for the API."),
    frontend_port: int = typer.Option(5173, "--frontend-port", help="Port for the dashboard."),
    dev: bool = typer.Option(False, "--dev", help="Development servers: backend reload, dashboard with HMR."),
    frontend: bool = typer.Option(True, "--frontend/--no-frontend", help="Also run the dashboard."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild the dashboard bundle even if it looks current."),
    install_deps: bool = typer.Option(True, "--install-deps/--no-install-deps",
                                      help="Run npm install when the dashboard has no node_modules."),
):
    """Run the backend and the dashboard together, from wherever you are.

    Production by default: the backend runs without a reloader, and the dashboard
    is the built bundle (rebuilt when its sources are newer). `--dev` swaps both
    for their development servers, with reload and HMR.

    Output from both is interleaved on this terminal, and Ctrl-C stops both. If
    one exits on its own the other is stopped too, so a failed start never
    leaves half a stack running.
    """
    import shutil
    import signal

    root = PROJECT_ROOT
    backend_dir = root / "dashboard" / "backend"
    frontend_dir = root / "dashboard" / "frontend"

    # Ctrl-C arrives as SIGINT and is handled below; `kill` sends SIGTERM, which
    # would otherwise end this process and orphan both servers. Route it into the
    # same shutdown.
    def _terminated(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminated)

    procs: list = []
    backend_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", host, "--port", str(backend_port)]
    if dev:
        backend_cmd.append("--reload")
    # No `--workers` counterpart in production: the backend starts singletons of
    # its own (the plan scheduler, the run watchdog, the Telegram poller), and a
    # second worker would run a second copy of each. Scaling out is the compose
    # prod profile's job, where replicas sit behind nginx.

    want_frontend = frontend
    if want_frontend and not shutil.which("npm"):
        console.print("[yellow]No npm on PATH, so the dashboard is skipped.[/yellow] "
                      "Install Node 22+, or pass [bold]--no-frontend[/bold] to stop being told.")
        want_frontend = False

    if want_frontend and not (frontend_dir / "node_modules").exists():
        if not install_deps:
            console.print("[red]The dashboard has no node_modules[/red] and --no-install-deps was given.")
            raise typer.Exit(1)
        console.print("[bold]Installing dashboard dependencies[/bold] (first run only)…")
        if subprocess.run(["npm", "install"], cwd=frontend_dir).returncode != 0:
            console.print("[red]npm install failed.[/red] The backend can still run with --no-frontend.")
            raise typer.Exit(1)

    # The bundle is only built for a production run; the dev server compiles on
    # demand and would ignore it.
    if want_frontend and not dev and (rebuild or _dist_is_stale(frontend_dir)):
        console.print("[bold]Building the dashboard[/bold] "
                      f"({'asked for' if rebuild else 'its sources changed'})…")
        if subprocess.run(["npm", "run", "build"], cwd=frontend_dir).returncode != 0:
            console.print("[red]The build failed.[/red] Run with [bold]--dev[/bold] to use the dev server, "
                          "or [bold]--no-frontend[/bold] for the API alone.")
            raise typer.Exit(1)

    mode = "development" if dev else "production"
    lines = [f"[bold]API:[/bold]       http://{host}:{backend_port}"]
    if want_frontend:
        lines.append(f"[bold]Dashboard:[/bold] http://{host}:{frontend_port}")
    lines.append(f"[dim]Mode: {mode}. Ctrl-C stops everything.[/dim]")
    console.print(Panel("\n".join(lines), title="Agents Hub", border_style="green"))

    try:
        procs.append(("backend", subprocess.Popen(backend_cmd, cwd=backend_dir)))
        if want_frontend:
            env = dict(os.environ)
            # Same-origin /api in the browser: the dashboard forwards it to the
            # backend, wherever this run happens to have put it. Both the dev
            # server and `vite preview` read this (see vite.config.js).
            env["VITE_API_PROXY"] = f"http://localhost:{backend_port}"
            npm_cmd = ["npm", "run", "dev" if dev else "preview", "--", "--port", str(frontend_port)]
            if host not in ("127.0.0.1", "localhost"):
                npm_cmd.append("--host")
            procs.append(("dashboard", subprocess.Popen(npm_cmd, cwd=frontend_dir, env=env)))

        # Whichever stops first takes the other with it: half a stack is worse
        # than none, because the half that is up looks like it works.
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    console.print(f"[yellow]The {name} exited[/yellow] (status {code}), stopping the rest.")
                    _stop(procs)
                    raise typer.Exit(code or 0)
            try:
                # Sleep, but wake the moment the backend does anything.
                procs[0][1].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping…[/dim]")
        _stop(procs)


# ---------------------------------------------------------------------------
# agent commands
# ---------------------------------------------------------------------------


@agent_app.command("list")
def agent_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
):
    """List all available agents."""
    workspace = _active_workspace(workspace)
    agents = call(hub().list_agents, workspace)

    table = Table(title="Agents", box=box.ROUNDED, show_lines=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Type")

    for a in agents:
        table.add_row(
            a.get("id", ""),
            a.get("name", ""),
            a.get("domain", ""),
            a.get("type", ""),
        )

    console.print(table)
    console.print(f"[dim]{len(agents)} agent(s)[/dim]")


@agent_app.command("get")
def agent_get(agent_id: str = typer.Argument(..., help="Agent ID.")):
    """Show details for a specific agent."""
    a = call(hub().get_agent, agent_id)

    console.print(Panel(
        "\n".join([
            f"[bold]ID:[/bold]          {a.get('id')}",
            f"[bold]Name:[/bold]        {a.get('name')}",
            f"[bold]Domain:[/bold]      {a.get('domain', '-')}",
            f"[bold]Type:[/bold]        {a.get('type', '-')}",
            f"[bold]Description:[/bold] {a.get('description', '-')}",
            f"[bold]Capacity:[/bold]    {a.get('capacity', '-')}",
            f"[bold]Memory:[/bold]      {a.get('memory_type', '-')}",
            f"[bold]Tools:[/bold]       {', '.join(a.get('tools') or []) or '-'}",
        ]),
        title=f"Agent — {a.get('id')}",
        border_style="cyan",
    ))


@agent_app.command("run")
def agent_run(
    agent_id: str = typer.Argument(..., help="Agent ID."),
    instruction: str = typer.Argument(..., help="Instruction / prompt for the agent."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """Run an agent with a one-shot instruction."""
    # No node is started: /api/chat/message builds the agent and runs it inside
    # the server process. A node would be a second, idle copy of the agent.
    workspace = _active_workspace(workspace)
    body: dict = {
        "agent_id": agent_id,
        "message": instruction,
        "history": [],
        # Recorded as the run's message_origin, so these show up as CLI runs
        # rather than web-chat runs in Sessions and Messages.
        "source": "cli",
    }
    if workspace:
        body["workspace"] = workspace
    project = _active_project()
    if project:
        body["project_id"] = project

    console.print(f"[bold]Running agent:[/bold] {agent_id}")
    console.rule()
    result = call(hub().send_message, body)
    console.print(result.get("response", result))


# ---------------------------------------------------------------------------
# task commands
# ---------------------------------------------------------------------------


@task_app.command("list")
def task_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """List all tasks."""
    workspace = _active_workspace(workspace)
    params = {}
    if workspace:
        params["workspace"] = workspace
    tasks = call(hub().list_tasks, workspace)

    if status:
        tasks = [t for t in tasks if t.get("status") == status]

    table = Table(title="Tasks", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Title")
    table.add_column("Status", no_wrap=True)
    table.add_column("Agent")
    table.add_column("Workspace")

    for t in tasks:
        tid = str(t.get("id", ""))[:8]
        s = t.get("status", "")
        color = _task_color(s)
        table.add_row(
            tid,
            _short(t.get("title"), 45),
            Text(s, style=color),
            t.get("assigned_agent") or "",
            t.get("workspace") or "",
        )

    console.print(table)
    console.print(f"[dim]{len(tasks)} task(s)[/dim]")


@task_app.command("get")
def task_get(task_id: str = typer.Argument(..., help="Task ID (full or prefix).")):
    """Show full details of a task including subtasks."""
    t = call(hub().get_task, _resolve_task_id(task_id))

    subtasks = t.get("subtasks", [])
    lines = [
        f"[bold]ID:[/bold]          {t.get('id')}",
        f"[bold]Title:[/bold]       {t.get('title')}",
        f"[bold]Status:[/bold]      [{_task_color(t.get('status', ''))}]{t.get('status')}[/{_task_color(t.get('status', ''))}]",
        f"[bold]Agent:[/bold]       {t.get('assigned_agent') or '-'}",
        f"[bold]Workspace:[/bold]   {t.get('workspace') or '-'}",
        f"[bold]Priority:[/bold]    {t.get('priority') or '-'}",
        f"[bold]Description:[/bold] {t.get('description') or '-'}",
    ]
    if subtasks:
        lines.append(f"[bold]Subtasks:[/bold]    {len(subtasks)}")
        for st in subtasks:
            sc = _task_color(st.get("status", ""))
            lines.append(f"  • [{sc}]{st.get('status')}[/{sc}]  {_short(st.get('title'), 50)}")

    console.print(Panel("\n".join(lines), title=f"Task — {str(t.get('id', ''))[:8]}", border_style="blue"))


@task_app.command("create")
def task_create(
    title: str = typer.Argument(..., help="Task title."),
    description: Optional[str] = typer.Option(None, "--desc", "-d", help="Task description."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    decompose: bool = typer.Option(False, "--decompose", help="Auto-decompose into subtasks."),
):
    """Create a new task."""
    workspace = _active_workspace(workspace)
    body: dict = {"title": title}
    if description:
        body["description"] = description
    if workspace:
        body["workspace_name"] = workspace
    project = _active_project()
    if project:
        body["project_id"] = project
    if decompose:
        body["should_decompose"] = True

    t = call(hub().create_task, body)
    console.print(f"[green]Task created:[/green] [bold]{str(t.get('id', ''))[:8]}[/bold]  {t.get('title')}")


@task_app.command("assign")
def task_assign(
    task_id: str = typer.Argument(..., help="Task ID (full or prefix)."),
    agent_id: str = typer.Argument(..., help="Agent ID to assign."),
):
    """Assign an agent to a task and start execution.

    Whether the run needs approval first is the workspace's assignment_mode, not
    a per-call choice; this always assigns directly.
    """
    task_id = _resolve_task_id(task_id)
    result = call(hub().assign_task, task_id, agent_id)
    console.print(f"[green]Assigned[/green] agent [bold]{agent_id}[/bold] to task [bold]{task_id[:8]}[/bold]")
    if result.get("run_id"):
        console.print(f"[dim]Run ID: {result['run_id']}[/dim]")


@task_app.command("decompose")
def task_decompose(
    task_id: str = typer.Argument(..., help="Task ID to decompose (full or prefix)."),
):
    """Break a task down into subtasks using the decomposer agent."""
    task_id = _resolve_task_id(task_id)
    result = call(hub().decompose_task, task_id)
    subtasks = result.get("subtasks", [])
    console.print(f"[green]Decomposed into {len(subtasks)} subtask(s):[/green]")
    for st in subtasks:
        console.print(f"  • {_short(st.get('title', ''), 60)}")


@task_app.command("stop")
def task_stop(task_id: str = typer.Argument(..., help="Task ID to stop (full or prefix).")):
    """Stop a running task."""
    task_id = _resolve_task_id(task_id)
    call(hub().stop_task, task_id)
    console.print(f"[yellow]Stopped[/yellow] task [bold]{task_id[:8]}[/bold]")


@task_app.command("delete")
def task_delete(
    task_id: str = typer.Argument(..., help="Task ID to delete (full or prefix)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Delete a task."""
    task_id = _resolve_task_id(task_id)
    if not yes:
        typer.confirm(f"Delete task {task_id[:8]}?", abort=True)
    call(hub().delete_task, task_id)
    console.print(f"[red]Deleted[/red] task [bold]{task_id[:8]}[/bold]")


# ---------------------------------------------------------------------------
# workspace commands
# ---------------------------------------------------------------------------


@workspace_app.command("list")
def workspace_list():
    """List all workspaces."""
    workspaces = call(hub().list_workspaces)
    active = _active_workspace()

    table = Table(title="Workspaces", box=box.ROUNDED)
    table.add_column("", no_wrap=True)           # active marker
    table.add_column("Name", style="bold cyan")
    table.add_column("Tasks", justify="right")
    table.add_column("Location")

    for ws in workspaces:
        name = ws.get("name", "")
        # An attached workspace is a link, so show where the real files are;
        # a plain one lives under the state root and its path says nothing.
        location = f"[dim]→[/dim] {ws['target']}" if ws.get("attached") else "[dim]managed[/dim]"
        table.add_row(
            "[green]*[/green]" if name == active else "",
            name,
            str(ws.get("tasks_count", 0)),
            location,
        )

    console.print(table)
    console.print(f"[dim]{len(workspaces)} workspace(s)[/dim]")
    if active:
        console.print(f"[dim]* selected — from {_workspace_source()}[/dim]")


@workspace_app.command("create")
def workspace_create(
    name: str = typer.Argument(..., help="Workspace name."),
    use: bool = typer.Option(False, "--use", help="Select it for subsequent commands."),
):
    """Create a new workspace under the service's own state directory."""
    result = call(hub().create_workspace, name)
    created = result.get("name", name)
    console.print(f"[green]Workspace created:[/green] [bold]{created}[/bold]")
    if use:
        _write_state(workspace=created, project=None)
        console.print(f"[green]Selected[/green] workspace [bold]{created}[/bold]")


@workspace_app.command("init")
def workspace_init(
    path: Optional[str] = typer.Argument(None, help="Directory to register. Defaults to the current one."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Assert the resulting name (must match the folder)."),
    use: bool = typer.Option(True, "--use/--no-use", help="Select it for subsequent commands."),
):
    """Register an existing directory as a workspace, in place.

    Nothing is copied: the workspace becomes a link to this directory, so agents
    read and write the real files and a git checkout keeps its history.

    The path is resolved by the *backend*. With a local backend that is just this
    machine; if the backend runs in a container, pass a path inside the container
    and bind mount the directory there first.
    """
    target = str(Path(path or os.getcwd()).expanduser().resolve())
    body: dict = {"path": target}
    if name:
        body["name"] = name
    result = call(hub().attach_workspace, body["path"], body.get("name"))
    ws = result.get("name", "")

    console.print(
        f"[green]Workspace[/green] [bold]{ws}[/bold] [green]now points at[/green] {result.get('target', target)}"
    )
    console.print("[dim]Nothing was copied. Detaching later removes only the link.[/dim]")
    if use:
        _write_state(workspace=ws, project=None)
        console.print(f"[green]Selected[/green] workspace [bold]{ws}[/bold]")


@workspace_app.command("use")
def workspace_use(
    name: str = typer.Argument(..., help="Workspace to select."),
):
    """Select a workspace, so later commands act on it without -w."""
    workspaces = call(hub().list_workspaces)
    names = [w.get("name") for w in workspaces]
    if name not in names:
        console.print(f"[red]No workspace '{name}'.[/red] Known: {', '.join(n for n in names if n)}")
        raise typer.Exit(1)
    _write_state(workspace=name, project=None)
    console.print(f"[green]Selected[/green] workspace [bold]{name}[/bold]")
    console.print(f"[dim]Stored in {_state_file()} — one selection per machine. "
                  "For a per-shell one, source [bold]shell-init[/bold] and use the "
                  "function it prints.[/dim]")


@workspace_app.command("current")
def workspace_current():
    """Show the selected workspace, where it came from, and where it points."""
    active = _active_workspace()
    if not active:
        console.print("[yellow]No workspace selected.[/yellow] Pick one with "
                      "[bold]workspace use <name>[/bold], or register a folder with "
                      "[bold]workspace init[/bold].")
        return

    lines = [f"[bold]Workspace:[/bold] {active}", f"[bold]Source:[/bold]    {_workspace_source()}"]
    try:
        match = next((w for w in call(hub().list_workspaces) if w.get("name") == active), None)
    except typer.Exit:
        match = None
    if match is None:
        lines.append("[red]Not present on the backend[/red] — it may have been deleted.")
    elif match.get("attached"):
        lines.append(f"[bold]Points at:[/bold] {match.get('target')}")
    else:
        lines.append("[bold]Points at:[/bold] the service's own state directory")

    project = _active_project()
    if project:
        lines.append(f"[bold]Project:[/bold]   {project}")

    console.print(Panel("\n".join(lines), title="Current", border_style="cyan"))


@workspace_app.command("unuse")
def workspace_unuse():
    """Clear the selected workspace."""
    _write_state(workspace=None, project=None)
    console.print("[green]Cleared[/green] the workspace selection.")
    detected, _ = _detect_from_cwd()
    if detected:
        console.print(f"[dim]This directory still selects [bold]{detected}[/bold] on its own.[/dim]")


@workspace_app.command("detach")
def workspace_detach(
    name: str = typer.Argument(..., help="Attached workspace to unregister."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Unregister an attached workspace. The directory itself is left alone."""
    match = next((w for w in call(hub().list_workspaces) if w.get("name") == name), None)
    if match is None:
        console.print(f"[red]No workspace '{name}'.[/red]")
        raise typer.Exit(1)
    if not match.get("attached"):
        console.print(
            f"[red]'{name}' is not attached[/red] — it is a managed workspace, and detaching "
            "does not apply. Deleting it would erase its contents, which this command will "
            "not do; use the dashboard if that is what you want."
        )
        raise typer.Exit(1)

    target = match.get("target")
    if not yes:
        typer.confirm(f"Detach '{name}'? {target} stays exactly as it is.", abort=True)
    result = call(hub().delete_workspace, name) or {}
    console.print(f"[green]Detached[/green] [bold]{name}[/bold]")
    console.print(f"[dim]Left untouched: {result.get('target_kept') or target}[/dim]")
    if _read_state().get("workspace") == name:
        _write_state(workspace=None, project=None)
        console.print("[dim]It was the selected workspace, so the selection was cleared.[/dim]")


# ---------------------------------------------------------------------------
# project commands
# ---------------------------------------------------------------------------


def _require_workspace(explicit: Optional[str]) -> str:
    ws = _active_workspace(explicit)
    if not ws:
        console.print(
            "[red]No workspace.[/red] Select one with [bold]workspace use <name>[/bold], "
            "register a folder with [bold]workspace init[/bold], or pass [bold]-w[/bold]."
        )
        raise typer.Exit(1)
    return ws


@project_app.command("list")
def project_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    all_workspaces: bool = typer.Option(False, "--all", "-a", help="List projects in every workspace."),
):
    """List projects in the selected workspace."""
    projects = call(hub().list_projects)
    ws = None if all_workspaces else _active_workspace(workspace)
    if ws:
        projects = [pr for pr in projects if (pr.get("workspace") or "") == ws]

    active = _active_project()
    table = Table(title="Projects", box=box.ROUNDED)
    table.add_column("", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Workspace")
    table.add_column("Tasks", justify="right")
    table.add_column("Repo")

    for pr in projects:
        repo = pr.get("repo") or {}
        repo_type = repo.get("type") or "none"
        pid = str(pr.get("id", ""))
        table.add_row(
            "[green]*[/green]" if pid == active else "",
            pid[:8],
            _short(pr.get("name"), 30),
            pr.get("workspace") or "",
            str(pr.get("tasks_count", 0)),
            "[dim]-[/dim]" if repo_type == "none" else repo_type,
        )

    console.print(table)
    scope = "all workspaces" if not ws else f"workspace '{ws}'"
    console.print(f"[dim]{len(projects)} project(s) in {scope}[/dim]")


@project_app.command("add")
def project_add(
    path: Optional[str] = typer.Argument(None, help="Directory to register. Defaults to the current one."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name. Defaults to the folder's name."),
    description: Optional[str] = typer.Option(None, "--desc", "-d"),
    use: bool = typer.Option(True, "--use/--no-use", help="Select it for subsequent commands."),
):
    """Register an existing directory as a project in a workspace, in place.

    Nothing is copied. If the directory is a git repo, it is recorded as one and
    `git status` / pull work on it immediately — no clone step.
    """
    ws = _require_workspace(workspace)
    target = str(Path(path or os.getcwd()).expanduser().resolve())
    body: dict = {"workspace": ws, "path": target}
    if name:
        body["name"] = name
    if description:
        body["description"] = description

    result = call(hub().attach_project, body)
    pid = str(result.get("id", ""))
    console.print(
        f"[green]Project[/green] [bold]{result.get('name')}[/bold] "
        f"[green]added to workspace[/green] [bold]{ws}[/bold]"
    )
    console.print(f"[dim]Points at {result.get('target', target)} — nothing was copied.[/dim]")
    if result.get("is_git_repo"):
        console.print("[dim]It is a git repo, so its branch and history came along.[/dim]")
    else:
        console.print("[dim]No .git found, so it is registered without a repo.[/dim]")
    if use and pid:
        _write_state(project=pid)
        console.print(f"[green]Selected[/green] project [bold]{pid[:8]}[/bold]")


@project_app.command("get")
def project_get(
    project_id: Optional[str] = typer.Argument(None, help="Project ID (full or prefix). Defaults to the selected one."),
):
    """Show a project, including git state when it has a repo."""
    pid = _active_project(project_id)
    if not pid:
        console.print("[red]No project.[/red] Pass an ID or select one with [bold]project use[/bold].")
        raise typer.Exit(1)

    projects = call(hub().list_projects)
    pid = _expand_id(pid, [str(x.get("id", "")) for x in projects], "project")
    pr = next(x for x in projects if str(x.get("id")) == pid)
    repo = pr.get("repo") or {}

    lines = [
        f"[bold]ID:[/bold]        {pr.get('id')}",
        f"[bold]Name:[/bold]      {pr.get('name')}",
        f"[bold]Workspace:[/bold] {pr.get('workspace')}",
        f"[bold]Type:[/bold]      {pr.get('type') or '-'}",
        f"[bold]Folder:[/bold]    {pr.get('folder') or '-'}",
        f"[bold]Repo:[/bold]      {repo.get('type') or 'none'}"
        + (f" ({repo.get('local_path')})" if repo.get("local_path") else ""),
    ]
    if pr.get("description"):
        lines.append(f"[bold]About:[/bold]     {_short(pr.get('description'), 60)}")

    if (repo.get("type") or "none") != "none":
        try:
            git = hub().project_git_status(pid)
            lines.append(f"[bold]Branch:[/bold]    {git.get('branch') or '-'}")
            status = (git.get("status") or "").strip()
            lines.append(f"[bold]Changes:[/bold]   {len(status.splitlines()) if status else 0} file(s)")
            for commit in (git.get("recent_commits") or "").splitlines()[:3]:
                lines.append(f"  [dim]{commit}[/dim]")
        except typer.Exit:
            # A project can legitimately point at a folder that is not a repo (or
            # not one any more); the rest of the panel is still worth printing.
            lines.append("[dim]Git state unavailable[/dim]")

    console.print(Panel("\n".join(lines), title=f"Project — {pid[:8]}", border_style="magenta"))


@project_app.command("use")
def project_use(
    project_id: str = typer.Argument(..., help="Project ID (full or prefix)."),
):
    """Select a project, so later commands act inside it."""
    projects = call(hub().list_projects)
    pid = _expand_id(project_id, [str(x.get("id", "")) for x in projects], "project")
    pr = next(x for x in projects if str(x.get("id")) == pid)
    _write_state(workspace=pr.get("workspace"), project=pid)
    console.print(
        f"[green]Selected[/green] project [bold]{pr.get('name')}[/bold] "
        f"in workspace [bold]{pr.get('workspace')}[/bold]"
    )


@project_app.command("unuse")
def project_unuse():
    """Clear the selected project, keeping the workspace."""
    _write_state(project=None)
    console.print("[green]Cleared[/green] the project selection.")


# ---------------------------------------------------------------------------
# node commands
# ---------------------------------------------------------------------------


@node_app.command("list")
def node_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List all running nodes."""
    workspace = _active_workspace(workspace)
    nodes = call(hub().list_nodes, workspace)

    table = Table(title="Nodes", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Agent", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Workspace")
    table.add_column("Started")

    for n in nodes:
        nid = str(n.get("node_id", ""))[:8]
        s = n.get("status", "")
        color = _node_color(s)
        started = (n.get("started_at") or "")[:16].replace("T", " ")
        table.add_row(
            nid,
            n.get("agent_name") or n.get("agent_id", ""),
            Text(s, style=color),
            n.get("workspace") or "",
            started,
        )

    console.print(table)
    console.print(f"[dim]{len(nodes)} node(s)[/dim]")


@node_app.command("start")
def node_start(
    agent_id: str = typer.Argument(..., help="Agent ID to start a node for."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    label: Optional[str] = typer.Option(None, "--label", "-l"),
):
    """Start a persistent node for an agent."""
    workspace = _active_workspace(workspace)
    body: dict = {"agent_id": agent_id}
    if workspace:
        body["workspace"] = workspace
    if label:
        body["label"] = label
    node = call(hub().start_node, body)
    console.print(f"[green]Node started:[/green] [bold]{str(node.get('node_id', ''))[:8]}[/bold]  agent={agent_id}")


@node_app.command("stop")
def node_stop(
    node_id: str = typer.Argument(..., help="Node ID to stop (full or prefix)."),
):
    """Stop a running node."""
    node_id = _resolve_node_id(node_id)
    call(hub().stop_node, node_id)
    console.print(f"[yellow]Stopped[/yellow] node [bold]{node_id[:8]}[/bold]")


# ---------------------------------------------------------------------------
# top-level chat command
# ---------------------------------------------------------------------------


@app.command("chat")
def chat(
    agent_id: str = typer.Argument(..., help="Agent ID to chat with."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    project_id: Optional[str] = typer.Option(None, "--project", "-p"),
):
    """Start an interactive chat session with an agent (REPL mode)."""
    # Each turn is one /api/chat/message call, which runs the agent in the
    # server process — no node to start, and history is carried in the body.
    workspace = _active_workspace(workspace)
    project_id = _active_project(project_id)
    console.print(Panel(
        f"Chatting with [bold cyan]{agent_id}[/bold cyan]\n"
        f"[dim]Workspace: {workspace or 'default'}[/dim]\n\n"
        "Type your message and press Enter. Type [bold]/exit[/bold] or Ctrl-C to quit.",
        border_style="cyan",
        title="Chat",
    ))

    history: list[dict] = []

    while True:
        try:
            user_input = typer.prompt("\nYou", prompt_suffix=" > ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting chat.[/dim]")
            break

        if user_input.strip().lower() in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]Exiting chat.[/dim]")
            break

        if not user_input.strip():
            continue

        body: dict = {
            "agent_id": agent_id,
            "message": user_input,
            "history": history,
            "source": "cli",
        }
        if workspace:
            body["workspace"] = workspace
        if project_id:
            body["project_id"] = project_id

        try:
            result = hub().send_message(body)
        except BackendError as e:
            console.print(f"[red]Error:[/red] {e}")
            # One bad turn should not end the session — the next message may
            # well go through.
            continue

        response = result.get("response", "")
        console.print(f"\n[bold cyan]{agent_id}[/bold cyan] > {response}")

        # Accumulate history for multi-turn context
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": response})


# ---------------------------------------------------------------------------
# Shell integration (the `conda activate` half of the model)
# ---------------------------------------------------------------------------
# A process cannot change its parent shell's environment, which is why `conda
# activate` is a shell *function* rather than a program. Same here: `shell-init`
# prints a function to source, and that function re-exports the selection after
# any command that changed it. The exports then win over the working directory,
# so an explicit activation beats wherever you happen to be standing.

_SHELLS = ("zsh", "bash", "fish")

# Only the commands that write the selection resync the shell. Widening this to
# every command would let a stale file overwrite a variable exported by hand.
_SELECTION_COMMANDS = (
    "workspace:use", "workspace:unuse", "workspace:init", "workspace:create",
    "workspace:detach", "project:use", "project:unuse", "project:add",
)


_BEGIN_MARK = "# >>> agents hub >>>"
_END_MARK = "# <<< agents hub <<<"

# What `pip install -e .` puts on PATH, best first (see pyproject.toml). The
# shell function shadows `ah`, which is deliberate and not recursive: it calls
# the script through `command`, and by absolute path at that.
_CONSOLE_SCRIPTS = ("ah", "agents-hub")


def _guess_shell() -> str:
    name = Path(os.environ.get("SHELL", "")).name
    return name if name in _SHELLS else "zsh"


def _sh_quote(value: str) -> str:
    """Single-quote for POSIX shells and for fish, which quote alike here."""
    return "'" + value.replace("'", "'\\''") + "'"


def _launcher() -> str:
    """How the generated function should invoke this CLI, already quoted.

    The console script when `pip install -e .` put one on PATH, and this file
    under the interpreter running it otherwise — so the integration works either
    way, and an installed hub keeps working when the shell's PATH is rearranged.
    """
    import shutil

    # Whatever launched this process, when that was the installed command: the
    # console script of the environment in use, even one not on PATH right now.
    argv0 = Path(sys.argv[0] or "")
    if argv0.name in _CONSOLE_SCRIPTS and argv0.exists():
        return _sh_quote(str(argv0.resolve()))
    for candidate in _CONSOLE_SCRIPTS:
        script = shutil.which(candidate)
        if script:
            return _sh_quote(script)
    return f"{_sh_quote(sys.executable or 'python3')} {_sh_quote(str(Path(__file__).resolve()))}"


def _rc_file(shell: str) -> Path:
    """The startup file a shell reads, where the integration block belongs."""
    home = Path.home()
    if shell == "fish":
        base = os.environ.get("XDG_CONFIG_HOME") or (home / ".config")
        return Path(base) / "fish" / "config.fish"
    if shell == "bash":
        # Login shells on macOS read .bash_profile and never .bashrc, so follow
        # whichever this machine actually has rather than assuming Linux.
        profile = home / ".bash_profile"
        if profile.exists() and not (home / ".bashrc").exists():
            return profile
        return home / ".bashrc"
    return home / ".zshrc"


def _write_block(rc: Path, block: Optional[str]) -> str:
    """Put ``block`` between the markers in ``rc``, or remove it when None.

    Returns what happened: "installed", "updated", "removed" or "absent". The
    markers make this idempotent, so re-running after an upgrade rewrites the
    same region instead of stacking another copy.
    """
    existing = rc.read_text() if rc.exists() else ""
    lines = existing.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == _BEGIN_MARK), None)
    end = next((i for i, line in enumerate(lines) if line.strip() == _END_MARK), None)
    had_block = start is not None and end is not None and end > start

    if block is None:
        if not had_block:
            return "absent"
        remaining = lines[:start] + lines[end + 1:]
        rc.write_text("\n".join(remaining).rstrip("\n") + ("\n" if remaining else ""))
        return "removed"

    wrapped = [_BEGIN_MARK, *block.splitlines(), _END_MARK]
    if had_block:
        lines[start:end + 1] = wrapped
        outcome = "updated"
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += wrapped
        outcome = "installed"

    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text("\n".join(lines).rstrip("\n") + "\n")
    return outcome


@app.command("shell-export")
def shell_export(
    shell: Optional[str] = typer.Argument(None, help=f"One of {', '.join(_SHELLS)}. Defaults to $SHELL."),
):
    """Print the stored selection as shell assignments, for a wrapper to eval.

    Deliberately reads the state file only: this is what publishes a selection
    *into* a shell, so honouring the variables it is about to set would make the
    first export permanent.
    """
    shell = (shell or _guess_shell()).lower()
    if shell not in _SHELLS:
        raise typer.Exit(1)

    state = _read_state()
    lines = []
    for var, key in (("AGENTS_HUB_WORKSPACE", "workspace"), ("AGENTS_HUB_PROJECT", "project")):
        value = state.get(key)
        if shell == "fish":
            lines.append(f"set -gx {var} {_sh_quote(str(value))}" if value else f"set -e {var}")
        else:
            lines.append(f"export {var}={_sh_quote(str(value))}" if value else f"unset {var}")

    # Plain print, not console.print: this output is eval'd, and rich would wrap
    # it at the terminal width and colour it.
    print("\n".join(lines))


@app.command("shell-init")
def shell_init(
    shell: Optional[str] = typer.Argument(None, help=f"One of {', '.join(_SHELLS)}. Defaults to $SHELL."),
    name: str = typer.Option("ah", "--name", "-n", help="Name for the generated function."),
    install: bool = typer.Option(False, "--install", help="Write it into the shell's startup file."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove a previously installed block."),
    rc_file: Optional[str] = typer.Option(None, "--rc-file", help="Startup file to edit instead of the default."),
):
    """Print shell integration to source, the way `conda init` does.

        agents-hub shell-init --install     # writes it into ~/.zshrc
        agents-hub shell-init zsh           # or print it and place it yourself

    The function passes everything through to this CLI, and after a command that
    changes the selection it exports that selection into the current shell — the
    one thing a child process cannot do for itself. Installing is idempotent: the
    block sits between markers and is rewritten in place, never stacked.
    """
    shell = (shell or _guess_shell()).lower()
    if shell not in _SHELLS:
        console.print(f"[red]Unsupported shell '{shell}'.[/red] Known: {', '.join(_SHELLS)}")
        raise typer.Exit(1)

    launch = _launcher()
    header = (
        f"# Agents Hub shell integration for {shell}. Generated by `shell-init`.\n"
        f"# The path below is baked in: re-run it after moving or reinstalling the hub."
    )

    if shell == "fish":
        cases = " ".join(_sh_quote(c) for c in _SELECTION_COMMANDS)
        script = f"""{header}
function {name} --description 'Agents Hub'
    command {launch} $argv
    set -l _ah_status $status
    set -l _ah_cmd ''
    if test (count $argv) -ge 2
        set _ah_cmd "$argv[1]:$argv[2]"
    end
    switch $_ah_cmd
        case {cases}
            command {launch} shell-export fish | source
    end
    return $_ah_status
end"""
    else:
        cases = "|".join(_SELECTION_COMMANDS)
        script = f"""{header}
{name}() {{
  command {launch} "$@"
  local _ah_status=$?
  case "$1:$2" in
    {cases})
      eval "$(command {launch} shell-export {shell} 2>/dev/null)"
      ;;
  esac
  return $_ah_status
}}"""

    if not (install or uninstall):
        print(script)
        return

    rc = Path(rc_file).expanduser() if rc_file else _rc_file(shell)
    outcome = _write_block(rc, None if uninstall else script)
    messages = {
        "installed": f"[green]Installed[/green] the {shell} integration in {rc}",
        "updated": f"[green]Updated[/green] the {shell} integration in {rc}",
        "removed": f"[yellow]Removed[/yellow] the {shell} integration from {rc}",
        "absent": f"[dim]Nothing to remove: no integration block in {rc}[/dim]",
    }
    console.print(messages[outcome])
    if outcome in ("installed", "updated"):
        console.print(f"[dim]Open a new shell (or `source {rc}`), then [bold]{name} workspace current[/bold].[/dim]")


# ---------------------------------------------------------------------------
# top-level config command
# ---------------------------------------------------------------------------


@app.command("config")
def config_show():
    """Show current backend configuration (settings endpoint)."""
    cfg = call(hub().get_settings)

    table = Table(title="Configuration", box=box.ROUNDED)
    table.add_column("Key", style="bold")
    table.add_column("Value")

    if isinstance(cfg, dict):
        for k, v in cfg.items():
            # Mask API keys
            display = "***" if "key" in k.lower() and v else str(v) if v is not None else "[dim]-[/dim]"
            table.add_row(k, display)
    else:
        console.print(cfg)
        return

    console.print(table)
    console.print(f"\n[dim]Service: {hub().describe()}[/dim]")


# ---------------------------------------------------------------------------
# Secrets (common/secrets.py; docs/secrets.md)
# ---------------------------------------------------------------------------
# Values go in and never come out: `list` shows names, scopes and hints, and
# there is deliberately no `get`. Direct mode calls common.secrets in this
# process; over HTTP the same commands use /api/workspaces/{name}/secrets.

secrets_app = typer.Typer(help="Workspace secrets: encrypted values handed to agents by name.",
                          no_args_is_help=True)
app.add_typer(secrets_app, name="secrets")


def _secrets_workspace(workspace: Optional[str]) -> str:
    ws = _active_workspace(workspace)
    if not ws:
        console.print("[red]Error:[/red] no workspace selected; pass --workspace.")
        raise typer.Exit(1)
    return ws


def _secrets_call(direct, method: str, path: str, *, params=None, json=None):
    """One secrets operation on whichever backend serves this CLI."""
    backend = hub()
    if getattr(backend, "kind", "") == "http":
        return call(backend._request, method, path, params=params, json=json)
    from common import secrets as secret_store
    try:
        return direct(secret_store)
    except secret_store.SecretsError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _secrets_audit(action: str, workspace: str, name: str, agent: Optional[str],
                   user: Optional[str]) -> None:
    """Direct mode writes the same audit row the route would, as the operator."""
    from common import audit
    from common.auth import LOCAL_PRINCIPAL
    audit.record(action, principal=LOCAL_PRINCIPAL, object_type="secret", object_id=name,
                 workspace=workspace,
                 details={"name": name, "agent_id": agent or "", "user_id": user or ""})


@secrets_app.command("keygen")
def secrets_keygen():
    """Print a fresh key for AGENTS_HUB_SECRET_KEY."""
    from common.secrets import keygen
    typer.echo(keygen())


@secrets_app.command("list")
def secrets_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List the secrets of a workspace: names, scopes and hints, never values."""
    ws = _secrets_workspace(workspace)
    rows = _secrets_call(lambda s: s.list_secrets(ws), "GET", f"/api/workspaces/{ws}/secrets") or []
    if json_out:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        console.print(f"[dim]No secrets in {ws}.[/dim]")
        return
    table = Table(box=box.SIMPLE, show_header=True, title=f"secrets in {ws}")
    for col in ("name", "agent", "user", "hint", "updated"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["name"], r.get("agent_id") or "[dim]any[/dim]",
                      r.get("user_id") or "[dim]any[/dim]", r.get("hint") or "",
                      str(r.get("updated_at") or "")[:19])
    console.print(table)


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name, e.g. GITHUB_TOKEN."),
    value: Optional[str] = typer.Option(
        None, "--value", help="The value; '-' reads stdin. Prompted for, hidden, when omitted."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name."),
    agent: Optional[str] = typer.Option(None, "--agent", help="Only for this agent."),
    user: Optional[str] = typer.Option(None, "--user", help="Only for runs this user launches."),
):
    """Store or replace a secret."""
    ws = _secrets_workspace(workspace)
    if value == "-":
        value = sys.stdin.read().rstrip("\n")
    elif value is None:
        value = typer.prompt(f"Value for {name}", hide_input=True)

    def direct(s):
        row = s.set_secret(ws, name, value, agent_id=agent, user_id=user, created_by="local")
        _secrets_audit("secret.set", ws, name, agent, user)
        return row

    _secrets_call(direct, "PUT", f"/api/workspaces/{ws}/secrets/{name}",
                  json={"value": value, "agent_id": agent, "user_id": user})
    console.print(f"[green]Set[/green] {name} in {ws}.")


@secrets_app.command("delete")
def secrets_delete(
    name: str = typer.Argument(..., help="Secret name."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name."),
    agent: Optional[str] = typer.Option(None, "--agent", help="The agent scope it was set with."),
    user: Optional[str] = typer.Option(None, "--user", help="The user scope it was set with."),
):
    """Delete one secret at exactly the given scope."""
    ws = _secrets_workspace(workspace)

    def direct(s):
        if not s.delete_secret(ws, name, agent_id=agent, user_id=user):
            console.print(f"[red]Error:[/red] no secret {name} at that scope in {ws}.")
            raise typer.Exit(1)
        _secrets_audit("secret.delete", ws, name, agent, user)
        return {"deleted": True}

    params = {k: v for k, v in (("agent_id", agent), ("user_id", user)) if v}
    _secrets_call(direct, "DELETE", f"/api/workspaces/{ws}/secrets/{name}", params=params or None)
    console.print(f"[green]Deleted[/green] {name} from {ws}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Named rather than inline: `pip install -e .` points the `agents-hub` console
# script at it (see pyproject.toml), and an entry point has to be a function.


def main() -> None:
    app()


if __name__ == "__main__":
    main()

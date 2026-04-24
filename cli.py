#!/usr/bin/env python3
"""
Agents Hub CLI — interact with the Agents Hub backend from the terminal.

Usage:
    python cli.py --help
    python cli.py server start
    python cli.py agent list
    python cli.py task create "Build a REST API"
"""
import os
import sys
import json
import subprocess
from typing import Optional

import typer
import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.text import Text

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
server_app = typer.Typer(help="Backend server commands.", no_args_is_help=True)

app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(workspace_app, name="workspace")
app.add_typer(node_app, name="node")
app.add_typer(server_app, name="server")

console = Console()

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

DEFAULT_URL = "http://localhost:8000"


def _base_url() -> str:
    return os.environ.get("AGENTS_HUB_URL", DEFAULT_URL).rstrip("/")


def _get(path: str, params: dict | None = None) -> dict | list:
    try:
        r = requests.get(f"{_base_url()}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.ConnectionError:
        console.print(
            f"[red]Cannot connect to backend at {_base_url()}.[/red]\n"
            "Start it with: [bold]python cli.py server start[/bold]"
        )
        raise typer.Exit(1)
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        console.print(f"[red]Error:[/red] {detail}")
        raise typer.Exit(1)


def _post(path: str, body: dict) -> dict | list:
    try:
        r = requests.post(f"{_base_url()}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.ConnectionError:
        console.print(
            f"[red]Cannot connect to backend at {_base_url()}.[/red]\n"
            "Start it with: [bold]python cli.py server start[/bold]"
        )
        raise typer.Exit(1)
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        console.print(f"[red]Error:[/red] {detail}")
        raise typer.Exit(1)


def _delete(path: str) -> dict | None:
    try:
        r = requests.delete(f"{_base_url()}{path}", timeout=15)
        r.raise_for_status()
        return r.json() if r.content else None
    except requests.ConnectionError:
        console.print(f"[red]Cannot connect to backend at {_base_url()}.[/red]")
        raise typer.Exit(1)
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        console.print(f"[red]Error:[/red] {detail}")
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
# server commands
# ---------------------------------------------------------------------------


@server_app.command("start")
def server_start(
    host: str = typer.Option("0.0.0.0", help="Host to bind to."),
    port: int = typer.Option(8000, help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)."),
):
    """Start the backend API server."""
    backend_dir = os.path.join(os.path.dirname(__file__), "dashboard", "backend")
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
# agent commands
# ---------------------------------------------------------------------------


@agent_app.command("list")
def agent_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Filter by workspace."),
):
    """List all available agents."""
    params = {"workspace": workspace} if workspace else None
    agents = _get("/api/agents", params=params)

    table = Table(title="Agents", box=box.ROUNDED, show_lines=False)
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Domain")
    table.add_column("Type")
    table.add_column("Remote", justify="center")

    for a in agents:
        table.add_row(
            a.get("id", ""),
            a.get("name", ""),
            a.get("domain", ""),
            a.get("type", ""),
            "✓" if a.get("is_remote") else "",
        )

    console.print(table)
    console.print(f"[dim]{len(agents)} agent(s)[/dim]")


@agent_app.command("get")
def agent_get(agent_id: str = typer.Argument(..., help="Agent ID.")):
    """Show details for a specific agent."""
    a = _get(f"/api/agents/{agent_id}")

    console.print(Panel(
        "\n".join([
            f"[bold]ID:[/bold]          {a.get('id')}",
            f"[bold]Name:[/bold]        {a.get('name')}",
            f"[bold]Domain:[/bold]      {a.get('domain', '-')}",
            f"[bold]Type:[/bold]        {a.get('type', '-')}",
            f"[bold]Description:[/bold] {a.get('description', '-')}",
            f"[bold]Remote:[/bold]      {a.get('is_remote', False)}",
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
    """Run an agent with a one-shot instruction (starts a node if needed)."""
    # Ensure a node is running for this agent
    nodes = _get("/api/nodes")
    agent_nodes = [n for n in nodes if n.get("agent_id") == agent_id and n.get("status") == "running"]
    if not agent_nodes:
        console.print(f"[yellow]No running node for '{agent_id}'. Starting one…[/yellow]")
        _post("/api/nodes", {"agent_id": agent_id, "workspace": workspace})
        console.print("[green]Node started.[/green]")

    # Send as a single-turn chat message
    body: dict = {"agent_id": agent_id, "message": instruction, "history": []}
    if workspace:
        body["workspace"] = workspace

    console.print(f"[bold]Running agent:[/bold] {agent_id}")
    console.rule()
    result = _post("/api/chat/message", body)
    console.print(result.get("response", result))


# ---------------------------------------------------------------------------
# task commands
# ---------------------------------------------------------------------------


@task_app.command("list")
def task_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Filter by workspace name."),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """List all tasks."""
    params = {}
    if workspace:
        params["workspace"] = workspace
    tasks = _get("/api/tasks", params=params or None)

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
    # Try full UUID first; allow prefix matching via list
    try:
        t = _get(f"/api/tasks/{task_id}")
    except SystemExit:
        # Try prefix match
        tasks = _get("/api/tasks")
        matches = [x for x in tasks if str(x.get("id", "")).startswith(task_id)]
        if not matches:
            console.print(f"[red]No task found matching '{task_id}'[/red]")
            raise typer.Exit(1)
        if len(matches) > 1:
            console.print(f"[red]Ambiguous prefix — {len(matches)} matches. Use more characters.[/red]")
            raise typer.Exit(1)
        t = matches[0]

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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace name."),
    decompose: bool = typer.Option(False, "--decompose", help="Auto-decompose into subtasks."),
):
    """Create a new task."""
    body: dict = {"title": title}
    if description:
        body["description"] = description
    if workspace:
        body["workspace_name"] = workspace
    if decompose:
        body["should_decompose"] = True

    t = _post("/api/tasks", body)
    console.print(f"[green]Task created:[/green] [bold]{str(t.get('id', ''))[:8]}[/bold]  {t.get('title')}")


@task_app.command("assign")
def task_assign(
    task_id: str = typer.Argument(..., help="Task ID (full UUID)."),
    agent_id: str = typer.Argument(..., help="Agent ID to assign."),
    mode: str = typer.Option("live", "--mode", "-m", help="Approval mode: live or manual."),
):
    """Assign an agent to a task and start execution."""
    result = _post(f"/api/tasks/{task_id}/assign", {"agent_id": agent_id, "mode": mode})
    console.print(f"[green]Assigned[/green] agent [bold]{agent_id}[/bold] to task [bold]{task_id[:8]}[/bold]")
    if result.get("run_id"):
        console.print(f"[dim]Run ID: {result['run_id']}[/dim]")


@task_app.command("decompose")
def task_decompose(
    task_id: str = typer.Argument(..., help="Task ID to decompose."),
):
    """Break a task down into subtasks using the decomposer agent."""
    result = _post(f"/api/tasks/{task_id}/decompose", {})
    subtasks = result.get("subtasks", [])
    console.print(f"[green]Decomposed into {len(subtasks)} subtask(s):[/green]")
    for st in subtasks:
        console.print(f"  • {_short(st.get('title', ''), 60)}")


@task_app.command("stop")
def task_stop(task_id: str = typer.Argument(..., help="Task ID to stop.")):
    """Stop a running task."""
    result = _post(f"/api/tasks/{task_id}/stop", {})
    console.print(f"[yellow]Stopped[/yellow] task [bold]{task_id[:8]}[/bold]")


@task_app.command("delete")
def task_delete(
    task_id: str = typer.Argument(..., help="Task ID to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Delete a task."""
    if not yes:
        typer.confirm(f"Delete task {task_id[:8]}?", abort=True)
    _delete(f"/api/tasks/{task_id}")
    console.print(f"[red]Deleted[/red] task [bold]{task_id[:8]}[/bold]")


# ---------------------------------------------------------------------------
# workspace commands
# ---------------------------------------------------------------------------


@workspace_app.command("list")
def workspace_list():
    """List all workspaces."""
    workspaces = _get("/api/workspaces")

    table = Table(title="Workspaces", box=box.ROUNDED)
    table.add_column("Name", style="bold cyan")
    table.add_column("Tasks", justify="right")
    table.add_column("Agents")

    for ws in workspaces:
        agents_allowed = ws.get("allowed_agents") or []
        table.add_row(
            ws.get("name", ""),
            str(ws.get("task_count", 0)),
            ", ".join(agents_allowed) if agents_allowed else "[dim]all[/dim]",
        )

    console.print(table)
    console.print(f"[dim]{len(workspaces)} workspace(s)[/dim]")


@workspace_app.command("create")
def workspace_create(
    name: str = typer.Argument(..., help="Workspace name."),
):
    """Create a new workspace."""
    result = _post("/api/workspaces", {"name": name})
    console.print(f"[green]Workspace created:[/green] [bold]{result.get('name', name)}[/bold]")


# ---------------------------------------------------------------------------
# node commands
# ---------------------------------------------------------------------------


@node_app.command("list")
def node_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List all running nodes."""
    params = {"workspace": workspace} if workspace else None
    nodes = _get("/api/nodes", params=params)

    table = Table(title="Nodes", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Agent", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Workspace")
    table.add_column("Started")

    for n in nodes:
        nid = str(n.get("id", ""))[:8]
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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    label: Optional[str] = typer.Option(None, "--label", "-l"),
):
    """Start a persistent node for an agent."""
    body: dict = {"agent_id": agent_id}
    if workspace:
        body["workspace"] = workspace
    if label:
        body["label"] = label
    node = _post("/api/nodes", body)
    console.print(f"[green]Node started:[/green] [bold]{str(node.get('id', ''))[:8]}[/bold]  agent={agent_id}")


@node_app.command("stop")
def node_stop(
    node_id: str = typer.Argument(..., help="Node ID to stop."),
):
    """Stop a running node."""
    _post(f"/api/nodes/{node_id}/stop", {})
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
    # Ensure a node is running for this agent
    nodes = _get("/api/nodes")
    agent_nodes = [n for n in nodes if n.get("agent_id") == agent_id and n.get("status") == "running"]
    if not agent_nodes:
        console.print(f"[yellow]No running node for '{agent_id}'. Starting one…[/yellow]")
        _post("/api/nodes", {"agent_id": agent_id, "workspace": workspace})
        console.print("[green]Node started.[/green]")

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
        }
        if workspace:
            body["workspace"] = workspace
        if project_id:
            body["project_id"] = project_id

        try:
            result = _post("/api/chat/message", body)
        except SystemExit:
            break

        response = result.get("response", "")
        console.print(f"\n[bold cyan]{agent_id}[/bold cyan] > {response}")

        # Accumulate history for multi-turn context
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": response})


# ---------------------------------------------------------------------------
# top-level config command
# ---------------------------------------------------------------------------


@app.command("config")
def config_show():
    """Show current backend configuration (settings endpoint)."""
    try:
        cfg = _get("/api/settings")
    except SystemExit:
        return

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
    console.print(f"\n[dim]Backend: {_base_url()}[/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()

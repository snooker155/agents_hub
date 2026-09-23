"""`ah node`: start, stop, and list persistent agent nodes."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from cli.main import _active_workspace, _node_color, _resolve_node_id, call, console, hub

node_app = typer.Typer(help="Node management commands.", no_args_is_help=True)


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

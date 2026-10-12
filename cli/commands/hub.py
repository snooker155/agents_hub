"""``ah hub``: this hub's link to another, and the nodes attached to this one
(docs/hub-link.md).

``ah hub link URL --key KEY`` attaches this hub to the one at URL; ``ah hub
status``, ``ah hub sync`` and ``ah hub unlink`` do what they say. ``ah hub
nodes ...`` manages the nodes of *this* hub: ``add`` prints the node token
once, which the other machine pastes into ``ah hub link ... --node-token``.
Every command goes through ``hub().request``, in process by default and
over REST when ``AGENTS_HUB_URL`` is set.
"""
from __future__ import annotations

import json
from typing import List, Optional

import typer
from rich import box
from rich.table import Table

from cli.main import call, console, hub

hub_app = typer.Typer(help="Link this hub to another, and manage this hub's nodes.", no_args_is_help=True)
nodes_app = typer.Typer(help="The nodes attached to this hub.", no_args_is_help=True)
hub_app.add_typer(nodes_app, name="nodes")


def _fmt(value) -> str:
    return "yes" if value else "no"


@hub_app.command("status")
def hub_status(json_out: bool = typer.Option(False, "--json", help="Print JSON.")):
    """Where this hub is linked, what it mirrors, and whether it serves as a node."""
    data = call(hub().request, "GET", "/api/hublink") or {}
    if json_out:
        console.print_json(json.dumps(data))
        return
    link = data.get("link")
    if not link:
        console.print("Not linked. Attach this hub with [bold]ah hub link URL --key KEY[/bold].")
        return
    mirror = link.get("mirror") or {}
    node = link.get("node") or {}
    rt = (data.get("runtime") or {}).get("node") or {}
    console.print(f"[bold]Linked to[/bold] {link.get('url')} as [cyan]{link.get('name')}[/cyan]")
    console.print(f"  models: {_fmt(link.get('use_models'))}   agents: {_fmt(link.get('use_agents'))}   "
                  f"target workspace: {link.get('target_workspace') or 'default'}")
    console.print(f"  mirror: runs {_fmt(mirror.get('runs'))}, chats {_fmt(mirror.get('chats'))}, "
                  f"memory {_fmt(mirror.get('memory'))}")
    console.print(f"  node: {_fmt(node.get('enabled'))}"
                  + (f" (connected: {_fmt(rt.get('connected'))}, answered {rt.get('answered', 0)})" if node.get('enabled') else ""))
    outbox = data.get("outbox") or {}
    console.print(f"  outbox: {outbox.get('pending', 0)} pending"
                  + (f", last error: {outbox.get('last_error')}" if outbox.get("last_error") else ""))
    if link.get("last_sync_at"):
        console.print(f"  last sync: {link['last_sync_at']}")
    if link.get("last_error"):
        console.print(f"  [yellow]last error:[/yellow] {link['last_error']}")


@hub_app.command("link")
def hub_link(
    url: str = typer.Argument(..., help="The remote hub, e.g. https://hub.example.com"),
    key: str = typer.Option("", "--key", help="A personal API key cut on the remote hub."),
    name: Optional[str] = typer.Option(None, "--name", help="How this hub is called there (default: hostname)."),
    models: bool = typer.Option(True, "--models/--no-models", help="Use the remote's models here."),
    agents: bool = typer.Option(True, "--agents/--no-agents", help="Attach the remote's agents as tools."),
    mirror: Optional[str] = typer.Option(None, "--mirror", help="What to mirror: runs,chats,memory (comma separated; 'none' for nothing)."),
    workspace: str = typer.Option("", "--workspace", help="Where mirrored records land on the remote (default: your personal workspace there)."),
    node_token: Optional[str] = typer.Option(None, "--node-token", help="A node token from the remote's Nodes page: serve it as a node."),
    no_node: bool = typer.Option(False, "--no-node", help="Stop serving as a node."),
    share_models: bool = typer.Option(True, "--share-models/--no-share-models", help="As a node, offer this hub's local models."),
    sandbox: bool = typer.Option(True, "--sandbox/--no-sandbox", help="As a node, offer this hub's sandbox."),
):
    """Attach this hub to another one."""
    body = {"url": url, "api_key": key, "name": name, "use_models": models, "use_agents": agents,
            "target_workspace": workspace}
    if mirror is not None:
        wanted = {p.strip() for p in mirror.split(",") if p.strip() and p.strip() != "none"}
        body["mirror"] = {k: (k in wanted) for k in ("runs", "chats", "memory")}
    node = {"share_models": share_models, "sandbox": sandbox}
    if node_token:
        node.update({"enabled": True, "token": node_token})
    if no_node:
        node["enabled"] = False
    body["node"] = node
    data = call(hub().request, "PUT", "/api/hublink", json=body) or {}
    link = data.get("link") or {}
    console.print(f"[green]Linked[/green] to {link.get('url')} as [cyan]{link.get('name')}[/cyan].")
    if link.get("use_models"):
        console.print("  The remote's models are the custom backend [bold]hub-remote[/bold] (Models page).")
    if link.get("use_agents"):
        console.print("  The remote's agents are tools through the MCP server [bold]hub-remote[/bold] in the default workspace.")
    if (link.get("node") or {}).get("enabled"):
        console.print("  This hub now serves the remote as a node.")


@hub_app.command("unlink")
def hub_unlink(yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask.")):
    """Detach this hub. Nothing is deleted on the remote."""
    if not yes and not typer.confirm("Detach this hub from the remote one?", default=False):
        raise typer.Exit(0)
    data = call(hub().request, "DELETE", "/api/hublink") or {}
    console.print("[green]Unlinked.[/green]" if data.get("removed") else "This hub was not linked.")


@hub_app.command("sync")
def hub_sync():
    """Send what is in the outbox now, and say hello as a node."""
    data = call(hub().request, "POST", "/api/hublink/sync") or {}
    drained = data.get("drained") or {}
    console.print(f"Sent {drained.get('sent', 0)}, failed {drained.get('failed', 0)}; "
                  f"node hello: {_fmt(data.get('hello'))}.")


# ── nodes of this hub ────────────────────────────────────────────────────────

@nodes_app.command("list")
def nodes_list(json_out: bool = typer.Option(False, "--json", help="Print JSON.")):
    """The nodes attached to this hub."""
    data = call(hub().request, "GET", "/api/nodes") or {}
    rows = data.get("nodes") or []
    if json_out:
        console.print_json(json.dumps(rows))
        return
    table = Table(title="Nodes", box=box.ROUNDED)
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Online")
    table.add_column("Models", justify="right")
    table.add_column("Sandbox")
    table.add_column("Workspaces")
    for n in rows:
        table.add_row(n.get("id", ""), n.get("name", ""), _fmt(n.get("online")),
                      str(len(n.get("models") or [])), _fmt((n.get("capabilities") or {}).get("sandbox")),
                      ", ".join(n.get("workspaces") or []) or "any")
    console.print(table)


@nodes_app.command("add")
def nodes_add(
    name: str = typer.Argument(..., help="A name for the machine, e.g. 'laptop'."),
    workspace: List[str] = typer.Option([], "--workspace", "-w", help="Limit the node's sandbox to these workspaces (repeatable)."),
):
    """Register a node and print its token, once."""
    data = call(hub().request, "POST", "/api/nodes", json={"name": name, "workspaces": list(workspace) or None}) or {}
    node = data.get("node") or {}
    console.print(f"[green]Node created:[/green] {node.get('id')} ({node.get('name')})")
    console.print(f"Token (shown once): [bold]{data.get('token')}[/bold]")
    console.print("On the other machine: [dim]ah hub link <this hub's URL> --key <personal key> "
                  f"--node-token {data.get('token')}[/dim]")


@nodes_app.command("remove")
def nodes_remove(node_id: str = typer.Argument(..., help="The node id."),
                 yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask.")):
    """Remove a node. Its models and sandbox stop being offered here."""
    if not yes and not typer.confirm(f"Remove node {node_id}?", default=False):
        raise typer.Exit(0)
    data = call(hub().request, "DELETE", f"/api/nodes/{node_id}") or {}
    console.print("[green]Removed.[/green]" if data.get("removed") else "No such node.")


@nodes_app.command("rotate")
def nodes_rotate(node_id: str = typer.Argument(..., help="The node id.")):
    """Issue a new token for a node; the old one stops working at once."""
    data = call(hub().request, "POST", f"/api/nodes/{node_id}/rotate") or {}
    console.print(f"New token (shown once): [bold]{data.get('token')}[/bold]")

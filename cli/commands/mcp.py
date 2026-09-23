"""`ah mcp`: attach, test, and inspect MCP servers on a workspace.

Every value that looks like a credential (a header, an env var) is masked by
the API itself before it ever reaches here (dashboard/backend/routes/mcp.py),
so this module never has to think about it either.
"""
from __future__ import annotations

from typing import List, Optional

import typer
from rich import box
from rich.table import Table

from cli.main import call, console, hub
from cli.openapi import render_result

mcp_app = typer.Typer(help="MCP server commands.", no_args_is_help=True)


def _workspace(workspace: Optional[str]) -> str:
    from cli.main import _active_workspace
    ws = _active_workspace(workspace)
    if not ws:
        console.print("[red]Error:[/red] no workspace selected; pass --workspace.")
        raise typer.Exit(1)
    return ws


@mcp_app.command("list")
def mcp_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List MCP servers attached to a workspace."""
    ws = _workspace(workspace)
    servers = (call(hub().request, "GET", "/api/mcp/servers", params={"workspace": ws}) or {}).get("servers", [])
    if json_out:
        render_result(console, servers, as_json=True)
        return
    table = Table(title=f"MCP servers in {ws}", box=box.ROUNDED)
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Transport")
    table.add_column("Enabled")
    table.add_column("Tools", justify="right")
    for s in servers:
        count = s.get("tool_count")
        table.add_row(s.get("id", ""), s.get("name") or s.get("id", ""), s.get("transport", ""),
                      "yes" if s.get("enabled") else "no",
                      str(count) if count is not None else "[dim]not loaded[/dim]")
    console.print(table)
    console.print(f"[dim]{len(servers)} server(s)[/dim]")


@mcp_app.command("add")
def mcp_add(
    server_id: str = typer.Argument(..., help="A short, unique id for this server."),
    transport: str = typer.Option("stdio", "--transport", help="stdio or http."),
    command: Optional[str] = typer.Option(None, "--command", help="Command to run, for a stdio server."),
    arg: List[str] = typer.Option([], "--arg", help="Repeatable: one argument to the stdio command."),
    url: Optional[str] = typer.Option(None, "--url", help="Server URL, for an http server."),
    name: Optional[str] = typer.Option(None, "--name", help="Defaults to the id."),
    description: str = typer.Option("", "--desc", "-d"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
):
    """Attach an MCP server to a workspace.

    Headers, environment variables and a tool allowlist are not exposed here;
    set them with `ah api patch /api/mcp/servers/<id> --json '{...}'` or from
    the MCP page once the server exists.
    """
    ws = _workspace(workspace)
    body = {
        "id": server_id, "name": name or server_id, "description": description,
        "transport": transport, "command": command or "", "args": list(arg),
        "url": url or "", "enabled": enabled,
    }
    result = call(hub().request, "POST", "/api/mcp/servers", params={"workspace": ws}, json=body)
    server = (result or {}).get("server", {})
    console.print(f"[green]Attached[/green] MCP server [bold]{server.get('id', server_id)}[/bold] to {ws}")


@mcp_app.command("remove")
def mcp_remove(
    server_id: str = typer.Argument(..., help="Server id."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Detach an MCP server from a workspace."""
    ws = _workspace(workspace)
    if not yes:
        typer.confirm(f"Detach '{server_id}' from {ws}?", abort=True)
    call(hub().request, "DELETE", f"/api/mcp/servers/{server_id}", params={"workspace": ws})
    console.print(f"[red]Detached[/red] [bold]{server_id}[/bold] from {ws}")


@mcp_app.command("test")
def mcp_test(
    server_id: str = typer.Argument(..., help="Server id."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
):
    """Connect to a server now, and report what is there."""
    ws = _workspace(workspace)
    result = call(hub().request, "POST", f"/api/mcp/servers/{server_id}/test", params={"workspace": ws})
    render_result(console, result, as_json=False)


@mcp_app.command("tools")
def mcp_tools(
    server_id: str = typer.Argument(..., help="Server id."),
    refresh: bool = typer.Option(False, "--refresh", help="Reconnect instead of reading the cache."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """The tools a server offers, from cache unless --refresh reconnects."""
    ws = _workspace(workspace)
    params = {"workspace": ws}
    if refresh:
        params["refresh"] = "true"
    tools = (call(hub().request, "GET", f"/api/mcp/servers/{server_id}/tools", params=params) or {}).get("tools", [])
    if json_out:
        render_result(console, tools, as_json=True)
        return
    table = Table(title=f"Tools on {server_id}", box=box.SIMPLE, show_header=True)
    table.add_column("name")
    table.add_column("description")
    for t in tools:
        if isinstance(t, dict):
            table.add_row(t.get("name", ""), (t.get("description") or "")[:60])
        else:
            table.add_row(str(t), "")
    console.print(table)
    console.print(f"[dim]{len(tools)} tool(s)[/dim]")

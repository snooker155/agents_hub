"""`ah flow`: list, inspect, run, stop, and see the run history of a flow.

The daily-use shape of ``/api/flows`` (dashboard/backend/routes/flows.py): a
hand-written group rather than the OpenAPI-generated one, because the routes
that matter for a terminal are five of the router's twenty, and the ones that
do not (webhook triggers, YAML import/export, sharing) stay reachable through
`ah api` instead of cluttering this one. No method was added to either
backend for this: every command below goes through the generic transport,
``hub().request(...)``.
"""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_workspace, _short, call, console, hub
from cli.openapi import render_result

flow_app = typer.Typer(help="Agent flow commands.", no_args_is_help=True)


@flow_app.command("list")
def flow_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List flows visible to a workspace."""
    workspace = _active_workspace(workspace)
    params = {"workspace": workspace} if workspace else None
    flows = call(hub().request, "GET", "/api/flows", params=params) or []
    if json_out:
        render_result(console, flows, as_json=True)
        return
    table = Table(title="Flows", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Workspace")
    for f in flows:
        table.add_row(str(f.get("id", ""))[:8], f.get("name", ""),
                      _short(f.get("description"), 40), f.get("workspace") or "[dim]global[/dim]")
    console.print(table)
    console.print(f"[dim]{len(flows)} flow(s)[/dim]")


@flow_app.command("get")
def flow_get(
    flow_id: str = typer.Argument(..., help="Flow ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Show a flow: its metadata and node/edge counts."""
    f = call(hub().request, "GET", f"/api/flows/{flow_id}")
    if json_out:
        render_result(console, f, as_json=True)
        return
    lines = [
        f"[bold]ID:[/bold]          {f.get('id')}",
        f"[bold]Name:[/bold]        {f.get('name')}",
        f"[bold]Workspace:[/bold]   {f.get('workspace') or '[dim]global[/dim]'}",
        f"[bold]Description:[/bold] {f.get('description') or '-'}",
        f"[bold]Entry point:[/bold] {f.get('entry_point') or '-'}",
        f"[bold]Nodes:[/bold]       {len(f.get('nodes') or [])}",
        f"[bold]Edges:[/bold]       {len(f.get('edges') or [])}",
    ]
    console.print(Panel("\n".join(lines), title=f"Flow {str(f.get('id', ''))[:8]}", border_style="cyan"))


@flow_app.command("run")
def flow_run(
    flow_id: str = typer.Argument(..., help="Flow ID to run."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    description: Optional[str] = typer.Option(None, "--desc", "-d", help="Description for the task this run creates."),
    task_id: Optional[str] = typer.Option(None, "--task", help="Run inside an existing task instead of a new one."),
):
    """Start a flow run."""
    workspace = _active_workspace(workspace)
    body = {"workspace": workspace, "description": description, "task_id": task_id}
    result = call(hub().request, "POST", f"/api/flows/{flow_id}/run", json=body)
    console.print(f"[green]Started[/green] flow run [bold]{str(result.get('run_id', ''))[:8]}[/bold] "
                  f"(task {str(result.get('task_id', ''))[:8]}, workspace {result.get('workspace')})")
    if result.get("estimated_cost"):
        console.print(f"[dim]Estimated cost: {result['estimated_cost']}[/dim]")


@flow_app.command("stop")
def flow_stop(
    flow_id: str = typer.Argument(..., help="Flow ID to stop."),
    flow_run_id: Optional[str] = typer.Option(
        None, "--run", help="Stop only this run. Omit to stop every active run of this flow."),
):
    """Stop a flow's running instance(s)."""
    params = {"flow_run_id": flow_run_id} if flow_run_id else None
    result = call(hub().request, "POST", f"/api/flows/{flow_id}/stop", params=params)
    if result.get("stopped"):
        console.print(f"[yellow]Stopped[/yellow] flow [bold]{flow_id[:8]}[/bold]")
    else:
        console.print(f"[dim]Nothing was running for flow {flow_id[:8]}.[/dim]")


@flow_app.command("runs")
def flow_runs(
    flow_id: str = typer.Argument(..., help="Flow ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """The flow's run history."""
    runs = call(hub().request, "GET", f"/api/flows/{flow_id}/runs") or []
    if json_out:
        render_result(console, runs, as_json=True)
        return
    table = Table(title=f"Runs of flow {flow_id[:8]}", box=box.SIMPLE, show_header=True)
    for col in ("run", "status", "started", "task"):
        table.add_column(col)
    for r in runs:
        table.add_row(str(r.get("flow_run_id") or r.get("run_id") or "")[:8],
                      r.get("status", ""), str(r.get("started_at") or "")[:19],
                      str(r.get("task_id") or "")[:8])
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s)[/dim]")

"""`ah loop`: list, inspect, run, watch runs, and stop an evaluate-and-refine loop."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_workspace, call, console, hub
from cli.openapi import render_result

loop_app = typer.Typer(help="Loop (evaluate-and-refine) commands.", no_args_is_help=True)


@loop_app.command("list")
def loop_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List loops visible to a workspace."""
    workspace = _active_workspace(workspace)
    params = {"workspace": workspace} if workspace else None
    loops = (call(hub().request, "GET", "/api/loops", params=params) or {}).get("loops", [])
    if json_out:
        render_result(console, loops, as_json=True)
        return
    table = Table(title="Loops", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Flow")
    table.add_column("Max iterations", justify="right")
    table.add_column("Workspace")
    for loop in loops:
        table.add_row(str(loop.get("loop_id", ""))[:8], loop.get("name", ""),
                      str(loop.get("flow_id") or "")[:8], str(loop.get("max_iterations", "")),
                      loop.get("workspace") or "")
    console.print(table)
    console.print(f"[dim]{len(loops)} loop(s)[/dim]")


@loop_app.command("get")
def loop_get(
    loop_id: str = typer.Argument(..., help="Loop ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Show a loop's configuration."""
    loop = call(hub().request, "GET", f"/api/loops/{loop_id}")
    if json_out:
        render_result(console, loop, as_json=True)
        return
    lines = [
        f"[bold]ID:[/bold]           {loop.get('loop_id')}",
        f"[bold]Name:[/bold]         {loop.get('name')}",
        f"[bold]Flow:[/bold]         {loop.get('flow_id')}",
        f"[bold]Workspace:[/bold]    {loop.get('workspace') or '-'}",
        f"[bold]Exit criterion:[/bold] {loop.get('exit_criterion') or '-'}",
        f"[bold]Iterations:[/bold]   {loop.get('min_iterations')}-{loop.get('max_iterations')}",
        f"[bold]Target score:[/bold] {loop.get('target_score')}",
    ]
    console.print(Panel("\n".join(lines), title=f"Loop {str(loop.get('loop_id', ''))[:8]}", border_style="cyan"))


@loop_app.command("run")
def loop_run(
    loop_id: str = typer.Argument(..., help="Loop ID to run."),
    goal: str = typer.Argument(..., help="What this run is trying to achieve."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    task_id: Optional[str] = typer.Option(None, "--task", help="Attach the run to an existing task."),
):
    """Start a loop run."""
    workspace = _active_workspace(workspace)
    body = {"goal": goal, "workspace": workspace, "task_id": task_id}
    run = call(hub().request, "POST", f"/api/loops/{loop_id}/run", json=body)
    console.print(f"[green]Started[/green] loop run [bold]{str(run.get('loop_run_id', ''))[:8]}[/bold]")


@loop_app.command("runs")
def loop_runs(
    loop_id: Optional[str] = typer.Argument(None, help="Only runs of this loop. Omit for every recent run."),
    limit: int = typer.Option(50, "--limit", help="Most recent runs to show."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Recent loop runs."""
    params: dict = {"limit": limit}
    if loop_id:
        params["loop_id"] = loop_id
    runs = (call(hub().request, "GET", "/api/loops/runs", params=params) or {}).get("runs", [])
    if json_out:
        render_result(console, runs, as_json=True)
        return
    table = Table(title="Loop runs", box=box.SIMPLE, show_header=True)
    for col in ("run", "loop", "status", "iterations", "best score"):
        table.add_column(col)
    for r in runs:
        table.add_row(str(r.get("loop_run_id", ""))[:8], str(r.get("loop_id", ""))[:8],
                      r.get("status", ""), str(r.get("iterations_done", "")), str(r.get("best_score", "")))
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s)[/dim]")


@loop_app.command("stop")
def loop_stop(loop_run_id: str = typer.Argument(..., help="Loop run ID to stop.")):
    """Ask a running loop to stop, at its next checkpoint."""
    call(hub().request, "POST", f"/api/loops/runs/{loop_run_id}/stop")
    console.print(f"[yellow]Stop requested[/yellow] for loop run [bold]{loop_run_id[:8]}[/bold]")

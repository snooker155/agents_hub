"""`ah team`: list, inspect, run, watch runs, and stop a multi-agent team."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_workspace, call, console, hub
from cli.openapi import render_result

team_app = typer.Typer(help="Team (multi-agent) commands.", no_args_is_help=True)


@team_app.command("list")
def team_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List teams visible to a workspace."""
    workspace = _active_workspace(workspace)
    params = {"workspace": workspace} if workspace else None
    teams = (call(hub().request, "GET", "/api/teams", params=params) or {}).get("teams", [])
    if json_out:
        render_result(console, teams, as_json=True)
        return
    table = Table(title="Teams", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Members", justify="right")
    table.add_column("Workspace")
    for t in teams:
        table.add_row(str(t.get("team_id", ""))[:8], t.get("name", ""),
                      str(len(t.get("members") or [])), t.get("workspace") or "")
    console.print(table)
    console.print(f"[dim]{len(teams)} team(s)[/dim]")


@team_app.command("get")
def team_get(
    team_id: str = typer.Argument(..., help="Team ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Show a team's configuration and members."""
    team = call(hub().request, "GET", f"/api/teams/{team_id}")
    if json_out:
        render_result(console, team, as_json=True)
        return
    lines = [
        f"[bold]ID:[/bold]        {team.get('team_id')}",
        f"[bold]Name:[/bold]      {team.get('name')}",
        f"[bold]Workspace:[/bold] {team.get('workspace') or '-'}",
        f"[bold]Description:[/bold] {team.get('description') or '-'}",
        f"[bold]Members:[/bold]   {', '.join(m.get('agent_id', '') for m in (team.get('members') or [])) or '-'}",
    ]
    console.print(Panel("\n".join(lines), title=f"Team {str(team.get('team_id', ''))[:8]}", border_style="cyan"))


@team_app.command("run")
def team_run(
    team_id: str = typer.Argument(..., help="Team ID to run."),
    goal: Optional[str] = typer.Argument(
        None, help="What the team should work on. Defaults to the team's own description."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    task_id: Optional[str] = typer.Option(None, "--task", help="Attach the run to an existing task."),
):
    """Start a team run."""
    workspace = _active_workspace(workspace)
    body = {"goal": goal or "", "workspace": workspace, "task_id": task_id}
    run = call(hub().request, "POST", f"/api/teams/{team_id}/run", json=body)
    console.print(f"[green]Started[/green] team run [bold]{str(run.get('team_run_id', ''))[:8]}[/bold]")


@team_app.command("runs")
def team_runs(
    team_id: Optional[str] = typer.Argument(None, help="Only runs of this team. Omit for every recent run."),
    limit: int = typer.Option(50, "--limit", help="Most recent runs to show."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Recent team runs."""
    params: dict = {"limit": limit}
    if team_id:
        params["team_id"] = team_id
    runs = (call(hub().request, "GET", "/api/teams/runs", params=params) or {}).get("runs", [])
    if json_out:
        render_result(console, runs, as_json=True)
        return
    table = Table(title="Team runs", box=box.SIMPLE, show_header=True)
    for col in ("run", "team", "status"):
        table.add_column(col)
    for r in runs:
        table.add_row(str(r.get("team_run_id", ""))[:8], str(r.get("team_id", ""))[:8], r.get("status", ""))
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s)[/dim]")


@team_app.command("stop")
def team_stop(team_run_id: str = typer.Argument(..., help="Team run ID to stop.")):
    """Stop a running team."""
    call(hub().request, "POST", f"/api/teams/runs/{team_run_id}/stop")
    console.print(f"[yellow]Stopped[/yellow] team run [bold]{team_run_id[:8]}[/bold]")

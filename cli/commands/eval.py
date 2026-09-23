"""`ah eval`: list, inspect, run a sweep, watch runs, and diff two runs."""
from __future__ import annotations

from typing import List, Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_workspace, call, console, hub
from cli.openapi import render_result

eval_app = typer.Typer(help="Eval harness commands.", no_args_is_help=True)


@eval_app.command("list")
def eval_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List eval sets visible to a workspace."""
    workspace = _active_workspace(workspace)
    params = {"workspace": workspace} if workspace else None
    sets = (call(hub().request, "GET", "/api/evals", params=params) or {}).get("eval_sets", [])
    if json_out:
        render_result(console, sets, as_json=True)
        return
    table = Table(title="Eval sets", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Cases", justify="right")
    table.add_column("Agent")
    for s in sets:
        table.add_row(str(s.get("eval_set_id", ""))[:8], s.get("name", ""),
                      str(len(s.get("cases") or [])), s.get("agent_id") or "")
    console.print(table)
    console.print(f"[dim]{len(sets)} eval set(s)[/dim]")


@eval_app.command("get")
def eval_get(
    eval_set_id: str = typer.Argument(..., help="Eval set ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Show an eval set: its cases and graders."""
    evalset = call(hub().request, "GET", f"/api/evals/{eval_set_id}")
    if json_out:
        render_result(console, evalset, as_json=True)
        return
    lines = [
        f"[bold]ID:[/bold]        {evalset.get('eval_set_id')}",
        f"[bold]Name:[/bold]      {evalset.get('name')}",
        f"[bold]Workspace:[/bold] {evalset.get('workspace') or '-'}",
        f"[bold]Agent:[/bold]     {evalset.get('agent_id') or '-'}",
        f"[bold]Cases:[/bold]     {len(evalset.get('cases') or [])}",
        f"[bold]Graders:[/bold]   {', '.join(g.get('kind', '') for g in (evalset.get('graders') or [])) or '-'}",
    ]
    console.print(Panel("\n".join(lines), title=f"Eval set {str(evalset.get('eval_set_id', ''))[:8]}",
                        border_style="cyan"))


@eval_app.command("run")
def eval_run(
    eval_set_id: str = typer.Argument(..., help="Eval set ID to sweep."),
    agent: List[str] = typer.Option(
        [], "--agent", help="Repeatable: agent_id to sweep. Omit to use the set's own default agent."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    cost_ceiling: Optional[float] = typer.Option(
        None, "--cost-ceiling", help="Stop the sweep once accumulated spend crosses this (USD)."),
):
    """Run a sweep: real, billable LLM calls against every case."""
    workspace = _active_workspace(workspace)
    body = {
        "configs": [{"agent_id": a} for a in agent],
        "workspace": workspace,
        "cost_ceiling": cost_ceiling,
    }
    console.print(f"[bold]Running sweep for {eval_set_id[:8]}[/bold] (this is billable and can take a while)")
    result = call(hub().request, "POST", f"/api/evals/{eval_set_id}/run", json=body)
    console.print(f"[green]Done[/green]: run [bold]{str(result.get('eval_run_id', ''))[:8]}[/bold], "
                  f"cost {result.get('total_cost', '-')}")


@eval_app.command("runs")
def eval_runs(
    eval_set_id: str = typer.Argument(..., help="Eval set ID."),
    limit: int = typer.Option(50, "--limit", help="Most recent runs to show."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """This eval set's run history."""
    runs = (call(hub().request, "GET", f"/api/evals/{eval_set_id}/runs",
                params={"limit": limit}) or {}).get("eval_runs", [])
    if json_out:
        render_result(console, runs, as_json=True)
        return
    table = Table(title=f"Runs of {eval_set_id[:8]}", box=box.SIMPLE, show_header=True)
    for col in ("run", "status", "cost", "started"):
        table.add_column(col)
    for r in runs:
        table.add_row(str(r.get("eval_run_id", ""))[:8], r.get("status", ""),
                      str(r.get("total_cost", "")), str(r.get("started_at") or "")[:19])
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s)[/dim]")


@eval_app.command("diff")
def eval_diff(
    run_a: str = typer.Argument(..., help="Earlier eval run ID."),
    run_b: str = typer.Argument(..., help="Later eval run ID."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """Compare two eval runs of the same set, case by case."""
    diff = call(hub().request, "GET", f"/api/evals/runs/{run_a}/diff/{run_b}")
    render_result(console, diff, as_json=json_out)

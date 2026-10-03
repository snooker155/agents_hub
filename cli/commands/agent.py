"""`ah agent`: list, inspect, and run agents one-shot."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_project, _active_workspace, call, console, hub
from cli.openapi import render_result

agent_app = typer.Typer(help="Agent management commands.", no_args_is_help=True)


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


def read_overrides(overrides: Optional[str], overrides_file: Optional[str]) -> Optional[dict]:
    """The per-run overrides object of ``--overrides`` / ``--overrides-file``
    (agents/run_overrides.py), checked here so a typo fails before the call.
    ``--overrides-file -`` reads standard input."""
    import json
    import sys
    from pathlib import Path

    from agents import run_overrides

    if overrides and overrides_file:
        raise typer.BadParameter("give --overrides or --overrides-file, not both")
    raw: Optional[str] = overrides
    if overrides_file:
        try:
            raw = sys.stdin.read() if overrides_file == "-" else Path(overrides_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise typer.BadParameter(f"cannot read {overrides_file}: {exc}") from exc
    if not raw:
        return None
    try:
        data = json.loads(raw)
        run_overrides.normalize(data)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return data


@agent_app.command("run")
def agent_run(
    agent_id: str = typer.Argument(..., help="Agent ID."),
    instruction: str = typer.Argument(..., help="Instruction / prompt for the agent."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    agent_version: Optional[int] = typer.Option(
        None, "--agent-version", min=1,
        help="Run this stored version of the agent instead of the live definition."),
    overrides: Optional[str] = typer.Option(
        None, "--overrides",
        help='Per-run overrides as JSON, e.g. \'{"model": "gpt-4o-mini", "tools": {"remove": ["run_shell"]}}\'.'),
    overrides_file: Optional[str] = typer.Option(
        None, "--overrides-file", help="Read the per-run overrides from this JSON file (- for stdin)."),
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
    if agent_version is not None:
        body["agent_version"] = agent_version
    run_overrides = read_overrides(overrides, overrides_file)
    if run_overrides:
        body["overrides"] = run_overrides
    if workspace:
        body["workspace"] = workspace
    project = _active_project()
    if project:
        body["project_id"] = project

    console.print(f"[bold]Running agent:[/bold] {agent_id}")
    console.rule()
    result = call(hub().send_message, body)
    # The agent may hand the conversation over (docs/handoffs.md): show each
    # handing reply and who took over before the answer.
    for h in (result.get("handoffs") or []) if isinstance(result, dict) else []:
        console.print(str(h.get("from_response") or ""))
        console.print(f"[dim]handed over to {h.get('to_agent_name') or h.get('to_agent_id')}: "
                      f"{h.get('reason') or ''}[/dim]")
    console.print(result.get("response", result))


# ── Review status (dashboard/backend/routes/registry.py, docs/registry.md) ──
#
# Owner and admin, same review model flows and skills share (common/review.py).
# Listing reads straight from GET /api/registry rather than a dedicated route:
# that call is already the whole registry, and a second endpoint for "just the
# agents, filtered" would be one more thing to keep in step with it.

review_app = typer.Typer(help="An agent's owner and review status.", no_args_is_help=True)
agent_app.add_typer(review_app, name="review")


@review_app.command("list")
def agent_review_list(
    status: Optional[str] = typer.Option(
        None, "--status", help="draft, in_review, approved or rejected."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
):
    """List agents with their owner and review status."""
    agents = (call(hub().request, "GET", "/api/registry") or {}).get("agents", [])
    if status:
        agents = [a for a in agents if a.get("review_status") == status]
    if json_out:
        render_result(console, agents, as_json=True)
        return
    table = Table(title="Agent review", box=box.ROUNDED)
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Owner")
    table.add_column("Status")
    table.add_column("Shared")
    for a in agents:
        table.add_row(a.get("id", ""), a.get("name") or a.get("id", ""),
                      a.get("owner_user") or "(unknown)", a.get("review_status", ""),
                      "yes" if a.get("shared") else "no")
    console.print(table)
    console.print(f"[dim]{len(agents)} agent(s)[/dim]")


@review_app.command("submit")
def agent_review_submit(
    agent_id: str = typer.Argument(..., help="Agent ID."),
    note: Optional[str] = typer.Option(None, "--note"),
):
    """Ask for the agent to be reviewed and shared (owner or admin)."""
    result = call(hub().request, "POST", f"/api/registry/agents/{agent_id}/submit",
                 json={"note": note})
    console.print(f"[green]Submitted[/green] [bold]{agent_id}[/bold] "
                 f"(status: {result.get('review_status')})")


@review_app.command("approve")
def agent_review_approve(
    agent_id: str = typer.Argument(..., help="Agent ID."),
    note: Optional[str] = typer.Option(None, "--note"),
):
    """List the agent on the marketplace (admin)."""
    result = call(hub().request, "POST", f"/api/registry/agents/{agent_id}/approve",
                 json={"note": note})
    console.print(f"[green]Approved[/green] [bold]{agent_id}[/bold] "
                 f"(status: {result.get('review_status')})")


@review_app.command("reject")
def agent_review_reject(
    agent_id: str = typer.Argument(..., help="Agent ID."),
    note: Optional[str] = typer.Option(None, "--note"),
):
    """Turn the agent down; the owner may edit and resubmit (admin)."""
    result = call(hub().request, "POST", f"/api/registry/agents/{agent_id}/reject",
                 json={"note": note})
    console.print(f"[red]Rejected[/red] [bold]{agent_id}[/bold] "
                 f"(status: {result.get('review_status')})")

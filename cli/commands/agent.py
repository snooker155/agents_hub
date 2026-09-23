"""`ah agent`: list, inspect, and run agents one-shot."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import _active_project, _active_workspace, call, console, hub

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

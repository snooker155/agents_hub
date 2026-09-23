"""`ah task`: create, assign, decompose, list."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cli.main import (
    _active_project,
    _active_workspace,
    _resolve_task_id,
    _short,
    _task_color,
    call,
    console,
    hub,
)

task_app = typer.Typer(help="Task management commands.", no_args_is_help=True)


@task_app.command("list")
def task_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """List all tasks."""
    workspace = _active_workspace(workspace)
    params = {}
    if workspace:
        params["workspace"] = workspace
    tasks = call(hub().list_tasks, workspace)

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
    t = call(hub().get_task, _resolve_task_id(task_id))

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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    decompose: bool = typer.Option(False, "--decompose", help="Auto-decompose into subtasks."),
):
    """Create a new task."""
    workspace = _active_workspace(workspace)
    body: dict = {"title": title}
    if description:
        body["description"] = description
    if workspace:
        body["workspace_name"] = workspace
    project = _active_project()
    if project:
        body["project_id"] = project
    if decompose:
        body["should_decompose"] = True

    t = call(hub().create_task, body)
    console.print(f"[green]Task created:[/green] [bold]{str(t.get('id', ''))[:8]}[/bold]  {t.get('title')}")


@task_app.command("assign")
def task_assign(
    task_id: str = typer.Argument(..., help="Task ID (full or prefix)."),
    agent_id: str = typer.Argument(..., help="Agent ID to assign."),
):
    """Assign an agent to a task and start execution.

    Whether the run needs approval first is the workspace's assignment_mode, not
    a per-call choice; this always assigns directly.
    """
    task_id = _resolve_task_id(task_id)
    result = call(hub().assign_task, task_id, agent_id)
    console.print(f"[green]Assigned[/green] agent [bold]{agent_id}[/bold] to task [bold]{task_id[:8]}[/bold]")
    if result.get("run_id"):
        console.print(f"[dim]Run ID: {result['run_id']}[/dim]")


@task_app.command("decompose")
def task_decompose(
    task_id: str = typer.Argument(..., help="Task ID to decompose (full or prefix)."),
):
    """Break a task down into subtasks using the decomposer agent."""
    task_id = _resolve_task_id(task_id)
    result = call(hub().decompose_task, task_id)
    subtasks = result.get("subtasks", [])
    console.print(f"[green]Decomposed into {len(subtasks)} subtask(s):[/green]")
    for st in subtasks:
        console.print(f"  • {_short(st.get('title', ''), 60)}")


@task_app.command("stop")
def task_stop(task_id: str = typer.Argument(..., help="Task ID to stop (full or prefix).")):
    """Stop a running task."""
    task_id = _resolve_task_id(task_id)
    call(hub().stop_task, task_id)
    console.print(f"[yellow]Stopped[/yellow] task [bold]{task_id[:8]}[/bold]")


@task_app.command("delete")
def task_delete(
    task_id: str = typer.Argument(..., help="Task ID to delete (full or prefix)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Delete a task."""
    task_id = _resolve_task_id(task_id)
    if not yes:
        typer.confirm(f"Delete task {task_id[:8]}?", abort=True)
    call(hub().delete_task, task_id)
    console.print(f"[red]Deleted[/red] task [bold]{task_id[:8]}[/bold]")

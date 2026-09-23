"""`ah workspace`: list, create, attach, select."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from cli.main import (
    _active_project,
    _active_workspace,
    _detect_from_cwd,
    _read_state,
    _state_file,
    _workspace_source,
    _write_state,
    call,
    console,
    hub,
)

workspace_app = typer.Typer(help="Workspace management commands.", no_args_is_help=True)


@workspace_app.command("list")
def workspace_list():
    """List all workspaces."""
    workspaces = call(hub().list_workspaces)
    active = _active_workspace()

    table = Table(title="Workspaces", box=box.ROUNDED)
    table.add_column("", no_wrap=True)           # active marker
    table.add_column("Name", style="bold cyan")
    table.add_column("Tasks", justify="right")
    table.add_column("Location")

    for ws in workspaces:
        name = ws.get("name", "")
        # An attached workspace is a link, so show where the real files are;
        # a plain one lives under the state root and its path says nothing.
        location = f"[dim]→[/dim] {ws['target']}" if ws.get("attached") else "[dim]managed[/dim]"
        table.add_row(
            "[green]*[/green]" if name == active else "",
            name,
            str(ws.get("tasks_count", 0)),
            location,
        )

    console.print(table)
    console.print(f"[dim]{len(workspaces)} workspace(s)[/dim]")
    if active:
        console.print(f"[dim]* selected — from {_workspace_source()}[/dim]")


@workspace_app.command("create")
def workspace_create(
    name: str = typer.Argument(..., help="Workspace name."),
    use: bool = typer.Option(False, "--use", help="Select it for subsequent commands."),
):
    """Create a new workspace under the service's own state directory."""
    result = call(hub().create_workspace, name)
    created = result.get("name", name)
    console.print(f"[green]Workspace created:[/green] [bold]{created}[/bold]")
    if use:
        _write_state(workspace=created, project=None)
        console.print(f"[green]Selected[/green] workspace [bold]{created}[/bold]")


@workspace_app.command("init")
def workspace_init(
    path: Optional[str] = typer.Argument(None, help="Directory to register. Defaults to the current one."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Assert the resulting name (must match the folder)."),
    use: bool = typer.Option(True, "--use/--no-use", help="Select it for subsequent commands."),
):
    """Register an existing directory as a workspace, in place.

    Nothing is copied: the workspace becomes a link to this directory, so agents
    read and write the real files and a git checkout keeps its history.

    The path is resolved by the *backend*. With a local backend that is just this
    machine; if the backend runs in a container, pass a path inside the container
    and bind mount the directory there first.
    """
    target = str(Path(path or os.getcwd()).expanduser().resolve())
    body: dict = {"path": target}
    if name:
        body["name"] = name
    result = call(hub().attach_workspace, body["path"], body.get("name"))
    ws = result.get("name", "")

    console.print(
        f"[green]Workspace[/green] [bold]{ws}[/bold] [green]now points at[/green] {result.get('target', target)}"
    )
    console.print("[dim]Nothing was copied. Detaching later removes only the link.[/dim]")
    if use:
        _write_state(workspace=ws, project=None)
        console.print(f"[green]Selected[/green] workspace [bold]{ws}[/bold]")


@workspace_app.command("use")
def workspace_use(
    name: str = typer.Argument(..., help="Workspace to select."),
):
    """Select a workspace, so later commands act on it without -w."""
    workspaces = call(hub().list_workspaces)
    names = [w.get("name") for w in workspaces]
    if name not in names:
        console.print(f"[red]No workspace '{name}'.[/red] Known: {', '.join(n for n in names if n)}")
        raise typer.Exit(1)
    _write_state(workspace=name, project=None)
    console.print(f"[green]Selected[/green] workspace [bold]{name}[/bold]")
    console.print(f"[dim]Stored in {_state_file()} — one selection per machine. "
                  "For a per-shell one, source [bold]shell-init[/bold] and use the "
                  "function it prints.[/dim]")


@workspace_app.command("current")
def workspace_current():
    """Show the selected workspace, where it came from, and where it points."""
    active = _active_workspace()
    if not active:
        console.print("[yellow]No workspace selected.[/yellow] Pick one with "
                      "[bold]workspace use <name>[/bold], or register a folder with "
                      "[bold]workspace init[/bold].")
        return

    lines = [f"[bold]Workspace:[/bold] {active}", f"[bold]Source:[/bold]    {_workspace_source()}"]
    try:
        match = next((w for w in call(hub().list_workspaces) if w.get("name") == active), None)
    except typer.Exit:
        match = None
    if match is None:
        lines.append("[red]Not present on the backend[/red] — it may have been deleted.")
    elif match.get("attached"):
        lines.append(f"[bold]Points at:[/bold] {match.get('target')}")
    else:
        lines.append("[bold]Points at:[/bold] the service's own state directory")

    project = _active_project()
    if project:
        lines.append(f"[bold]Project:[/bold]   {project}")

    console.print(Panel("\n".join(lines), title="Current", border_style="cyan"))


@workspace_app.command("unuse")
def workspace_unuse():
    """Clear the selected workspace."""
    _write_state(workspace=None, project=None)
    console.print("[green]Cleared[/green] the workspace selection.")
    detected, _ = _detect_from_cwd()
    if detected:
        console.print(f"[dim]This directory still selects [bold]{detected}[/bold] on its own.[/dim]")


@workspace_app.command("detach")
def workspace_detach(
    name: str = typer.Argument(..., help="Attached workspace to unregister."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Unregister an attached workspace. The directory itself is left alone."""
    match = next((w for w in call(hub().list_workspaces) if w.get("name") == name), None)
    if match is None:
        console.print(f"[red]No workspace '{name}'.[/red]")
        raise typer.Exit(1)
    if not match.get("attached"):
        console.print(
            f"[red]'{name}' is not attached[/red] — it is a managed workspace, and detaching "
            "does not apply. Deleting it would erase its contents, which this command will "
            "not do; use the dashboard if that is what you want."
        )
        raise typer.Exit(1)

    target = match.get("target")
    if not yes:
        typer.confirm(f"Detach '{name}'? {target} stays exactly as it is.", abort=True)
    result = call(hub().delete_workspace, name) or {}
    console.print(f"[green]Detached[/green] [bold]{name}[/bold]")
    console.print(f"[dim]Left untouched: {result.get('target_kept') or target}[/dim]")
    if _read_state().get("workspace") == name:
        _write_state(workspace=None, project=None)
        console.print("[dim]It was the selected workspace, so the selection was cleared.[/dim]")

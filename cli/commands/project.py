"""`ah project`: register directories as projects inside a workspace."""
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
    _expand_id,
    _require_workspace,
    _short,
    _write_state,
    call,
    console,
    hub,
)

project_app = typer.Typer(help="Project management commands.", no_args_is_help=True)


@project_app.command("list")
def project_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    all_workspaces: bool = typer.Option(False, "--all", "-a", help="List projects in every workspace."),
):
    """List projects in the selected workspace."""
    projects = call(hub().list_projects)
    ws = None if all_workspaces else _active_workspace(workspace)
    if ws:
        projects = [pr for pr in projects if (pr.get("workspace") or "") == ws]

    active = _active_project()
    table = Table(title="Projects", box=box.ROUNDED)
    table.add_column("", no_wrap=True)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Workspace")
    table.add_column("Tasks", justify="right")
    table.add_column("Repo")

    for pr in projects:
        repo = pr.get("repo") or {}
        repo_type = repo.get("type") or "none"
        pid = str(pr.get("id", ""))
        table.add_row(
            "[green]*[/green]" if pid == active else "",
            pid[:8],
            _short(pr.get("name"), 30),
            pr.get("workspace") or "",
            str(pr.get("tasks_count", 0)),
            "[dim]-[/dim]" if repo_type == "none" else repo_type,
        )

    console.print(table)
    scope = "all workspaces" if not ws else f"workspace '{ws}'"
    console.print(f"[dim]{len(projects)} project(s) in {scope}[/dim]")


@project_app.command("add")
def project_add(
    path: Optional[str] = typer.Argument(None, help="Directory to register. Defaults to the current one."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name. Defaults to the folder's name."),
    description: Optional[str] = typer.Option(None, "--desc", "-d"),
    use: bool = typer.Option(True, "--use/--no-use", help="Select it for subsequent commands."),
):
    """Register an existing directory as a project in a workspace, in place.

    Nothing is copied. If the directory is a git repo, it is recorded as one and
    `git status` / pull work on it immediately — no clone step.
    """
    ws = _require_workspace(workspace)
    target = str(Path(path or os.getcwd()).expanduser().resolve())
    body: dict = {"workspace": ws, "path": target}
    if name:
        body["name"] = name
    if description:
        body["description"] = description

    result = call(hub().attach_project, body)
    pid = str(result.get("id", ""))
    console.print(
        f"[green]Project[/green] [bold]{result.get('name')}[/bold] "
        f"[green]added to workspace[/green] [bold]{ws}[/bold]"
    )
    console.print(f"[dim]Points at {result.get('target', target)} — nothing was copied.[/dim]")
    if result.get("is_git_repo"):
        console.print("[dim]It is a git repo, so its branch and history came along.[/dim]")
    else:
        console.print("[dim]No .git found, so it is registered without a repo.[/dim]")
    if use and pid:
        _write_state(project=pid)
        console.print(f"[green]Selected[/green] project [bold]{pid[:8]}[/bold]")


@project_app.command("get")
def project_get(
    project_id: Optional[str] = typer.Argument(None, help="Project ID (full or prefix). Defaults to the selected one."),
):
    """Show a project, including git state when it has a repo."""
    pid = _active_project(project_id)
    if not pid:
        console.print("[red]No project.[/red] Pass an ID or select one with [bold]project use[/bold].")
        raise typer.Exit(1)

    projects = call(hub().list_projects)
    pid = _expand_id(pid, [str(x.get("id", "")) for x in projects], "project")
    pr = next(x for x in projects if str(x.get("id")) == pid)
    repo = pr.get("repo") or {}

    lines = [
        f"[bold]ID:[/bold]        {pr.get('id')}",
        f"[bold]Name:[/bold]      {pr.get('name')}",
        f"[bold]Workspace:[/bold] {pr.get('workspace')}",
        f"[bold]Type:[/bold]      {pr.get('type') or '-'}",
        f"[bold]Folder:[/bold]    {pr.get('folder') or '-'}",
        f"[bold]Repo:[/bold]      {repo.get('type') or 'none'}"
        + (f" ({repo.get('local_path')})" if repo.get("local_path") else ""),
    ]
    if pr.get("description"):
        lines.append(f"[bold]About:[/bold]     {_short(pr.get('description'), 60)}")

    if (repo.get("type") or "none") != "none":
        try:
            git = hub().project_git_status(pid)
            lines.append(f"[bold]Branch:[/bold]    {git.get('branch') or '-'}")
            status = (git.get("status") or "").strip()
            lines.append(f"[bold]Changes:[/bold]   {len(status.splitlines()) if status else 0} file(s)")
            for commit in (git.get("recent_commits") or "").splitlines()[:3]:
                lines.append(f"  [dim]{commit}[/dim]")
        except typer.Exit:
            # A project can legitimately point at a folder that is not a repo (or
            # not one any more); the rest of the panel is still worth printing.
            lines.append("[dim]Git state unavailable[/dim]")

    console.print(Panel("\n".join(lines), title=f"Project — {pid[:8]}", border_style="magenta"))


@project_app.command("use")
def project_use(
    project_id: str = typer.Argument(..., help="Project ID (full or prefix)."),
):
    """Select a project, so later commands act inside it."""
    projects = call(hub().list_projects)
    pid = _expand_id(project_id, [str(x.get("id", "")) for x in projects], "project")
    pr = next(x for x in projects if str(x.get("id")) == pid)
    _write_state(workspace=pr.get("workspace"), project=pid)
    console.print(
        f"[green]Selected[/green] project [bold]{pr.get('name')}[/bold] "
        f"in workspace [bold]{pr.get('workspace')}[/bold]"
    )


@project_app.command("unuse")
def project_unuse():
    """Clear the selected project, keeping the workspace."""
    _write_state(project=None)
    console.print("[green]Cleared[/green] the project selection.")

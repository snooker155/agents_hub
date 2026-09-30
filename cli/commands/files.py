"""`ah files`: the workspace file registry (docs/files.md)."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.table import Table

from cli.main import _active_workspace, call, console, hub

files_app = typer.Typer(help="Workspace files: the registry the Files page shows.", no_args_is_help=True)


@files_app.command("index")
def files_index(
    workspace: Optional[str] = typer.Argument(None, help="Workspace to index (default: the active one)."),
    all_workspaces: bool = typer.Option(False, "--all", help="Index every workspace folder."),
):
    """Register the files of a workspace folder as workspace files.

    Files agents wrote before the registry followed their tools, or that a
    process outside the tools wrote (Claude Code, a shell), get a record and
    show on the Files page; records of folder files that are gone are marked
    deleted. Hidden entries and version control, cache and build folders are
    skipped. Safe to run again: an unchanged file is left alone.
    """
    if all_workspaces:
        names = [w["name"] for w in call(hub().list_workspaces)]
    else:
        name = workspace or _active_workspace()
        if not name:
            console.print("[red]No workspace given and none is active. Pass a name or run `ah workspace select`.[/red]")
            raise typer.Exit(1)
        names = [name]

    table = Table(title="Workspace files indexed", box=box.ROUNDED)
    table.add_column("Workspace", style="bold cyan")
    for col in ("Added", "Updated", "Unchanged", "Removed", "Skipped"):
        table.add_column(col, justify="right")
    skipped: list = []
    for name in names:
        summary = call(hub().index_workspace_files, name)
        table.add_row(name, str(summary["added"]), str(summary["updated"]), str(summary["unchanged"]),
                      str(summary["removed"]), str(len(summary["skipped"])))
        skipped.extend((name, item["path"], item["reason"]) for item in summary["skipped"])
    console.print(table)
    for name, path, reason in skipped:
        console.print(f"  [yellow]skipped[/yellow] {name}/{path}: {reason}")

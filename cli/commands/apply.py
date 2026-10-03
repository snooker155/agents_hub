"""``ah apply``: declarative files in a repository into hub entities.

Agents, environments, deployments and memory pools are declared in files
(docs/apply.md); ``ah apply <paths>`` prints a plan of what would be created,
updated, left alone or deleted, and then makes the hub match. ``ah.lock``
records the hub id each declared resource got, so a second apply updates it
instead of creating another. ``ah apply --export <agent_id>...`` writes the
same format from records already in the hub.

All the work is in the ``declarative`` package, which talks to the hub only
through ``hub().request``: in process by default, over REST when
``AGENTS_HUB_URL`` is set, the same as every other command.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer

from cli.main import _active_workspace, console, hub

#: Colour of each plan action in the table.
ACTION_STYLES = {
    "create": "green", "update": "yellow", "unchanged": "dim", "delete": "red",
    "forget": "dim", "orphan": "dim", "drift": "magenta", "blocked": "red",
}


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(1)


def _print_plan(p) -> None:
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Action")
    table.add_column("Kind")
    table.add_column("Id")
    table.add_column("Hub id", overflow="fold")
    table.add_column("Details", overflow="fold")
    for c in p.changes:
        style = ACTION_STYLES.get(c.action, "")
        details = c.message
        if c.action in ("update", "drift") and c.fields:
            details = f"fields: {', '.join(c.fields)}" + (f". {c.message}" if c.message else "")
        if c.action in ("drift", "blocked"):
            details = "see below"
        hub_id = c.hub_id or ""
        if hub_id == c.key:
            hub_id = ""  # an agent: the declared id is the hub id
        elif len(hub_id) == 36 and hub_id.count("-") == 4:
            hub_id = hub_id[:8]  # a uuid, in full with --json
        table.add_row(f"[{style}]{c.action}[/{style}]" if style else c.action, c.kind, c.key,
                      hub_id, details)
    if p.changes:
        console.print(table)
    counts = p.counts()
    summary = ", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "nothing declared"
    console.print(f"Plan: {summary}.")
    for line in p.explain().splitlines():
        console.print(f"[red]{line}[/red]", highlight=False)


def _confirm_deletes(p, yes: bool) -> None:
    if not p.deletes or yes:
        return
    names = ", ".join(c.address for c in p.deletes)
    if not sys.stdin.isatty():
        _fail(f"the plan deletes {names}; pass --yes to confirm without a terminal")
    if not typer.confirm(f"Delete {names} from the hub?", default=False):
        console.print("Nothing applied.")
        raise typer.Exit(1)


def _export(refs: List[str], out: str, lock_path: Optional[str], workspace: Optional[str],
            as_json: bool) -> None:
    from declarative import Lock, LockError, adopt, export, load_bundle
    from declarative.errors import ValidationError

    if not refs:
        _fail("name what to export: agent ids, or environment:<id>, memory_pool:<id>, deployment:<id>")
    out_dir = Path(out)
    request = hub().request
    try:
        written = export(request, refs, out_dir, workspace=workspace)
        lock = Lock.load(Path(lock_path) if lock_path else out_dir / "ah.lock")
        bundle = load_bundle([str(p) for p in written.files], base=out_dir)
        owned = adopt(bundle, request, lock, written.ids, workspace=workspace)
        saved = lock.save()
    except (ValueError, LockError, ValidationError) as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - the transport's error, shown as it came
        _fail(str(exc))
    if as_json:
        console.print_json(json.dumps({"files": [str(p) for p in written.files], "lock": str(saved),
                                       "owned": owned}))
        return
    for path in written.files:
        console.print(f"wrote {path}")
    console.print(f"Lock: {saved} now owns {len(owned)} resource(s); `ah apply {out_dir}` plans no change.")


def apply_command(
    paths: List[str] = typer.Argument(
        None, help="Files or folders to apply (default: the current folder). With --export: "
                   "agent ids, or environment:<id>, memory_pool:<id>, deployment:<id>."),
    lock_path: Optional[str] = typer.Option(
        None, "--lock", help="Lock file (default: ah.lock in the folder of the first path)."),
    workspace: Optional[str] = typer.Option(
        None, "--workspace", "-w", help="Workspace for resources that do not name one."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and stop."),
    prune: bool = typer.Option(
        False, "--prune", help="Delete resources the lock owns that are no longer declared."),
    force: bool = typer.Option(
        False, "--force", help="Overwrite resources changed in the hub since the last apply."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before deleting."),
    as_json: bool = typer.Option(False, "--json", help="Print the plan and the result as JSON."),
    export_: bool = typer.Option(
        False, "--export", help="Write declarative files from hub records instead of applying."),
    out: str = typer.Option(".", "--out", "-o", help="With --export: the folder to write into."),
) -> None:
    """Apply declarative files (agents, environments, deployments, memory pools).

    PATHS are files or folders (default: the current folder). With --export they
    are agent ids, or environment:ID, memory_pool:ID, deployment:ID. The file
    format is in docs/apply.md.
    """
    from declarative import DEFAULT_LOCK_NAME, Lock, LockError, apply, load_bundle, plan
    from declarative.errors import PlanBlocked, ValidationError

    ws = _active_workspace(workspace)
    if export_:
        _export(list(paths or []), out, lock_path, ws, as_json)
        return

    targets = list(paths or ["."])
    try:
        bundle = load_bundle(targets)
    except ValidationError as exc:
        for problem in exc.problems:
            console.print(f"[red]{problem}[/red]", highlight=False)
        console.print(f"Nothing applied: {len(exc.problems)} problem(s) in the files.")
        raise typer.Exit(1)
    lock_file = Path(lock_path) if lock_path else (bundle.root or Path(".")) / DEFAULT_LOCK_NAME
    try:
        lock = Lock.load(lock_file)
    except LockError as exc:
        _fail(str(exc))

    request = hub().request
    try:
        p = plan(bundle, request, lock, ws, prune=prune, force=force)
    except Exception as exc:  # noqa: BLE001 - the transport's error, shown as it came
        _fail(f"could not read the hub: {exc}")

    if as_json and (dry_run or not p.ok):
        console.print_json(json.dumps({"plan": p.to_dict()}, default=str))
    elif not as_json:
        _print_plan(p)
    if not p.ok:
        if not as_json:
            console.print("[red]Nothing applied.[/red] " + ("Fix the rows above"
                          + (", or pass --force to overwrite drift." if any(c.action == "drift" for c in p.changes)
                             else ".")))
        raise typer.Exit(1)
    if dry_run:
        return

    _confirm_deletes(p, yes)
    try:
        result = apply(p, request, lock)
    except PlanBlocked as exc:
        _fail(str(exc))
    finally:
        # Saved whatever happened: after a partial failure the lock names
        # exactly what was created, so the next apply does not create it again.
        if p.ok:
            lock.save(lock_file)

    if as_json:
        console.print_json(json.dumps({"plan": p.to_dict(), "result": result.to_dict(),
                                       "lock": str(lock_file)}, default=str))
    else:
        for row in result.failed:
            console.print(f"[red]failed[/red] {row['action']} {row['address']}: {row['error']}",
                          highlight=False)
        done = [r for r in result.applied if r["action"] != "unchanged" and
                (r["action"] != "update" or r["fields"])]
        console.print(f"Applied: {len(done)} change(s), {len(result.failed)} failed. Lock: {lock_file}")
    if not result.ok:
        raise typer.Exit(1)

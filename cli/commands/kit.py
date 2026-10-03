"""``ah kit``: install a ready-made agent kit (``kits/``, docs/kits.md).

A kit is an ``ah apply`` bundle (declarative/, docs/apply.md) plus a manifest;
installing one applies its bundle. ``ah kit install`` runs it through
``hub().request`` the same way ``ah apply`` does: in process by default, over
REST when ``AGENTS_HUB_URL`` is set, using the same per-(kit, workspace) lock
the dashboard's install route uses, so a second install updates the kit's
agents instead of creating a second copy.
"""
from __future__ import annotations

import json
from typing import Optional

import typer

from cli.main import _active_workspace, console, hub

kit_app = typer.Typer(help="List, inspect and install industry agent kits.")


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(1)


@kit_app.command("list")
def kit_list(as_json: bool = typer.Option(False, "--json", help="Print as JSON.")) -> None:
    """List the kits available to install."""
    import kits

    rows = [k.to_dict() for k in kits.list_kits()]
    if as_json:
        console.print_json(json.dumps(rows))
        return
    from rich.table import Table

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Id")
    table.add_column("Name")
    table.add_column("Industry")
    table.add_column("Connectors", overflow="fold")
    for row in rows:
        conns = ", ".join(row["connectors"]["required"]) or "none"
        table.add_row(row["id"], row["name"], row["industry"], conns)
    console.print(table)


@kit_app.command("show")
def kit_show(
    kit_id: str,
    as_json: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """Show one kit's manifest and the resources its bundle declares."""
    import kits

    kit = kits.get_kit(kit_id)
    if kit is None:
        _fail(f"Unknown kit '{kit_id}'")
    bundle = kits.load_kit_bundle(kit)
    if as_json:
        console.print_json(json.dumps({**kit.to_dict(),
                                       "resources": [[r.kind, r.key] for r in bundle.resources]}))
        return
    console.print(f"[bold]{kit.name}[/bold] ({kit.id}): {kit.industry}")
    console.print(kit.description)
    console.print(f"Connectors: required {', '.join(kit.connectors_required) or 'none'}, "
                  f"optional {', '.join(kit.connectors_optional) or 'none'}.")
    for r in bundle.resources:
        console.print(f"  {r.kind}/{r.key}")
    if kit.rubrics:
        console.print(f"\nRubrics: {kit.rubrics}")
    if kit.next_steps:
        console.print(f"\nNext steps: {kit.next_steps}")


@kit_app.command("install")
def kit_install(
    kit_id: str,
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace to install into."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and stop."),
    as_json: bool = typer.Option(False, "--json", help="Print the plan and the result as JSON."),
) -> None:
    """Install a kit's agents, memory pool and deployment into a workspace."""
    import kits
    from declarative import Lock, LockError, apply, plan
    from declarative.errors import PlanBlocked, ValidationError

    kit = kits.get_kit(kit_id)
    if kit is None:
        _fail(f"Unknown kit '{kit_id}'")
    ws = _active_workspace(workspace)
    try:
        bundle = kits.namespaced_bundle(kits.load_kit_bundle(kit), ws)
    except ValidationError as exc:
        for problem in exc.problems:
            console.print(f"[red]{problem}[/red]", highlight=False)
        _fail(f"{len(exc.problems)} problem(s) in the kit's files.")

    lock_file = kits.lock_path(kit.id, ws)
    try:
        lock = Lock.load(lock_file)
    except LockError as exc:
        _fail(str(exc))

    request = hub().request
    try:
        p = plan(bundle, request, lock, ws)
    except Exception as exc:  # noqa: BLE001 - the transport's error, shown as it came
        _fail(f"could not read the hub: {exc}")

    if as_json and (dry_run or not p.ok):
        console.print_json(json.dumps({"plan": p.to_dict()}, default=str))
    elif not as_json:
        for c in p.changes:
            console.print(f"{c.action:10} {c.kind}/{c.key} -> {c.hub_id or ''}")
        counts = p.counts()
        console.print("Plan: " + (", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "nothing to do") + ".")
        for line in p.explain().splitlines():
            console.print(f"[red]{line}[/red]", highlight=False)
    if not p.ok:
        if not as_json:
            console.print("[red]Nothing installed.[/red] Fix the rows above.")
        raise typer.Exit(1)
    if dry_run:
        return

    try:
        result = apply(p, request, lock)
    except PlanBlocked as exc:
        _fail(str(exc))
    finally:
        if p.ok:
            lock.save(lock_file)

    if as_json:
        console.print_json(json.dumps({"plan": p.to_dict(), "result": result.to_dict()}, default=str))
    else:
        for row in result.failed:
            console.print(f"[red]failed[/red] {row['action']} {row['address']}: {row['error']}",
                          highlight=False)
        changed = [r for r in result.applied if r["action"] != "unchanged"]
        console.print(f"Installed '{kit.id}' into workspace '{ws or 'default'}': "
                      f"{len(changed)} change(s), {len(result.failed)} failed.")
        if kit.next_steps:
            console.print(f"\n{kit.next_steps}")
    if not result.ok:
        raise typer.Exit(1)

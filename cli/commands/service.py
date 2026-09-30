"""`ah service`: agents kept running as replicas (docs/services.md)."""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from cli.main import _active_workspace, call, console, hub

service_app = typer.Typer(help="Services: agents kept running as replicas, and the runners chat turns go to.",
                          no_args_is_help=True)


def _resolve_service_id(service_id: str) -> str:
    from cli.main import _expand_id
    services = call(hub().list_services)
    return _expand_id(service_id, [str(s.get("service_id", "")) for s in services], "service")


def _replicas_cell(svc: dict) -> str:
    rep = svc.get("replicas") or {}
    return f"{rep.get('live', 0)} live / {svc.get('replicas_min', 0)}–{svc.get('replicas_max', 0)}"


@service_app.command("list")
def service_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
    all_workspaces: bool = typer.Option(False, "--all", help="Every workspace, not only the selected one."),
):
    """List services."""
    workspace = None if all_workspaces else _active_workspace(workspace)
    services = call(hub().list_services, workspace)

    table = Table(title="Services", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Name", style="bold")
    table.add_column("Agent")
    table.add_column("Workspace")
    table.add_column("Status", no_wrap=True)
    table.add_column("Replicas")
    table.add_column("Public")
    for s in services:
        status = str(s.get("status") or "")
        table.add_row(
            str(s.get("service_id", ""))[:8],
            s.get("name") or "",
            s.get("agent_id") or "[dim]runner[/dim]",
            s.get("workspace") or "",
            Text(status, style="green" if status == "active" else "yellow"),
            _replicas_cell(s),
            "yes" if s.get("is_exposed") else "",
        )
    console.print(table)
    console.print(f"[dim]{len(services)} service(s)[/dim]")


@service_app.command("create")
def service_create(
    agent_id: Optional[str] = typer.Argument(None, help="Agent to deploy; omit for a runner."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    environment: Optional[str] = typer.Option(None, "--environment", "-e", help="Environment id."),
    replicas_min: int = typer.Option(1, "--min", help="Replicas kept running."),
    replicas_max: int = typer.Option(1, "--max", help="Replicas at most."),
    concurrency: int = typer.Option(4, "--concurrency", "-c", help="Conversations a replica answers at once."),
    take_tasks: bool = typer.Option(False, "--take-tasks", help="Replicas also take the agent's tasks."),
    idle_stop: int = typer.Option(600, "--idle-stop", help="Seconds before an idle replica beyond the minimum stops."),
    budget: Optional[float] = typer.Option(None, "--budget", help="Money cap per turn, USD."),
    version: Optional[int] = typer.Option(None, "--version", help="Agent version to pin."),
    publish: bool = typer.Option(False, "--publish", help="Give the service a public address."),
):
    """Deploy an agent as a service (or a runner, without an agent)."""
    workspace = _active_workspace(workspace)
    body: dict = {
        "agent_id": agent_id, "workspace": workspace, "name": name, "environment_id": environment,
        "replicas_min": replicas_min, "replicas_max": max(replicas_max, replicas_min),
        "concurrency": concurrency, "take_tasks": take_tasks, "idle_stop_seconds": idle_stop,
        "budget_usd": budget, "agent_version": version, "publish": publish,
    }
    svc = call(hub().create_service, {k: v for k, v in body.items() if v is not None})
    console.print(f"[green]Service created:[/green] [bold]{str(svc.get('service_id', ''))[:8]}[/bold]"
                  f"  {svc.get('name')}  ({_replicas_cell(svc)})")
    if svc.get("external_url"):
        console.print(f"[dim]Public address:[/dim] {svc['external_url']}")


@service_app.command("show")
def service_show(service_id: str = typer.Argument(..., help="Service id (full or prefix).")):
    """Show a service, its replicas and the supervisor's last events."""
    service_id = _resolve_service_id(service_id)
    svc = call(hub().get_service, service_id)
    console.print(f"[bold]{svc.get('name')}[/bold]  [dim]{svc.get('service_id')}[/dim]")
    console.print(f"agent: {svc.get('agent_id') or 'runner'}   workspace: {svc.get('workspace')}   "
                  f"environment: {svc.get('environment_name') or '-'}")
    console.print(f"status: {svc.get('status')}"
                  + (f" ({svc.get('paused_reason')})" if svc.get("paused_reason") else "")
                  + f"   replicas: {_replicas_cell(svc)}   concurrency: {svc.get('concurrency')}"
                  f"   idle stop: {svc.get('idle_stop_seconds')}s")
    if svc.get("external_url"):
        console.print(f"public: {svc['external_url']}")
    replicas = call(hub().list_service_replicas, service_id)
    table = Table(title="Replicas", box=box.SIMPLE)
    table.add_column("ID", style="dim", max_width=10)
    table.add_column("Label")
    table.add_column("State")
    table.add_column("Carrier")
    table.add_column("Runs", justify="right")
    for r in replicas:
        table.add_row(str(r.get("instance_id", ""))[:8], r.get("label") or "", r.get("state") or "",
                      f"{r.get('carrier_mode') or ''} {r.get('pid') or r.get('container_name') or ''}".strip(),
                      str(r.get("runs_count") or 0))
    console.print(table)
    events = call(hub().service_events, service_id)
    for ev in events[:10]:
        console.print(f"[dim]{(ev.get('at') or '')[:19].replace('T', ' ')}[/dim] {ev.get('kind')}: {ev.get('detail')}")


@service_app.command("scale")
def service_scale(
    service_id: str = typer.Argument(..., help="Service id (full or prefix)."),
    replicas_min: Optional[int] = typer.Option(None, "--min"),
    replicas_max: Optional[int] = typer.Option(None, "--max"),
    concurrency: Optional[int] = typer.Option(None, "--concurrency", "-c"),
    idle_stop: Optional[int] = typer.Option(None, "--idle-stop"),
):
    """Change how many replicas a service keeps and may grow to."""
    service_id = _resolve_service_id(service_id)
    body = {"replicas_min": replicas_min, "replicas_max": replicas_max,
            "concurrency": concurrency, "idle_stop_seconds": idle_stop}
    body = {k: v for k, v in body.items() if v is not None}
    if not body:
        console.print("[yellow]Nothing to change.[/yellow]")
        raise typer.Exit(1)
    svc = call(hub().update_service, service_id, body)
    console.print(f"[green]Updated[/green] {svc.get('name')}: {_replicas_cell(svc)}")


@service_app.command("pause")
def service_pause(service_id: str = typer.Argument(...)):
    """Stop every replica and start none until resumed."""
    service_id = _resolve_service_id(service_id)
    call(hub().pause_service, service_id)
    console.print(f"[yellow]Paused[/yellow] service [bold]{service_id[:8]}[/bold]")


@service_app.command("resume")
def service_resume(service_id: str = typer.Argument(...)):
    """Resume a paused service."""
    service_id = _resolve_service_id(service_id)
    call(hub().resume_service, service_id)
    console.print(f"[green]Resumed[/green] service [bold]{service_id[:8]}[/bold]")


@service_app.command("delete")
def service_delete(service_id: str = typer.Argument(...),
                   yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask.")):
    """Remove a service; its replicas are stopped."""
    service_id = _resolve_service_id(service_id)
    if not yes and not typer.confirm(f"Delete service {service_id[:8]} and stop its replicas?"):
        raise typer.Exit(0)
    call(hub().delete_service, service_id)
    console.print(f"[red]Deleted[/red] service [bold]{service_id[:8]}[/bold]")


@service_app.command("publish")
def service_publish(service_id: str = typer.Argument(...),
                    withdraw: bool = typer.Option(False, "--withdraw", help="Take the address away.")):
    """Give a service a public address through the hub (or withdraw it)."""
    service_id = _resolve_service_id(service_id)
    svc = call(hub().unpublish_service if withdraw else hub().publish_service, service_id)
    if withdraw:
        console.print(f"[yellow]Withdrawn[/yellow] the public address of [bold]{service_id[:8]}[/bold]")
    else:
        console.print(f"[green]Published[/green] {svc.get('name')}: {svc.get('external_url') or svc.get('expose_token')}")

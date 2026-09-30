"""`ah instance`: start, stop, restart, and message resident agent instances."""
from __future__ import annotations

import time
from typing import Optional

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from cli.main import _active_workspace, _instance_color, _resolve_instance_id, call, console, hub

instance_app = typer.Typer(help="Resident instance management commands.", no_args_is_help=True)


@instance_app.command("list")
def instance_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
):
    """List all resident instances."""
    workspace = _active_workspace(workspace)
    instances = call(hub().list_instances, workspace)

    table = Table(title="Instances", box=box.ROUNDED)
    table.add_column("ID", style="dim", no_wrap=True, max_width=10)
    table.add_column("Agent", style="bold")
    table.add_column("State", no_wrap=True)
    table.add_column("Workspace")
    table.add_column("Started")

    for i in instances:
        iid = str(i.get("instance_id", ""))[:8]
        s = i.get("state", "")
        color = _instance_color(s)
        started = (i.get("started_at") or "")[:16].replace("T", " ")
        table.add_row(
            iid,
            i.get("agent_name") or i.get("agent_id", ""),
            Text(s, style=color),
            i.get("workspace") or "",
            started,
        )

    console.print(table)
    console.print(f"[dim]{len(instances)} instance(s)[/dim]")


@instance_app.command("start")
def instance_start(
    agent_id: str = typer.Argument(..., help="Agent ID to start a resident instance for."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Defaults to the selected workspace."),
    label: Optional[str] = typer.Option(None, "--label", "-l"),
):
    """Start a resident instance for an agent: a process of its own that
    answers its mailbox until stopped."""
    workspace = _active_workspace(workspace)
    body: dict = {"agent_id": agent_id}
    if workspace:
        body["workspace"] = workspace
    if label:
        body["label"] = label
    instance = call(hub().start_instance, body)
    console.print(
        f"[green]Instance started:[/green] [bold]{str(instance.get('instance_id', ''))[:8]}[/bold]"
        f"  agent={agent_id}"
    )


@instance_app.command("stop")
def instance_stop(
    instance_id: str = typer.Argument(..., help="Instance ID to stop (full or prefix)."),
):
    """Stop a resident instance's process. It keeps its conversations and can
    be started again."""
    instance_id = _resolve_instance_id(instance_id)
    call(hub().stop_instance, instance_id)
    console.print(f"[yellow]Stopped[/yellow] instance [bold]{instance_id[:8]}[/bold]")


@instance_app.command("restart")
def instance_restart(
    instance_id: str = typer.Argument(..., help="Instance ID to restart (full or prefix)."),
):
    """Replace a resident instance's process with a fresh one, or start a
    stopped one again. Conversations and runs stay."""
    instance_id = _resolve_instance_id(instance_id)
    call(hub().restart_instance, instance_id)
    console.print(f"[green]Restarted[/green] instance [bold]{instance_id[:8]}[/bold]")


@instance_app.command("logs")
def instance_logs(
    instance_id: str = typer.Argument(..., help="Instance ID (full or prefix)."),
):
    """Print the tail of a resident instance's carrier log."""
    instance_id = _resolve_instance_id(instance_id)
    result = call(hub().instance_logs, instance_id)
    text = (result or {}).get("logs") or ""
    if not text:
        console.print("[dim](no logs yet)[/dim]")
        return
    console.print(text, markup=False, highlight=False)


@instance_app.command("message")
def instance_message(
    instance_id: str = typer.Argument(..., help="Instance ID (full or prefix)."),
    message: str = typer.Argument(..., help="The message to send."),
    conversation: Optional[str] = typer.Option(
        None, "--conversation", "-c",
        help="Conversation of the instance to write to; the main one when omitted."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between polls."),
    timeout: float = typer.Option(120.0, "--timeout", help="Seconds to wait for a reply."),
):
    """Send a message to a resident instance's mailbox and wait for its reply."""
    instance_id = _resolve_instance_id(instance_id)
    sent = call(hub().send_instance_message, instance_id, message, conversation)
    msg_id = sent.get("msg_id")
    if not msg_id:
        console.print(f"[yellow]{sent.get('mode', 'sent')}[/yellow]: {sent}")
        return

    deadline = time.monotonic() + timeout
    reply: dict = {}
    while time.monotonic() < deadline:
        reply = call(hub().get_instance_message, instance_id, msg_id)
        if reply.get("status") in ("completed", "failed", "stopped"):
            break
        time.sleep(poll_interval)

    status = reply.get("status")
    if status == "completed":
        console.print(reply.get("output") or "")
    elif status in ("failed", "stopped"):
        console.print(f"[red]{status}[/red]: {reply.get('error') or ''}")
        raise typer.Exit(1)
    else:
        console.print(f"[yellow]Timed out waiting for a reply[/yellow] (status: {status or 'unknown'})")
        raise typer.Exit(1)

"""`ah user`: accounts, over `/api/auth/users`.

Users exist only under ``AUTH_MODE=multi`` (dashboard/backend/routes/auth.py,
``_require_multi``): in ``single`` and ``token`` mode there is one operator
and nobody to administer, so the API answers 404 rather than an empty list,
and that is what every command below surfaces.
"""
from __future__ import annotations

from typing import Optional

import typer
from rich import box
from rich.table import Table

from cli.main import call, console, hub
from cli.openapi import render_result

user_app = typer.Typer(help="User account commands (AUTH_MODE=multi only).", no_args_is_help=True)


@user_app.command("list")
def user_list(json_out: bool = typer.Option(False, "--json", help="Print JSON.")):
    """List every account."""
    users = call(hub().request, "GET", "/api/auth/users") or []
    if json_out:
        render_result(console, users, as_json=True)
        return
    table = Table(title="Users", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Username", style="bold")
    table.add_column("Role")
    table.add_column("Display name")
    table.add_column("Disabled")
    for u in users:
        table.add_row(str(u.get("id", "")), u.get("username", ""), u.get("role", ""),
                      u.get("display_name") or "", "yes" if u.get("disabled") else "")
    console.print(table)
    console.print(f"[dim]{len(users)} user(s)[/dim]")


@user_app.command("create")
def user_create(
    username: str = typer.Argument(..., help="Unique login name."),
    password: Optional[str] = typer.Option(
        None, "--password", help="Prompted for, hidden, when omitted. Leave unset for SSO-only."),
    role: str = typer.Option("member", "--role", help="member or admin."),
    display_name: str = typer.Option("", "--display-name"),
    email: str = typer.Option("", "--email"),
    no_password: bool = typer.Option(
        False, "--no-password", help="Create without one: the account signs in only through SSO until set."),
):
    """Add an account."""
    if not no_password and password is None:
        password = typer.prompt("Password (leave empty for SSO-only)", hide_input=True, default="", show_default=False)
    body = {"username": username, "password": (password or None) if not no_password else None,
            "role": role, "display_name": display_name, "email": email}
    user = call(hub().request, "POST", "/api/auth/users", json=body)
    console.print(f"[green]Created[/green] user [bold]{user.get('username')}[/bold] "
                  f"(id {user.get('id')}, role {user.get('role')})")


@user_app.command("disable")
def user_disable(
    user_id: str = typer.Argument(..., help="User id, from `ah user list`."),
    enable: bool = typer.Option(False, "--enable", help="Re-enable instead of disabling."),
):
    """Disable (or, with --enable, re-enable) an account. Drops its sessions and keys at once."""
    user = call(hub().request, "PATCH", f"/api/auth/users/{user_id}", json={"disabled": not enable})
    state = "enabled" if enable else "disabled"
    console.print(f"[green]{state.capitalize()}[/green] user [bold]{user.get('username', user_id)}[/bold]")


@user_app.command("set-role")
def user_set_role(
    user_id: str = typer.Argument(..., help="User id, from `ah user list`."),
    role: str = typer.Argument(..., help="member or admin."),
):
    """Change an account's global role."""
    user = call(hub().request, "PATCH", f"/api/auth/users/{user_id}", json={"role": role})
    console.print(f"[green]Set[/green] [bold]{user.get('username', user_id)}[/bold] to role [bold]{role}[/bold]")

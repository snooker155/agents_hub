"""`ah costs`: the spend report from a terminal (routes/accounting.py,
docs/costs.md "Report").

Goes through ``hub().request``, the generic transport every entity group
already reuses (cli/backend.py): one route, no new backend method, works the
same in direct mode and over ``AGENTS_HUB_URL``. CSV is built here from the
same JSON rows the table renders, rather than asking the route for the CSV
response directly — the generic transport only ever returns JSON.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.table import Table

from cli.main import call, console, hub

costs_app = typer.Typer(help="Spend by API key, user, project and workspace.", no_args_is_help=True)

GROUP_BY_CHOICES = ("key", "user", "project", "workspace", "agent", "model")

_CSV_COLUMNS = ("key", "label", "runs", "calls", "inbound_tokens", "cached_tokens",
                "outbound_tokens", "total_tokens", "cost")


def _fmt_usd(value) -> str:
    return f"${float(value or 0):.2f}"


@costs_app.command("report")
def costs_report(
    by: str = typer.Option("user", "--by", help=f"Group by: {', '.join(GROUP_BY_CHOICES)}."),
    since: Optional[str] = typer.Option(
        None, "--since", help="ISO date/time; only spend on or after it."),
    until: Optional[str] = typer.Option(
        None, "--until", help="ISO date/time; only spend on or before it."),
    workspace: Optional[str] = typer.Option(
        None, "--workspace", "-w", help="One workspace only (runs; a served /v1 call carries none)."),
    as_csv: bool = typer.Option(False, "--csv", help="Print as CSV instead of a table."),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Write CSV to this file instead of printing it."),
):
    """Spend grouped by key, user, project, workspace, agent or model:
    runs plus served /v1 calls, evaluation channels excluded. A
    non-administrator in multi mode sees only their own rows."""
    if by not in GROUP_BY_CHOICES:
        console.print(f"[red]--by must be one of: {', '.join(GROUP_BY_CHOICES)}[/red]")
        raise typer.Exit(1)

    params = {"group_by": by}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    if workspace:
        params["workspace"] = workspace
    result = call(hub().request, "GET", "/api/accounting/report", params=params)
    rows = result.get("rows") or []
    totals = result.get("totals") or {}

    if as_csv or out is not None:
        target = sys.stdout if out is None else open(out, "w", newline="", encoding="utf-8")
        try:
            writer = csv.writer(target)
            writer.writerow(_CSV_COLUMNS)
            for row in rows:
                writer.writerow([row.get(c) for c in _CSV_COLUMNS])
        finally:
            if out is not None:
                target.close()
        if out is not None:
            console.print(f"[green]wrote[/green] {out}")
        return

    table = Table(title=f"Spend by {by}", box=box.ROUNDED)
    table.add_column("Label", style="bold")
    table.add_column("Runs", justify="right")
    table.add_column("Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right", style="green")
    for row in rows:
        table.add_row(
            str(row.get("label") or row.get("key") or "-"),
            str(row.get("runs", 0)), str(row.get("calls", 0)),
            str(row.get("total_tokens", 0)), _fmt_usd(row.get("cost")))
    console.print(table)
    console.print(
        f"[dim]{totals.get('runs', 0)} run(s), {totals.get('calls', 0)} call(s), "
        f"{totals.get('total_tokens', 0)} tokens, {_fmt_usd(totals.get('cost'))} total[/dim]")

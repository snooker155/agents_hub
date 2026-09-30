"""`ah support-bundle`: one archive to attach to a support request.

Works two ways, like every other command in this package (cli/main.py's
``hub()``): direct mode builds the bundle in process
(``common.support_bundle.build``), no server needed; with ``AGENTS_HUB_URL``
set it downloads ``GET /api/support/bundle`` from the running backend instead,
so the bytes are always exactly what the dashboard's own "Download support
bundle" button would fetch. Not routed through ``cli.backend``'s generic
``request()``: that helper only ever returns parsed JSON, and a zip is not
JSON.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import typer

from cli.main import console

_SINCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([hmd]?)$", re.IGNORECASE)
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "d": 86400.0, "": 3600.0}


def _parse_since(raw: str) -> float:
    """``"24h"``, ``"90m"``, ``"2d"`` or a bare number of hours, as seconds."""
    match = _SINCE_RE.match(raw.strip())
    if not match:
        console.print(f"[red]Error:[/red] --since {raw!r} is not understood; try '24h', '90m' or '2d'.")
        raise typer.Exit(1)
    value, unit = match.groups()
    return float(value) * _UNIT_SECONDS[unit.lower()]


def _direct_bundle(since_seconds: float, error_limit: int) -> bytes:
    from cli.backend import _ensure_importable
    _ensure_importable()
    from common.support_bundle import build
    return build(since_seconds=since_seconds, error_limit=error_limit)


def _remote_bundle(base_url: str, since_seconds: float, error_limit: int) -> bytes:
    import requests
    from common.auth import auth_headers

    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/api/support/bundle",
            params={"since": since_seconds, "error_limit": error_limit},
            headers=auth_headers(), timeout=60,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        console.print(f"[red]Error:[/red] cannot reach the backend at {base_url}. "
                      "Start it with `ah server start`, or unset AGENTS_HUB_URL to build the "
                      "bundle in process.")
        raise typer.Exit(1)
    except requests.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except ValueError:  # not JSON: keep the HTTP error text
            detail = str(e)
        console.print(f"[red]Error:[/red] {detail}")
        raise typer.Exit(1)
    return resp.content


def support_bundle(
    out: Optional[Path] = typer.Option(None, "--out", help="Zip path. Defaults to the current directory."),
    since: str = typer.Option("24h", "--since", help="How far back errors.json looks: '24h', '90m', '2d'."),
    error_limit: int = typer.Option(200, "--error-limit", help="Failed runs to include at most."),
) -> None:
    """Write a support bundle: version, doctor, health, migrations, config
    (secrets reduced to "is it set"), recent errors, SLO status and a log
    tail, one zip, no secret survives (common/support_bundle.py's scrubber).
    """
    since_seconds = _parse_since(since)
    url = os.environ.get("AGENTS_HUB_URL", "").strip()
    if url:
        data = _remote_bundle(url, since_seconds, error_limit)
    else:
        data = _direct_bundle(since_seconds, error_limit)

    if out is None:
        from common.support_bundle import default_filename
        out = Path.cwd() / default_filename()
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    console.print(f"[green]Wrote[/green] {out} ({len(data):,} bytes)")


__all__ = ["support_bundle"]

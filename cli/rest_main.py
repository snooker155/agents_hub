"""
The entry point for the REST-only build of `ah` (scripts/build_cli_binary.sh).

A PyInstaller binary built from this module bundles the CLI, typer, rich and
requests, and nothing from the service itself: no agents, no tasks, no
database driver, none of it. That build only ever talks to a backend over
AGENTS_HUB_URL (HttpBackend, cli/backend.py); it cannot run in direct mode,
because direct mode *is* the service, and the service is exactly what this
build leaves out.

The check below runs before ``cli.main`` is imported at all, on purpose: it
guarantees that an unset AGENTS_HUB_URL never gets anywhere near
``DirectBackend`` (whose methods import the service, lazily, one call at a
time) and instead ends in one clear message. The ordinary ``ah`` — the one
``./install.sh`` puts on PATH, cli/__main__.py and agents_hub/__init__.py —
has no such check and keeps defaulting to direct mode, because it is run from
a checkout that has the service to run.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    # --help (and no arguments at all, which typer's no_args_is_help turns
    # into the same thing) must work with nothing configured: it is how
    # someone new to this build finds out AGENTS_HUB_URL is what it needs,
    # and a build that cannot even print its own usage is a bad first
    # impression of a tool whose entire point is being easy to hand someone.
    asks_for_help = len(sys.argv) == 1 or any(a in ("-h", "--help") for a in sys.argv[1:])
    if not asks_for_help and not os.environ.get("AGENTS_HUB_URL", "").strip():
        sys.stderr.write(
            "AGENTS_HUB_URL is not set.\n\n"
            "This build of `ah` talks to a running Agents Hub backend over REST\n"
            "only; it does not bundle the service itself, so it cannot run in\n"
            "direct mode. Point it at a backend and try again:\n\n"
            "    export AGENTS_HUB_URL=http://localhost:8000\n"
            "    ah agent list\n\n"
            "For direct, in-process mode (no server needed), install from a\n"
            "checkout instead: ./install.sh (see docs/installation.md).\n"
        )
        raise SystemExit(1)

    from cli.main import app
    app()


if __name__ == "__main__":
    main()

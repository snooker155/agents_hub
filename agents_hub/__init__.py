"""The installed entry point for the terminal client.

``pip install -e .`` adds exactly two things: this package, and an ``agents-hub``
command on PATH. The service's own modules are deliberately *not* installed —
top-level names like ``agents``, ``tasks`` and ``tools`` would shadow unrelated
distributions and be shadowed by them (``agents`` is the OpenAI SDK's name, and
it is commonly already present). They are imported from the checkout instead,
which is where the service reads its agent definitions, seed catalog and state
root from in any case.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The checkout this package was installed from: <repo>/agents_hub/__init__.py.
# Computed from __file__, not imported from common.paths: this is the very
# first code that runs for the ``agents-hub`` console script, before the repo
# root is on sys.path, so common.paths is not importable yet. ``main()`` below
# puts the root on sys.path first, so everything downstream imports
# common.paths.PROJECT_ROOT (the same value) instead of recomputing it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["PROJECT_ROOT", "main"]


def main() -> None:
    """Run the CLI out of the checkout, whatever the working directory."""
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        # Ahead of site-packages on purpose: the checkout is the service, and a
        # same-named distribution must not answer for it.
        sys.path.insert(0, root)
    from cli.main import app

    app()

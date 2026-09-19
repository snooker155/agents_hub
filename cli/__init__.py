"""The terminal client.

Two modules, split by what they are rather than by size: ``main.py`` is the
Typer application — every command, and the terminal rendering around it — and
``backend.py`` is the layer underneath, which either calls the service in this
process or talks to one over REST.

This file stays empty on purpose. Importing ``cli`` must stay free, because
``cli/main.py`` is also run as a script (that is the fallback the shell
integration bakes in when no console script is on PATH), and an ``__init__``
that pulled the app in would load it a second time under another name. Import
the module you want: ``from cli.main import app``, or ``python -m cli``.
"""

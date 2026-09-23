"""
Entity command groups, one module per entity.

Each module builds a ``typer.Typer`` (or, for `api.py`, a single command) and
imports the shared infrastructure, workspace/project selection, `hub()`,
`call()`, id resolution, from ``cli.main``. ``cli.main`` imports every module
here at the bottom of its own definitions and registers what each one builds
onto the main app, so this package never constructs the app itself.
"""

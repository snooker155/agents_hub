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
    if any(a.startswith(_COMPLETION_FLAGS) for a in sys.argv[1:]):
        _shell_detection_fallback()
    from cli.main import app

    app()


_COMPLETION_FLAGS = ("--install-completion", "--show-completion")
_SUPPORTED_SHELLS = ("bash", "zsh", "fish", "powershell", "pwsh")


def _shell_detection_fallback() -> None:
    """Make ``--install-completion`` find the shell typer cannot.

    Typer asks shellingham, which walks the process tree with ``ps``. Under some
    terminals (IDE shells, sandboxes, ``conda run``) it finds nothing and raises
    ShellDetectionFailure, or finds the ``sh`` wrapper the real shell was
    started from, which typer rejects. Then the answer comes from, in order: a
    shell named after the flag (``ah --install-completion zsh``, which typer
    alone refuses while shellingham is installed), ``$SHELL``, and the login
    shell in the user database.
    """
    try:
        import shellingham
    except ImportError:
        return
    import os

    explicit = _pop_explicit_shell()
    detect = shellingham.detect_shell

    def fallback():
        candidates = [explicit, os.environ.get("SHELL", "")]
        try:
            import pwd

            candidates.append(pwd.getpwuid(os.getuid()).pw_shell)
        except (ImportError, KeyError):
            pass
        for path in candidates:
            name = os.path.basename(path or "")
            if name in _SUPPORTED_SHELLS:
                return name, path
        return None

    def detect_shell(pid=None, max_depth=10):
        if explicit:
            return explicit, explicit
        try:
            found = detect(pid, max_depth)
        except shellingham.ShellDetectionFailure:
            found = None
        if found and found[0] in _SUPPORTED_SHELLS:
            return found
        chosen = fallback()
        if chosen:
            return chosen
        if found:
            return found
        raise shellingham.ShellDetectionFailure()

    shellingham.detect_shell = detect_shell


def _pop_explicit_shell() -> str:
    """Take ``zsh`` out of ``--install-completion zsh`` (or ``=zsh``)."""
    args = sys.argv
    for i, arg in enumerate(args[1:], start=1):
        flag, eq, value = arg.partition("=")
        if flag not in _COMPLETION_FLAGS:
            continue
        if eq:
            args[i] = flag
            return value
        if i + 1 < len(args) and args[i + 1] in _SUPPORTED_SHELLS:
            return args.pop(i + 1)
    return ""

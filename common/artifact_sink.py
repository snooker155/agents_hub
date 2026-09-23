"""
Artifact sink — a context-var channel for reporting file changes.

The filesystem tool wrappers mutate files (write/create/delete/apply_diff).
When an agent runs inside the chat pipeline we want to surface each change as a
git-style diff in the UI. Rather than coupling the low-level tools to the web
layer, the chat callback installs a recording callable on a ContextVar for the
duration of a run; the tools call ``record_artifact(...)`` if (and only if) a
sink is present. Outside the chat pipeline (CLI, worker subprocesses, tests)
the ContextVar is empty and the tools no-op.

The recorder receives raw before/after text and decides how to build the diff,
so all diff/formatting policy lives in one place (the chat route).
"""
from __future__ import annotations

import contextvars
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

# A callable(op: str, path: str, before: str | None, after: str | None) -> None.
# ``before``/``after`` are the file's full text content before and after the op;
# ``None`` means "did not exist" (add has before=None, delete has after=None).
ArtifactRecorder = Callable[[str, str, Optional[str], Optional[str]], None]

_artifact_recorder: contextvars.ContextVar[Optional[ArtifactRecorder]] = contextvars.ContextVar(
    "artifact_recorder", default=None
)


def set_recorder(recorder: Optional[ArtifactRecorder]):
    """Install a recorder for the current context. Returns the reset token."""
    return _artifact_recorder.set(recorder)


def reset_recorder(token) -> None:
    """Restore the previous recorder using a token from :func:`set_recorder`."""
    try:
        _artifact_recorder.reset(token)
    except (ValueError, RuntimeError):
        pass


def record_artifact(op: str, path: str, before: Optional[str], after: Optional[str]) -> None:
    """Report a file change to the active recorder, if any. Never raises."""
    recorder = _artifact_recorder.get()
    if recorder is None:
        return
    try:
        recorder(op, path, before, after)
    except Exception:  # noqa: BLE001 - artifact reporting is best-effort; never let it break a tool call
        log.debug("artifact_sink: record failed for %s %s", op, path, exc_info=True)


__all__ = ["ArtifactRecorder", "set_recorder", "reset_recorder", "record_artifact"]

"""
The first run: the install's own setup, done once in the browser before the
rest of the app opens (docs/installation.md, "The first run in the browser").

A full screen of its own, step by step: the language, light or dark, a model
to think with and which one, personal memory, the demo workspace. Only the
model is required; every other step has a "not now". There is no way past the
whole of it, and once finished it does not come back (Settings, First setup,
starts it again on purpose).

**One per install.** The record is one document, ``install``, in the
``first_run`` collection.

**An install that is already in use is never stopped.** The first time the
record is read and there is none, the hub looks for signs of use (a chat
outside the demo workspace) and, finding any, writes the first run as finished
by the upgrade. That check runs once: a fresh install writes an empty record
and is asked from then on.

**What the steps change is written where the pages write it.** The model and
the demo go through common/setup_ops.py, personal memory through the
workspace's own route. The record keeps only what no page holds: the step the
person is on, so a reload goes on from there, and the language and the look
they chose, so a second browser starts with them.

**A screen whose setting is already in force shows it instead of asking.**
``ah setup`` can set the model and the demo before the hub first starts;
``context()["configured"]`` reads where each of them is stored (no marker of
its own) so the browser can say "already set up" with the value, and offer
Continue or Change.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("first_run")
_KEY = "install"

#: The screens, in order. The browser draws them; the server only checks a
#: saved step is one of them.
STEPS = ("hello", "language", "appearance", "model", "choose_model", "memory", "demo", "done")
LANGUAGES = ("en", "ru", "de")
THEMES = ("light", "dark", "system")
ACTIONS = ("progress", "finish", "restart")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def applies() -> bool:
    """True where the first run is the way in: everything but ``multi`` mode."""
    from common import identity
    from common.auth import MULTI
    try:
        return identity.current_mode() != MULTI
    except Exception:  # noqa: BLE001 - an unreadable mode is the default, single
        log.debug("first run: auth mode unreadable", exc_info=True)
        return True


def _in_use() -> bool:
    """Signs that this install was set up before the first run existed."""
    try:
        from common import chat_store
        total = int(chat_store.list_chats(workspace=None, limit=1)["total"])
        if total:
            theirs = total - int(chat_store.list_chats(workspace="demo", limit=1)["total"])
            return theirs > 0
    except Exception:  # noqa: BLE001
        log.debug("first run: chats unreadable", exc_info=True)
    return False


def load() -> Dict[str, Any]:
    """The record, written on the first read (see the module docstring)."""
    try:
        doc = _store.get(_KEY)
    except Exception:  # noqa: BLE001 - an unreadable store asks nothing of anyone
        log.warning("first run: record unreadable", exc_info=True)
        return {"completed_at": _now(), "via": "unreadable"}
    if isinstance(doc, dict):
        return doc
    doc = {"created_at": _now(), "completed_at": None, "step": "hello"}
    if _in_use():
        doc.update(completed_at=_now(), via="upgrade")
    try:
        _store.put(_KEY, doc)
    except Exception:  # noqa: BLE001 - asked again next time, nothing lost
        log.warning("first run: record not saved", exc_info=True)
    return doc


def status() -> Dict[str, Any]:
    """What the browser needs to decide, cheaply: whether to show the first
    run, and where it stopped. Never raises."""
    if not applies():
        return {"applies": False, "required": False, "completed_at": None, "step": None,
                "language": None, "theme": None}
    doc = load()
    return {
        "applies": True,
        "required": not doc.get("completed_at"),
        "completed_at": doc.get("completed_at"),
        "via": doc.get("via") or "",
        "step": doc.get("step") if doc.get("step") in STEPS else "hello",
        "language": doc.get("language") if doc.get("language") in LANGUAGES else None,
        "theme": doc.get("theme") if doc.get("theme") in THEMES else None,
    }


def language() -> Optional[str]:
    """The language chosen in the first run, if any."""
    try:
        doc = _store.get(_KEY)
    except Exception:  # noqa: BLE001
        return None
    value = doc.get("language") if isinstance(doc, dict) else None
    return value if value in LANGUAGES else None


def act(action: str, *, step: Optional[str] = None, language: Optional[str] = None,
        theme: Optional[str] = None) -> Dict[str, Any]:
    """Save where the person is, finish, or start again. Raises ValueError."""
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    if not applies():
        raise ValueError("the first run does not apply in multi mode")
    if step is not None and step not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    if language is not None and language not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
    if theme is not None and theme not in THEMES:
        raise ValueError(f"theme must be one of {', '.join(THEMES)}")
    doc = dict(load())
    if action == "progress" and doc.get("completed_at"):
        # A tab left open on a finished first run must not reopen it.
        return status()
    if action == "finish":
        from common import setup_ops
        if not setup_ops.usable_providers():
            raise ValueError("connect a model first: nothing runs without one")
        doc.update(completed_at=_now(), step="done", via="first_run")
    elif action == "restart":
        doc.update(completed_at=None, step="hello", restarted_at=_now())
        doc.pop("via", None)
    if action == "progress" and step:
        doc["step"] = step
    if language:
        doc["language"] = language
    if theme:
        doc["theme"] = theme
    _store.put(_KEY, doc)
    return status()


def _configured_model() -> Optional[Dict[str, Any]]:
    """A default model that can run: a usable provider and a model on it."""
    from common import setup_ops
    cur = setup_ops.default_model()
    if cur.get("usable") and cur.get("model"):
        return {"provider": str(cur["provider"]), "model": str(cur["model"])}
    return None


def _configured_demo() -> Optional[Dict[str, Any]]:
    """DEMO_WORKSPACE written in .env or the environment, either value: the
    setting's default (off) is not a decision, an explicit off is."""
    import os
    from common.config import read_dot_env
    raw = read_dot_env().get("DEMO_WORKSPACE")
    if raw is None:
        raw = os.environ.get("DEMO_WORKSPACE")
    if raw is None or not str(raw).strip():
        return None
    return {"enabled": str(raw).strip().lower() in ("1", "true", "yes", "on")}


def configured() -> Dict[str, Optional[Dict[str, Any]]]:
    """Per screen, what is already in force (``None``: nothing, ask). Keys:
    ``model`` (the choice of model), ``demo``. A
    failure to read one means not configured, never an error."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for key, read in (("model", _configured_model), ("demo", _configured_demo)):
        try:
            out[key] = read()
        except Exception:  # noqa: BLE001 - unreadable means ask
            log.debug("first run: %s configuration unreadable", key, exc_info=True)
            out[key] = None
    return out


def context() -> Dict[str, Any]:
    """What the model and demo steps start from, read now. Cheap: no
    provider is asked (``setup_ops.options`` does that, for the model step's
    tiers only)."""
    from common import setup_ops
    out: Dict[str, Any] = {"providers": [], "default_model": {}, "demo": False}
    try:
        out["providers"] = setup_ops.usable_providers()
        out["default_model"] = setup_ops.default_model()
    except Exception:  # noqa: BLE001
        log.debug("first run: providers unreadable", exc_info=True)
    try:
        from common.demo_workspace import demo_status
        out["demo"] = bool(demo_status().get("present"))
    except Exception:  # noqa: BLE001
        log.debug("first run: demo unreadable", exc_info=True)
    out["configured"] = configured()
    return out


__all__ = ["ACTIONS", "LANGUAGES", "STEPS", "THEMES", "act", "applies", "configured", "context",
           "language", "load", "status"]

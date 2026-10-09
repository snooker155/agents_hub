"""
The first run: the install's own setup, done once in the browser before the
rest of the app opens (docs/installation.md, "The first run in the browser").

A full screen of its own, step by step: the language, light or dark, a model
to think with and which one, the assistant's voice, web search, personal
memory, the demo workspace. Only the model is required; every other step has
a "not now". There is no way past the whole of it, and once finished it does
not come back (Settings, First setup, starts it again on purpose).

**One per install, single operator only.** The record is one document,
``install``, in the ``first_run`` collection. In ``multi`` mode the first
administrator is made on the login screen and each person gets the welcome
window and the guided setup (common/setup_guide.py), so the first run does not
apply there.

**An install that is already in use is never stopped.** The first time the
record is read and there is none, the hub looks for signs of use (a guided
setup already started, a chat outside the demo and system workspaces) and,
finding any, writes the first run as finished by the upgrade. That check runs
once: a fresh install writes an empty record and is asked from then on.

**What the steps change is written where the pages write it.** The model,
the voice and the demo go through common/setup_ops.py, web search through the
Settings route, personal memory through the workspace's own route. The record
keeps only what no page holds: the step the person is on, so a reload goes on
from there, and the language and the look they chose, so a second browser
starts with them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("first_run")
_KEY = "install"

#: The screens, in order. The browser draws them; the server only checks a
#: saved step is one of them.
STEPS = ("hello", "language", "appearance", "model", "choose_model", "voice", "search", "memory",
         "demo", "done")
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
        from common import setup_guide
        if setup_guide.load_state(None):
            return True
    except Exception:  # noqa: BLE001
        log.debug("first run: guide state unreadable", exc_info=True)
    try:
        from common import chat_store
        total = int(chat_store.list_chats(workspace=None, limit=1)["total"])
        if total:
            theirs = total - sum(int(chat_store.list_chats(workspace=ws, limit=1)["total"])
                                 for ws in ("demo", "system"))
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
    """The language chosen in the first run, if any (for defaults such as the
    local voice)."""
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
        from common import setup_guide
        if not setup_guide.usable_providers() and not _local_set_started():
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


#: What each screen from the voice on is about, for the assistant's turn: from
#: the voice screen on the assistant can talk, and it answers questions there.
SCREEN_ABOUT = {
    "voice": "the assistant's voice: a cloud voice (OpenAI or Google key), one on this computer "
             "(the hub's own runtime, Piper voices per language) or the browser's own, then a short "
             "spoken check that the assistant hears and answers",
    "search": "web search: a Brave, Tavily or Exa key, or nothing when an Anthropic or OpenAI key "
              "already searches",
    "memory": "personal memory: whether agents keep what the person tells them about themselves "
              "(on the Memory page, per workspace and agent)",
    "demo": "the demo workspace: four ready agents with chats, views, a team and a pulse, apart from "
            "the person's own work",
    "done": "the summary of what was chosen, and the way into the app: Chat, the assistant's guided "
            "setup of the rest, or the tour",
}


def prompt_lines(screen: str) -> List[str]:
    """The block an assistant turn asked from the first run carries: only
    while the first run is on and for a screen the assistant is offered on."""
    if screen not in SCREEN_ABOUT:
        return []
    got = status()
    if not got["required"]:
        return []
    later = [s for s in STEPS[STEPS.index(screen) + 1:] if s in SCREEN_ABOUT]
    lines = [
        "The person is going through the hub's first setup, a full screen of its own; the rest of "
        "the app is hidden until it ends.",
        f"Screen now: {screen}: {SCREEN_ABOUT[screen]}.",
        "Screens after it: " + (", ".join(later) if later else "none"),
    ]
    if got.get("language"):
        lines.append(f"Language chosen in the setup: {got['language']}; answer in it unless the person "
                     f"writes in another.")
    lines.append("Answer in two or three short sentences, they are read aloud. Explain and advise; the "
                 "person makes each choice with the screen's own buttons, so call no tool that changes "
                 "anything (setup_step, propose_connection) and no show_on_screen, and never ask for a key "
                 "in the conversation. After the setup you can lead the rest of it.")
    return lines


def _local_set_started() -> bool:
    """The model step is also met by the ready local set running or done: its
    chat model is still downloading, the hub thinks with it once it loads."""
    try:
        from providers import local_set
        job = local_set.latest_job()
        return bool(job) and job.get("status") in ("queued", "running", "done")
    except Exception:  # noqa: BLE001
        log.debug("first run: local set unreadable", exc_info=True)
        return False


def context() -> Dict[str, Any]:
    """What the model, voice and search steps start from, read now. Cheap: no
    provider is asked (``setup_ops.options`` does that, for the model step's
    tiers only)."""
    from common import setup_guide
    from tools.web import effective_search_provider
    out: Dict[str, Any] = {"providers": [], "default_model": {}, "runtime": False, "local_set": None,
                           "search": {"provider": "", "source": "none"}, "demo": False, "voice": False}
    try:
        out["providers"] = setup_guide.usable_providers()
        out["default_model"] = setup_guide.default_model()
    except Exception:  # noqa: BLE001
        log.debug("first run: providers unreadable", exc_info=True)
    try:
        from providers import local_models as lm
        from providers import local_set
        out["runtime"] = bool(lm.runtime_configured())
        out["local_set"] = local_set.latest_job()
    except Exception:  # noqa: BLE001
        log.debug("first run: runtime unreadable", exc_info=True)
    try:
        eff = effective_search_provider()
        out["search"] = {"provider": str(eff["provider"]), "source": str(eff["source"]),
                         "key_set": bool(eff["key_set"])}
    except Exception:  # noqa: BLE001
        log.debug("first run: search unreadable", exc_info=True)
    try:
        from common.demo_workspace import demo_status
        out["demo"] = bool(demo_status().get("present"))
    except Exception:  # noqa: BLE001
        log.debug("first run: demo unreadable", exc_info=True)
    try:
        from providers import special
        own = special.stored("default")
        out["voice"] = all(isinstance(own.get(k), dict) for k in ("speech", "transcription"))
    except Exception:  # noqa: BLE001
        log.debug("first run: voice unreadable", exc_info=True)
    return out


__all__ = ["ACTIONS", "LANGUAGES", "SCREEN_ABOUT", "STEPS", "THEMES", "act", "applies", "context", "language",
           "load", "prompt_lines", "status"]

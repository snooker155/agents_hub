"""
The guided setup: what is left to set up in the hub and to try in it, one step
at a time, led by the assistant (docs/assistant.md "Guided setup").

`ah setup` (or the welcome window) does only what the assistant cannot do for
itself: an account and one model to think with. From there the assistant takes
the person through the rest by voice or text: the default model, its own voice,
web search, the demo, the team, the hub's health, then a first chat, channel,
account, agent, task and something that runs on its own. Each step here says
why it matters, how it is detected as done, what the assistant does about it
(a tool, a connection card, a page) and which page shows it.

**Done is read, not remembered.** A step is done when the hub says so (a key
is set, a channel is configured, a task exists), read again on every call, so
doing a step on its page ticks it in the guide as well. Only what the hub
cannot see is remembered per person: the steps they skipped, the ones the
assistant marked done (the health check), whether the guide is running, and
the voice download it started (common/setup_ops.py).

**Per person, by role.** The first group is the install's own setup and is an
administrator's (everyone, outside ``multi`` mode); the second is everyone's.
Counts for the second group come from the workspaces the person reaches,
without the demo and system workspaces, so seeding the demo ticks nothing.

Never raises on a half configured install: an item that cannot be read is "not
done yet", never a failed page.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("setup_guide")

GROUP_SETUP = "setup"
GROUP_START = "start"

DONE, TODO, SKIPPED, WORKING = "done", "todo", "skipped", "working"

#: Workspaces whose records say nothing about what this person has tried.
_NOT_THEIRS = frozenset({"demo", "system"})

ACTIONS = ("start", "skip", "unskip", "done", "undo", "finish", "dismiss", "restart")


@dataclass(frozen=True)
class Step:
    id: str
    group: str
    title: str
    #: One sentence on why it matters, for the assistant to say.
    why: str
    #: What the assistant does about it (tools, cards, pages), for its prompt.
    how: str
    #: The dashboard page that shows it.
    page: str
    #: ``ctx -> (done, detail)``; done None when it cannot be told.
    detect: Callable[["_Ctx"], "tuple[Optional[bool], str]"]
    #: Must be done before the rest makes sense (never offered as a skip).
    required: bool = False
    #: Only in ``multi`` mode.
    multi_only: bool = False


# ── what the hub says ─────────────────────────────────────────────────────────

class _Ctx:
    """What one call reads, each item once and only when a step asks."""

    def __init__(self, principal: Any, *, tour_done: Optional[bool], multi: bool) -> None:
        self.principal = principal
        self.tour_done = tour_done
        self.multi = multi
        self._cache: Dict[str, Any] = {}

    def get(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._cache:
            try:
                self._cache[key] = fn()
            except Exception:  # noqa: BLE001 - an unreadable item is "not done yet"
                log.debug("setup guide: %s unreadable", key, exc_info=True)
                self._cache[key] = None
        return self._cache[key]

    def workspaces(self) -> List[str]:
        def read() -> List[str]:
            if self.multi and self.principal is not None:
                from chat.lookup import reachable_workspaces
                names = reachable_workspaces(self.principal)
            else:
                from workspace.storage import list_workspace_folders
                names = [p.name for p in list_workspace_folders()] or ["default"]
            return [n for n in names if n and n not in _NOT_THEIRS]
        return self.get("workspaces", read) or ["default"]

    def count(self, key: str, per_workspace: Callable[[Optional[str]], int]) -> Optional[int]:
        """``per_workspace(name)`` summed over the person's workspaces;
        ``per_workspace(None)`` is every workspace's."""
        def read() -> int:
            if not self.multi:
                # Everything but the demo and system workspaces, records filed
                # under no workspace at all included.
                return max(0, int(per_workspace(None) or 0)
                           - sum(int(per_workspace(ws) or 0) for ws in _NOT_THEIRS))
            return sum(int(per_workspace(ws) or 0) for ws in self.workspaces())
        return self.get(key, read)


def usable_providers() -> List[str]:
    """Providers an agent can call right now: a key set, a local server named,
    a custom backend registered (the hub's own runtime among them)."""
    from common import provider_env
    env = provider_env.live()
    out = [p for p, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
                            ("google", "GOOGLE_API_KEY")) if env.get(key)]
    out += [p for p, key in (("ollama", "OLLAMA_MODEL"), ("lmstudio", "LMSTUDIO_MODEL")) if env.get(key)]
    try:
        # A custom backend counts once it has a model to call: its own default,
        # or one enabled in the catalog (the hub's runtime is registered as a
        # backend before any model is loaded into it).
        from providers import list_backends
        from providers.catalog import load_catalog_raw
        catalog = load_catalog_raw() or {}
        for b in list_backends():
            bid = str(b.get("id") or "")
            found = catalog.get(bid)
            entry: Dict[str, Any] = found if isinstance(found, dict) else {}
            enabled = any(isinstance(m, dict) and m.get("enabled") for m in entry.get("models") or [])
            if bid and (b.get("default_model") or enabled):
                out.append(bid)
    except Exception:  # noqa: BLE001 - no custom backends readable
        log.debug("setup guide: custom backends unreadable", exc_info=True)
    return out


def default_model() -> Dict[str, Any]:
    """The model every workspace without its own falls back to, and whether it can run."""
    from common import provider_env
    from cli.onboard.probe import MODEL_VAR
    env = provider_env.live()
    provider = env.get("DEFAULT_PROVIDER") or "openai"
    model = env.get(MODEL_VAR.get(provider, ""), "")
    if not model and provider not in MODEL_VAR:
        try:
            from providers import get_backend
            model = str((get_backend(provider) or {}).get("default_model") or "")
        except Exception:  # noqa: BLE001
            model = ""
    return {"provider": provider, "model": model, "usable": provider in usable_providers()}


def _model(ctx: _Ctx):
    have = ctx.get("providers", usable_providers) or []
    return bool(have), ", ".join(have)


def _default_model(ctx: _Ctx):
    cur = ctx.get("default_model", default_model) or {}
    ok = bool(cur.get("usable")) and bool(cur.get("model"))
    return ok, "/".join(x for x in (cur.get("provider"), cur.get("model")) if x)


def _voice(ctx: _Ctx):
    def read():
        from providers import special
        own = special.stored("default")
        return {k: own.get(k) for k in ("speech", "transcription")}
    have = ctx.get("voice", read) or {}
    parts = [f"{k} {v.get('provider')}/{v.get('model')}" for k, v in have.items() if isinstance(v, dict)]
    return all(isinstance(have.get(k), dict) for k in ("speech", "transcription")), ", ".join(parts)


def _web_search(ctx: _Ctx):
    from common import provider_env
    env = provider_env.live()
    provider = env.get("WEB_SEARCH_PROVIDER", "")
    return bool(provider and env.get("WEB_SEARCH_API_KEY")), provider


def _demo(ctx: _Ctx):
    def read():
        from common.demo_workspace import demo_status
        return bool(demo_status().get("present"))
    present = ctx.get("demo", read)
    return present, "the demo workspace is there" if present else ""


def _people(ctx: _Ctx):
    def read():
        from common import identity
        return len(identity.list_users())
    n = ctx.get("people", read)
    return (None if n is None else n > 1), (f"{n} accounts" if n else "")


def _health(ctx: _Ctx):
    return None, ""


def _chats(ctx: _Ctx):
    def one(ws: Optional[str]) -> int:
        from common import chat_store
        return int(chat_store.list_chats(workspace=ws, limit=1)["total"])
    n = ctx.count("chats", one)
    return (None if n is None else n > 0), (f"{n} chats" if n else "")


def _channel(ctx: _Ctx):
    def read():
        from common.onboarding import _channels
        seen: List[str] = []
        for ws in ctx.workspaces():
            seen += [c for c in _channels(ws)["configured"] if c not in seen]
        return seen
    have = ctx.get("channels", read)
    return (None if have is None else bool(have)), ", ".join(have or [])


def _accounts(ctx: _Ctx):
    def read():
        from common.onboarding import _accounts as accounts
        return accounts()
    have = ctx.get("accounts", read)
    return (None if have is None else bool(have)), ", ".join(have or [])


def _first_agent(ctx: _Ctx):
    def read():
        import json
        from agents.registry import load_all_raw
        from common.bootstrap import BOOTSTRAP_AGENTS_FILE
        from common.demo_workspace import DEMO_AGENT_IDS
        # Shipped agents that are not system ones (plot-manager) are not the person's either.
        shipped = {a.get("id") for a in json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8")).get("agents", [])
                   if isinstance(a, dict)}
        return [str(a.get("name") or a.get("id")) for a in load_all_raw()
                if isinstance(a, dict) and not a.get("system")
                and a.get("id") not in DEMO_AGENT_IDS and a.get("id") not in shipped]
    have = ctx.get("agents", read)
    return (None if have is None else bool(have)), ", ".join((have or [])[:3])


def _first_task(ctx: _Ctx):
    def one(ws: Optional[str]) -> int:
        from tasks.service import list_tasks_page
        return int(list_tasks_page(workspace=ws, limit=1)[1])
    n = ctx.count("tasks", one)
    return (None if n is None else n > 0), (f"{n} tasks" if n else "")


def _automation(ctx: _Ctx):
    def watchers(ws: Optional[str]) -> int:
        from watchers.service import list_watchers
        return len(list_watchers(ws))

    def pulses(ws: Optional[str]) -> int:
        from proactive.service import summary
        return int(summary(ws)["totals"]["agents"])
    w = ctx.count("watchers", watchers)
    p = ctx.count("pulses", pulses)
    if w is None and p is None:
        return None, ""
    parts = ([f"{w} watchers"] if w else []) + ([f"{p} pulses"] if p else [])
    return bool(w or p), ", ".join(parts)


def _tour(ctx: _Ctx):
    return ctx.tour_done, ""


STEPS: tuple[Step, ...] = (
    Step("model", GROUP_SETUP, "Connect a model",
         "Every agent, you included, thinks with a model, so nothing runs without one.",
         "Offer propose_connection with kind provider (openai, anthropic or google): the person types "
         "the key into the card. A model already running on this machine (Ollama, LM Studio) or the hub's "
         "own runtime is set on the Models page.",
         "/settings/providers", _model, required=True),
    Step("default_model", GROUP_SETUP, "Choose the default model",
         "Every workspace without a model of its own uses this one, and it decides speed and cost.",
         "setup_guide options lists each connected provider's strongest, balanced and fastest model; "
         "recommend balanced, then setup_step choose_model.",
         "/models", _default_model, required=True),
    Step("voice", GROUP_SETUP, "Give the assistant a voice",
         "With a speech and a transcription model the assistant hears and speaks in every browser; "
         "without them it uses the browser's own, which Firefox lacks.",
         "setup_step voice_cloud (a connected OpenAI or Google key, paid per use) or voice_local (the "
         "hub's own runtime, free, downloads 0.5 to 2 GB); skipping keeps the browser's voice.",
         "/models?tab=special", _voice),
    Step("web_search", GROUP_SETUP, "Turn on web search",
         "Agents can then search the web, not only open pages they are given.",
         "propose_connection with kind provider and target brave, tavily or exa: the person types the "
         "key into the card.",
         "/settings/webSearch", _web_search),
    Step("demo", GROUP_SETUP, "Look around the demo workspace",
         "Four agents with chats, views and a pulse show what the hub does before anything is built.",
         "setup_step seed_demo, then show_on_screen /workspaces/demo.",
         "/workspaces", _demo),
    Step("people", GROUP_SETUP, "Invite your team",
         "Each person gets their own sign in, personal workspace and assistant.",
         "Accounts and their passwords are made on the Users page: show_on_screen /users.",
         "/users", _people, multi_only=True),
    Step("health", GROUP_SETUP, "Check the hub's health",
         "The doctor checks the database, the model connection, the runtime and the rest, each with its fix.",
         "run_diagnostics (an administrator's service thread holds it; in a personal thread "
         "show_on_screen /health instead), report what failed with its docs section, then setup_guide "
         "done health.",
         "/health", _health),
    Step("first_chat", GROUP_START, "Talk to an agent in Chat",
         "Chat is where you work with any agent, with files, references and approvals.",
         "Explain Chat in two sentences and show_on_screen /chat.",
         "/chat", _chats),
    Step("channel", GROUP_START, "Reach the hub from Telegram, Slack or mail",
         "Then you can talk to your agents from your phone's messenger or by email.",
         "connection_options for kind channel, then propose_connection with kind channel: the person "
         "types the bot token into the card.",
         "/connectors", _channel),
    Step("accounts", GROUP_START, "Connect your accounts",
         "Agents can then read and write your Google, Microsoft, Jira, Linear or Notion data.",
         "propose_connection with kind connector; Google and Microsoft finish with a sign in on the "
         "Connectors page.",
         "/connectors", _accounts),
    Step("first_agent", GROUP_START, "Make an agent of your own",
         "An agent with its own instructions, tools and model does one kind of job well.",
         "Ask what job it should do and delegate to agent_creator, or show_on_screen /marketplace for "
         "ready ones.",
         "/marketplace", _first_agent),
    Step("first_task", GROUP_START, "Hand an agent a task",
         "A task runs on its own, with its result, cost and history kept on the Tasks page.",
         "create_task for an agent with something the person actually needs, after a yes.",
         "/tasks", _first_task),
    Step("automation", GROUP_START, "Let something run on its own",
         "A watcher wakes an agent on new mail or a changed page; a pulse lets an agent check in on a schedule.",
         "propose_connection with kind watcher, or show_on_screen /watchers; a pulse is set on the agent's "
         "Pulse tab.",
         "/watchers", _automation),
    Step("tour", GROUP_START, "Take the welcome tour",
         "Two minutes over the main pages, so you know where everything lives.",
         "The tour runs in the browser: ask the person to press Take the tour in the setup panel.",
         "", _tour),
)

_BY_ID = {s.id: s for s in STEPS}


# ── whose guide ───────────────────────────────────────────────────────────────

def _multi() -> bool:
    from common import identity
    from common.auth import MULTI
    return identity.current_mode() == MULTI


def person_key(principal: Any) -> str:
    """One guide per person in ``multi`` mode, one for the operator otherwise."""
    if _multi() and principal is not None and getattr(principal, "id", None):
        return f"user-{principal.id}"
    return "local"


def is_admin(principal: Any) -> bool:
    return (not _multi()) or bool(principal is not None and getattr(principal, "is_admin", False))


def steps_for(principal: Any) -> List[Step]:
    admin, multi = is_admin(principal), _multi()
    return [s for s in STEPS if (s.group != GROUP_SETUP or admin) and (multi or not s.multi_only)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(principal: Any) -> Dict[str, Any]:
    try:
        doc = _store.get(person_key(principal))
    except Exception:  # noqa: BLE001 - an unreadable store is a guide not started
        log.debug("setup guide: state unreadable", exc_info=True)
        doc = None
    return doc if isinstance(doc, dict) else {}


def save_state(principal: Any, state: Dict[str, Any]) -> None:
    _store.put(person_key(principal), state)


# ── the guide ─────────────────────────────────────────────────────────────────

def _work_line(work: Dict[str, Any]) -> str:
    jobs = [j for j in work.get("jobs") or [] if isinstance(j, dict)]
    running = [j for j in jobs if j.get("status") not in ("done", "error")]
    if work.get("phase") == "starting":
        return "starting the hub's model runtime"
    if running:
        j = running[0]
        pct = j.get("percent")
        return f"{j.get('label')}: {f'{pct:.0f}%' if isinstance(pct, (int, float)) and pct else 'running'}" + (
            f", {len(running) - 1} more" if len(running) > 1 else "")
    failed = [j for j in jobs if j.get("status") == "error"]
    if failed:
        return f"{failed[0].get('label')} failed: {failed[0].get('error') or 'see the Models page, Local tab'}"
    return work.get("message") or ""


def guide(principal: Any, *, tour_done: Optional[bool] = None) -> Dict[str, Any]:
    """The person's guide: every step with its status, what is next, and
    whether the guide is running. Never raises."""
    multi = _multi()
    state = load_state(principal)
    ctx = _Ctx(principal, tour_done=tour_done, multi=multi)
    skipped = set(state.get("skipped") or [])
    marked = set(state.get("marked") or [])
    work = state.get("work") if isinstance(state.get("work"), dict) else None
    rows: List[Dict[str, Any]] = []
    for step in steps_for(principal):
        try:
            done, detail = step.detect(ctx)
        except Exception:  # noqa: BLE001 - see the module docstring
            log.debug("setup guide: %s failed", step.id, exc_info=True)
            done, detail = None, ""
        status = DONE if (done or step.id in marked) else SKIPPED if step.id in skipped else TODO
        if work and work.get("step") == step.id and work.get("phase") not in ("done", "error"):
            # Saved, but not ready until the runtime has the models.
            status, detail = WORKING, _work_line(work)
        elif work and work.get("step") == step.id and work.get("phase") == "error":
            detail = _work_line(work) or detail
        rows.append({"id": step.id, "group": step.group, "title": step.title, "why": step.why,
                     "status": status, "detail": detail or "", "page": step.page,
                     "required": step.required, "marked": step.id in marked})
    open_rows = [r for r in rows if r["status"] == TODO]
    done_n = sum(1 for r in rows if r["status"] == DONE)
    needs_model = any(r["id"] == "model" and r["status"] != DONE for r in rows) or (
        not is_admin(principal) and not ctx.get("providers", usable_providers))
    active = bool(state.get("started_at")) and not state.get("finished_at") and not state.get("dismissed_at")
    return {
        "active": active,
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "dismissed_at": state.get("dismissed_at"),
        "mode": state.get("mode") or "",
        "admin": is_admin(principal),
        "multi": multi,
        "needs_model": bool(needs_model),
        "steps": rows,
        "done": done_n,
        "total": len(rows),
        "next": open_rows[0]["id"] if open_rows else None,
        "complete": not open_rows and not any(r["status"] == WORKING for r in rows),
        "work": work,
    }


def act(principal: Any, action: str, step: Optional[str] = None, *, mode: Optional[str] = None,
        tour_done: Optional[bool] = None) -> Dict[str, Any]:
    """Start, skip, mark or end the guide. Raises ValueError for a bad call."""
    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    step = str(step or "").strip() or None
    if action in ("skip", "unskip", "done", "undo"):
        if step not in {s.id for s in steps_for(principal)}:
            raise ValueError(f"step must be one of {', '.join(s.id for s in steps_for(principal))}")
        if action == "skip" and _BY_ID[step].required:
            raise ValueError(f"'{step}' cannot be skipped: nothing runs without it")
    state = load_state(principal)
    skipped = [s for s in state.get("skipped") or [] if s != step]
    marked = [s for s in state.get("marked") or [] if s != step]
    if action == "start" or action == "restart":
        if action == "restart":
            skipped, marked = [], []
            state.pop("work", None)
        state.update({"started_at": state.get("started_at") if action == "start" and state.get("started_at")
                      else _now(), "finished_at": None, "dismissed_at": None})
        if mode in ("voice", "text"):
            state["mode"] = mode
    elif action == "skip":
        skipped.append(step)
    elif action == "done":
        marked.append(step)
    elif action == "finish":
        state["finished_at"] = _now()
    elif action == "dismiss":
        state["dismissed_at"] = _now()
    state["skipped"], state["marked"] = skipped, marked
    save_state(principal, state)
    return guide(principal, tour_done=tour_done)


def set_work(principal: Any, work: Optional[Dict[str, Any]]) -> None:
    """The long piece of a step the hub carries on with (a voice download)."""
    state = load_state(principal)
    if work is None:
        state.pop("work", None)
    else:
        state["work"] = work
    save_state(principal, state)


def prompt_lines(principal: Any) -> List[str]:
    """The guide as the assistant's turn sees it, while it runs; [] otherwise."""
    g = guide(principal)
    if not g["active"]:
        return []
    by_id = {s.id: s for s in STEPS}
    lines = [f"Running: {g['done']} of {g['total']} steps done"
             + (f"; the person chose to go by {g['mode']}" if g["mode"] else "") + "."]
    for r in g["steps"]:
        mark = {"done": "x", "skipped": "-", "working": "~"}.get(r["status"], " ")
        lines.append(f"[{mark}] {r['id']}: {r['title']}" + (f" ({r['detail']})" if r["detail"] else ""))
    if g["next"]:
        nxt = by_id[g["next"]]
        lines += [f"Next: {nxt.id}. Why: {nxt.why}", f"How: {nxt.how}"]
        if nxt.page:
            lines.append(f"Its page (for show_on_screen): {nxt.page}")
    elif g["complete"]:
        lines.append("Every step is done or skipped: congratulate the person briefly and call setup_guide finish.")
    return lines


__all__ = ["ACTIONS", "STEPS", "Step", "act", "default_model", "guide", "is_admin", "load_state",
           "person_key", "prompt_lines", "save_state", "set_work", "steps_for", "usable_providers"]

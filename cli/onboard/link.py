"""The Linked hub step of `ah setup`: attach this hub to another Agents Hub.

A hub can lean on another one in four ways (docs/hub-link.md, docs/hub-sync.md),
and until now only the Settings page could choose between them, so an
unattended install could not say "this laptop syncs with the team hub" at all.
The step asks the same things the Remote hub tab of Settings does and writes
the same records:

* **catalog**: browse and install the other hub's agents, skills, flows,
  teams and loops. Nothing of this hub is sent, so the mirror stays off and
  neither its models nor its agents are wired in.
* **sync**: two-way sync of chosen workspaces with spaces there, then one
  choice for the start: encrypt every workspace (default and all the others,
  including ones added later), sealed with a passphrase before anything
  leaves, so the other hub stores envelopes it cannot open; or no encryption
  for now, and each workspace can be encrypted on its own later. The
  passphrase is generated (shown once in the farewell) or typed, for a
  second device. ``sync-e2e``, the mode an older answers file names, is sync
  with every workspace encrypted.
* **connect**: use its models and agents only, the behaviour a link had
  before the other modes existed; runs and chats are mirrored there as the
  link's defaults say.

In both sync modes the one-way mirror is turned off as a whole: sync carries
runs, chats and memory itself, and in the encrypted mode a mirror in clear
would hand the other hub exactly what the encryption keeps from it.

Where it is applied depends on the shape. On this checkout the database is
right here, so the record is written with hublink.store and the sync set up
with hubsync.client, never through ``hublink.link.link``: that one finishes
with ``runtime.refresh()``, which would start the drainer and node threads
inside this short-lived CLI process. Its registration helpers write records
only, so they are reused. A stack in Docker gets the same through its API once
it answers. A Kubernetes cluster is not running yet when the values are
written, and the link lives in its database, so the step only says where to
set it later.
"""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from cli.onboard.ui import Asker, SetupError

if TYPE_CHECKING:
    from cli.onboard.wizard import Plan

MODES = [
    ("catalog", "Catalog only",
     "browse and install agents, skills, flows, teams and loops; nothing of yours is sent"),
    ("sync", "Two-way sync",
     "of the workspaces you choose; encryption is the next question"),
    ("connect", "Only its models and agents",
     "as a provider and as tools here; finished runs and chats are mirrored there"),
]
MODE_LABELS = {value: label for value, label, _ in MODES}
MODE_LABELS["sync-e2e"] = "Two-way sync, every workspace encrypted"
SYNC_MODES = ("sync", "sync-e2e")

ENCRYPTION = [
    ("all", "Encrypt every workspace",
     "default and all the others, including ones you add later; the linked hub stores only data it cannot read"),
    ("off", "No encryption for now",
     "you can encrypt each workspace on its own later: Settings, Remote hub, or the workspace's own Settings"),
]
ENCRYPT_ALL_NOTE = ("Every workspace is encrypted: default and all the others, including ones you add later. "
                    "To encrypt only some, turn this off in Settings, Remote hub.")
ENCRYPT_OFF_NOTE = ("Nothing is encrypted for now. Encrypt a single workspace yourself: its row in Settings, "
                    "Remote hub, or the workspace's Settings, Remote hub sync.")

_WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

PASSPHRASE_WARNING = ("The sync passphrase is the only way to open the synced data: losing it and every "
                      "device that holds the key means nobody can recover it.")


def _url_problem(value: str) -> Optional[str]:
    from hublink.store import normalize_url
    try:
        normalize_url(value)
    except ValueError as exc:
        return str(exc).capitalize()
    return None


def _workspace_problem(name: str) -> Optional[str]:
    if not _WORKSPACE.fullmatch(name):
        return f"'{name}' is not a workspace name (letters, digits, - and _)"
    if name.startswith("personal-"):
        return f"'{name}' is a personal workspace name; sync the default workspace instead"
    return None


def _answered_value(a: Asker, key: str) -> Any:
    """The raw value an answers file gives for ``key``, or None: a list of
    workspaces is a list there, which a text question would turn into its repr."""
    node: Any = getattr(a, "answers", None)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# What is there already
# ---------------------------------------------------------------------------


def existing_link(plan: "Plan") -> Dict[str, Any]:
    """The link record of a local hub whose database this run keeps, so a
    rerun defaults to it. Empty whenever that cannot be known cheaply."""
    if plan.shape != "local" or plan.fresh:
        return {}
    if plan.db_url != (plan.current.get("AGENTS_HUB_DATABASE_URL") or ""):
        return {}
    try:
        from cli.backend import _ensure_importable
        _ensure_importable()
        if os.environ.get("AGENTS_HUB_DATABASE_URL", "") != plan.db_url:
            os.environ["AGENTS_HUB_DATABASE_URL"] = plan.db_url
        from hublink import store
        return store.get() or {}
    except Exception:  # noqa: BLE001 - no database yet is a fine answer: nothing linked
        return {}


def _mode_of(doc: Dict[str, Any]) -> str:
    sync = doc.get("sync") or {}
    if sync.get("enabled"):
        return "sync"
    if doc.get("catalog") and not doc.get("use_models") and not doc.get("use_agents"):
        return "catalog"
    return "connect"


# ---------------------------------------------------------------------------
# The check against the other hub
# ---------------------------------------------------------------------------


def probe(url: str, api_key: str) -> Dict[str, Any]:
    """Ask the other hub whether it answers and whether it takes the key.

    Returns ``{"ok": True, "me": <whoami>, "sync_keys": n}`` or
    ``{"ok": False, "error": why}``. Goes through hublink.remote, the client
    the link itself uses, so a test points both at the same fake hub.
    """
    from hublink.remote import Remote, RemoteError
    from hublink.store import normalize_url
    try:
        url = normalize_url(url)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        Remote(url, "").call("GET", "/api/health", ok=tuple(range(200, 500)), timeout=10)
    except RemoteError as exc:
        return {"ok": False, "error": f"{url} does not answer ({exc.__class__.__name__})"}
    rm = Remote(url, api_key)
    try:
        me = rm.whoami()
    except RemoteError as exc:
        if exc.status in (401, 403):
            return {"ok": False, "error": "it refused the key"}
        return {"ok": False, "error": f"it did not say who the key belongs to (HTTP {exc.status or 'error'})"}
    if not me.get("id"):
        return {"ok": False, "error": "it did not accept the key"}
    keys = 0
    try:
        keys = len(rm.get_json("/api/sync/keys", timeout=10).get("keys") or [])
    except (RemoteError, AttributeError):
        pass  # an older hub without sync: the mode question still decides
    return {"ok": True, "me": me, "sync_keys": keys}


def _whoami_cache(me: Dict[str, Any]) -> Dict[str, Any]:
    """What the link record keeps of the other hub's answer (hublink/link.py)."""
    return {"mode": me.get("mode"), "principal": me.get("principal"),
            "personal_workspace": me.get("personal_workspace")}


# ---------------------------------------------------------------------------
# The step
# ---------------------------------------------------------------------------


def step_link(a: Asker, plan: "Plan", number: int, total: int) -> None:
    a.section(number, total, "Linked hub",
              "Another Agents Hub this one borrows from or syncs with (docs/hub-link.md, docs/hub-sync.md).")
    plan.link = {}
    if plan.shape == "kubernetes":
        a.note("The link lives in the hub's database, so it is set once the cluster runs: "
               "Settings, Remote hub.")
        return
    if plan.shape not in ("local", "docker", "compose"):
        return
    current = existing_link(plan)
    advanced = not plan.quick
    if not a.confirm("link.enabled", "Link this hub to another Agents Hub?", bool(current)):
        if current:
            a.note(f"The link to {current.get('url')} stays as it is; remove it in Settings, Remote hub.")
        return

    a.note("[dim]A personal API key of your account there: on that hub, Settings, API access, "
           "Personal API keys.[/dim]")
    while True:
        url = a.text("link.url", "The other hub's URL (its dashboard's address)", default=current.get("url", ""),
                     required=True, validate=_url_problem)
        from hublink.store import normalize_url
        url = normalize_url(url)
        same = current.get("url") == url
        api_key = a.secret("link.api_key", "Personal API key on that hub",
                           current=str(current.get("api_key") or "") if same else "", allow_empty=False)
        if plan.shape in ("docker", "compose") and (urlparse(url).hostname or "") in ("localhost", "127.0.0.1"):
            a.note("[dim]The stack's backend reaches it from inside a container, where localhost is the "
                   "container itself: host.docker.internal reaches this machine.[/dim]")
        with a.console.status(f"  Asking {url}…"):
            checked = probe(url, api_key)
        if checked["ok"]:
            me = checked["me"]
            a.ok(f"{url} takes the key ({me.get('principal') or me.get('id')}).")
            break
        a.warn(f"Could not check it: {checked['error']}.")
        if not a.interactive:
            a.note("Kept unchecked: the link is tried when it is applied.")
            break
        again = a.choose("link.probe_failed", "What now?", [
            ("retry", "Enter it again", ""),
            ("keep", "Keep it unchecked", "tried again when it is applied"),
            ("skip", "Skip the link", "set it later in Settings, Remote hub"),
        ], default="retry")
        if again == "skip":
            return
        if again == "keep":
            break

    link: Dict[str, Any] = {"url": url, "api_key": api_key, "checked": checked["ok"],
                            "remote": _whoami_cache(checked.get("me") or {}),
                            "sync_keys": int(checked.get("sync_keys") or 0)}
    if not same:
        # Another hub than the one linked before: its sync key and mappings
        # mean nothing there.
        current = {k: v for k, v in current.items() if k != "sync"}
    link["name"] = str(current.get("name") or "")
    if advanced or a.answered("link.name"):
        from hublink.store import default_node_name
        link["name"] = a.text("link.name", "This hub's name there", default=link["name"] or default_node_name())

    if _answered_value(a, "link.mode") == "sync-e2e":
        # An older answers file: sync with every workspace encrypted.
        mode = "sync"
        a.note("[dim]What should the link do?[/dim] sync, every workspace encrypted")
        encrypt_all: Optional[bool] = True
    else:
        mode = a.choose("link.mode", "What should the link do?", MODES,
                        default=_mode_of(current) if current else "catalog")
        encrypt_all = None
    link["mode"] = mode
    keep_wiring = current and _mode_of(current) == mode
    use_models = bool(current.get("use_models")) if keep_wiring else mode == "connect"
    use_agents = bool(current.get("use_agents")) if keep_wiring else mode == "connect"
    if advanced or a.answered("link.use_models"):
        use_models = a.confirm("link.use_models", "Use its models here (the provider hub-remote)?", use_models)
    if advanced or a.answered("link.use_agents"):
        use_agents = a.confirm("link.use_agents", "Use its agents here as tools (the MCP server hub-remote)?",
                               use_agents)
    link.update(use_models=use_models, use_agents=use_agents)

    catalog = mode == "catalog"
    if mode in SYNC_MODES:
        catalog = bool(current.get("catalog")) if keep_wiring else False
        if advanced or a.answered("link.catalog"):
            catalog = a.confirm("link.catalog", "Browse and install from its catalog as well?", catalog)
        if encrypt_all is None:
            had = current.get("sync") or {}
            # A rerun keeps the start chosen before; a config from before the
            # choice was per workspace counts as "all" when it was encrypted.
            legacy_all = bool(had.get("encrypted")) and all(
                m.get("encrypted") is None for m in had.get("mappings") or [])
            choice = a.choose("link.sync.encryption", "Encryption at the start", ENCRYPTION,
                              default="all" if had.get("encrypt_all") or legacy_all else "off")
            encrypt_all = choice == "all"
        a.note(ENCRYPT_ALL_NOTE if encrypt_all else ENCRYPT_OFF_NOTE)
        link["sync"] = _ask_sync(a, plan, link, current, encrypt_all)
    link["catalog"] = catalog
    plan.link = link


def _ask_workspaces(a: Asker, default: List[str]) -> List[str]:
    question = "Workspaces to sync, separated by commas"
    raw = _answered_value(a, "link.sync.workspaces")
    if isinstance(raw, list):
        names = [str(x).strip() for x in raw if str(x).strip()]
        a.note(f"[dim]{question}[/dim] {', '.join(names)}")
    else:
        while True:
            text = a.text("link.sync.workspaces", question, default=",".join(default), required=True)
            names = [n.strip() for n in text.split(",") if n.strip()]
            problem = next((p for p in map(_workspace_problem, names) if p), None)
            if not problem:
                break
            if not a.interactive:
                raise SetupError(f"'link.sync.workspaces': {problem}")
            a.warn(problem)
    names = list(dict.fromkeys(names))
    for name in names:
        problem = _workspace_problem(name)
        if problem:
            raise SetupError(f"'link.sync.workspaces': {problem}")
    if not names:
        raise SetupError("the answers file needs at least one workspace in 'link.sync.workspaces'")
    return names


def _ask_sync(a: Asker, plan: "Plan", link: Dict[str, Any], current: Dict[str, Any],
              encrypted: bool) -> Dict[str, Any]:
    had = current.get("sync") or {}
    mapped = {m.get("workspace"): m.get("space") for m in had.get("mappings") or [] if m.get("workspace")}
    # A workspace already encrypted stays so when the start is "no encryption":
    # its space there holds sealed records and would refuse plain ones.
    legacy = bool(had.get("encrypted"))
    was = {m.get("workspace"): bool(legacy if m.get("encrypted") is None else m.get("encrypted"))
           for m in had.get("mappings") or [] if m.get("workspace")}
    workspaces = _ask_workspaces(a, list(mapped) or ["default"])
    personal = str((link.get("remote") or {}).get("personal_workspace") or "")
    spaces: Dict[str, str] = {}
    for i, ws in enumerate(workspaces):
        # The personal workspace there is the natural home of the first one;
        # two workspaces cannot share a space, so the rest keep their names.
        default = mapped.get(ws) or (personal if i == 0 and personal else ws)
        key = f"link.sync.spaces.{ws}"
        space, ask = default, not plan.quick or a.answered(key)
        while True:
            if ask:
                space = a.text(key, f"Space on the linked hub for {ws}", default=default, required=True)
            clash = next((w for w, s in spaces.items() if s == space), None)
            if not clash:
                break
            problem = f"{ws} and {clash} both map to the space {space}; give each its own"
            if not a.interactive:
                raise SetupError(f"'{key}': {problem}")
            a.warn(problem)
            ask = True
        spaces[ws] = space
    kept = {ws: True for ws in workspaces if was.get(ws)} if not encrypted else {}
    out: Dict[str, Any] = {"workspaces": workspaces, "spaces": spaces, "encrypted": encrypted, "kept": kept}
    if kept:
        a.note("Still encrypted from before: " + ", ".join(kept) + ".")
    if not encrypted and not kept:
        return out

    held = bool((had.get("key") or {}).get("sealed"))
    if a.answered("link.sync.passphrase"):
        out["passphrase"] = a.secret("link.sync.passphrase", "The sync passphrase", allow_empty=False)
        return out
    if held:
        a.note("This hub already holds a sync key; it is kept.")
        out["keep_key"] = True
        return out
    if link.get("sync_keys"):
        a.note("Your account there already has a sync key, made on another device: type its passphrase.")
    if a.confirm("link.sync.generate", "Generate a passphrase (shown once at the end)?", not link.get("sync_keys")):
        out["generate"] = True
        return out
    while True:
        passphrase = a.secret("link.sync.passphrase", "The sync passphrase", allow_empty=False)
        if link.get("sync_keys") or len(passphrase) >= 12:
            break
        if not a.interactive:
            raise SetupError("'link.sync.passphrase' needs at least 12 characters")
        a.warn("At least 12 characters, please, or let the hub generate one.")
    out["passphrase"] = passphrase
    return out


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------


def actions(plan: "Plan") -> List[str]:
    link = plan.link
    if not link:
        return []
    where = "in the database" if plan.shape == "local" else "once the stack answers"
    name = f" as {link['name']}" if link.get("name") else ""
    out = [f"link this hub to {link['url']}{name} {where}: {MODE_LABELS[link['mode']].lower()}"
           + ("" if link.get("checked") else " (the key was not checked)")]
    uses = [what for flag, what in ((link.get("use_models"), "its models (provider hub-remote)"),
                                    (link.get("use_agents"), "its agents as tools (MCP server hub-remote)"))
            if flag]
    if uses:
        out.append("use " + " and ".join(uses))
    if link["mode"] == "catalog":
        out.append("turn its catalog on; nothing of this hub is sent there")
    sync = link.get("sync")
    if sync:
        pairs = ", ".join(f"{ws} → {sync['spaces'][ws]}" for ws in sync["workspaces"])
        if sync["encrypted"]:
            how = ("every workspace end-to-end encrypted, default and all the others, "
                   "including ones added later")
        elif sync.get("kept"):
            how = "readable there, except " + ", ".join(sync["kept"]) + " (still encrypted)"
        else:
            how = "readable there; encrypt single workspaces later in Settings"
        out.append(f"sync {pairs} both ways, {how}; the one-way mirror is turned off")
        if sync.get("generate"):
            out.append("generate the sync passphrase and show it once at the end")
        elif sync.get("passphrase"):
            out.append("unlock the sync key with the passphrase you gave")
        if link.get("catalog"):
            out.append("turn its catalog on as well")
    return out


# ---------------------------------------------------------------------------
# Apply: this checkout
# ---------------------------------------------------------------------------


def _mappings(link: Dict[str, Any]) -> List[Dict[str, Any]]:
    sync = link.get("sync") or {}
    kept = sync.get("kept") or {}
    return [{"workspace": ws, "space": sync["spaces"][ws], "encrypted": bool(sync.get("encrypted") or kept.get(ws))}
            for ws in sync.get("workspaces") or []]


def _needs_key(sync: Dict[str, Any]) -> bool:
    return bool(sync.get("encrypted") or sync.get("kept"))


def _sync_done(sync: Dict[str, Any], link: Dict[str, Any]) -> str:
    pairs = ", ".join(f"{m['workspace']} → {m['space']}" for m in _mappings(link))
    if sync.get("encrypted"):
        return f"Sync on: {pairs}; every workspace end-to-end encrypted, default and all the others."
    return f"Sync on: {pairs}; no encryption for now, set it per workspace in Settings."


def _remember_passphrase(plan: "Plan", passphrase: str) -> None:
    if passphrase:
        plan.generated.append(("Sync passphrase", passphrase))
        plan.generated_notes.append(PASSPHRASE_WARNING)


def _fill_secret_key(plan: "Plan") -> None:
    """The sync key is sealed with AGENTS_HUB_SECRET_KEY. The settings object
    may have been loaded before this run wrote a fresh key to .env."""
    from common.config import settings
    if not str(getattr(settings, "secret_key", "") or "").strip():
        key = plan.env.get("AGENTS_HUB_SECRET_KEY") or plan.cur("AGENTS_HUB_SECRET_KEY")
        if key:
            settings.secret_key = key


def _write_record(a: Asker, link: Dict[str, Any]) -> bool:
    """The link record, as hublink.link.link() writes it, without the
    runtime refresh at its end. Returns whether the other hub took the key."""
    from hublink import link as hub_link
    from hublink import store
    from hublink.remote import Remote, RemoteError
    from workspace.storage import create_workspace_folder, get_workspace_folder

    url = store.normalize_url(link["url"])
    existing = store.get() or {}
    rm = Remote(url, link["api_key"])
    try:
        me = rm.whoami()
    except RemoteError as exc:
        me = {}
        a.warn(f"{url} did not take the key now ({exc}).")
    accepted = bool(me.get("id"))
    name = str(link.get("name") or existing.get("name") or store.default_node_name()).strip()[:60]
    doc = {**store.blank(), **existing}
    mirror = dict(doc.get("mirror") or {})
    if link["mode"] != "connect":
        mirror = {k: False for k in store.MIRROR_KINDS}
    doc.update({
        "url": url, "api_key": link["api_key"], "name": name,
        "use_models": bool(link.get("use_models")), "use_agents": bool(link.get("use_agents")),
        "catalog": bool(link.get("catalog")), "mirror": mirror,
        "target_workspace": hub_link._target_workspace(me, str(existing.get("target_workspace") or "")),
        "remote": _whoami_cache(me) if accepted else dict(existing.get("remote") or link.get("remote") or {}),
        "linked_at": existing.get("linked_at") or hub_link._now_iso(),
        "last_error": "" if accepted else "the key was not accepted when ah setup linked this hub",
    })
    if existing.get("url") != url:
        # Another hub: what was synced with the old one means nothing here.
        doc.pop("sync", None)
        doc["connection"] = {}
    if accepted and mirror.get("runs"):
        try:
            doc["connection"] = hub_link._ensure_connection(rm, name, doc["target_workspace"],
                                                            dict(doc.get("connection") or {}))
        except RemoteError as exc:
            a.warn(f"Runs are not mirrored yet: no connection on the linked hub ({exc}).")
    if not get_workspace_folder("default"):
        create_workspace_folder("default")
    if doc["use_models"] and accepted:
        hub_link._register_backend(url, doc["api_key"], name)
        hub_link._seed_remote_models(rm)
    elif not doc["use_models"]:
        hub_link._remove_backend()
    if doc["use_agents"]:
        hub_link._register_mcp(url, doc["api_key"], name)
    else:
        hub_link._remove_mcp()
    store.put(doc)
    a.ok(f"Linked to {url}: {MODE_LABELS[link['mode']].lower()}.")
    return accepted


def apply_local(a: Asker, plan: "Plan") -> None:
    link = plan.link
    if not link:
        return
    try:
        accepted = _write_record(a, link)
    except Exception as exc:  # noqa: BLE001 - the rest of the setup stands; say what to do
        a.warn(f"The link was not saved ({exc}). Set it in Settings, Remote hub.")
        return
    if link["mode"] == "catalog":
        a.ok("Catalog on: the linked hub's agents, skills, flows, teams and loops are at /catalog.")
    if not accepted:
        a.note("Sync and the linked hub's models wait for a key it takes: press Save once in "
               "Settings, Remote hub.")
        return
    from hublink.remote import RemoteError
    from hubsync import client
    from hubsync.crypto import CryptoError
    try:
        if link["mode"] in SYNC_MODES:
            _sync_local(a, plan, link, client)
        elif client.config().get("enabled"):
            had = client.config()
            client.configure(enabled=False, mappings=client.mappings(had))
            a.ok("Sync turned off.")
    except (client.SyncConfigError, RemoteError, CryptoError) as exc:
        a.warn(f"Sync was not set up ({exc}). Finish it in Settings, Remote hub, Sync.")


def _sync_local(a: Asker, plan: "Plan", link: Dict[str, Any], client: Any) -> None:
    from workspace.storage import create_workspace_folder, get_workspace_folder
    sync = link["sync"]
    for ws in sync["workspaces"]:
        if not get_workspace_folder(ws):
            create_workspace_folder(ws)
            a.ok(f"Created the workspace {ws}.")
    if _needs_key(sync) and not sync.get("keep_key"):
        _fill_secret_key(plan)
        made = client.setup_key(passphrase=sync.get("passphrase") or None, generate=bool(sync.get("generate")))
        _remember_passphrase(plan, made.get("generated_passphrase") or "")
        a.ok("Sync key " + ("created" if made.get("generated_passphrase") else "unlocked") + ".")
    client.configure(enabled=True, mappings=_mappings(link), encrypt_all=bool(sync["encrypted"]))
    a.ok(_sync_done(sync, link))


# ---------------------------------------------------------------------------
# Apply: a stack in Docker, through its API
# ---------------------------------------------------------------------------


def _stack_call(plan: "Plan", headers: Dict[str, str], method: str, path: str,
                body: Optional[dict] = None, ok_missing: bool = False) -> Dict[str, Any]:
    import requests
    r = requests.request(method, f"{plan.api_base}{path}", headers=headers, timeout=60, json=body)
    if ok_missing and r.status_code == 404:
        return {"missing": True}
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except (ValueError, AttributeError):
            detail = r.text
        raise SetupError(f"{method} {path}: {r.status_code} {str(detail)[:200]}")
    return r.json() if r.content else {}


def apply_http(a: Asker, plan: "Plan", session: Optional[str]) -> None:
    import requests
    link = plan.link
    if not link:
        return
    if plan.auth == "multi" and not session:
        a.note("The linked hub was not set: no administrator session (the hub already had accounts). "
               "Set it in Settings, Remote hub.")
        return
    token = session or plan.env.get("AGENTS_HUB_API_TOKEN") or plan.cur("AGENTS_HUB_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token and plan.auth != "single" else {}

    def call(method: str, path: str, body: Optional[dict] = None, ok_missing: bool = False) -> Dict[str, Any]:
        return _stack_call(plan, headers, method, path, body, ok_missing)

    body: Dict[str, Any] = {"url": link["url"], "api_key": link["api_key"],
                            "use_models": bool(link.get("use_models")), "use_agents": bool(link.get("use_agents"))}
    if link.get("name"):
        body["name"] = link["name"]
    if link["mode"] != "connect":
        body["mirror"] = {"runs": False, "chats": False, "memory": False}
    try:
        call("PUT", "/api/hublink", body)
        a.ok(f"Linked to {link['url']}: {MODE_LABELS[link['mode']].lower()}.")
        call("PUT", "/api/catalog/settings", {"enabled": bool(link.get("catalog"))})
        status = call("GET", "/api/hublink/sync")
        if link["mode"] in SYNC_MODES:
            sync = link["sync"]
            for ws in sync["workspaces"]:
                if call("GET", f"/api/workspaces/{quote(ws, safe='')}", ok_missing=True).get("missing"):
                    call("POST", "/api/workspaces", {"name": ws})
                    a.ok(f"Created the workspace {ws}.")
            if _needs_key(sync) and not (sync.get("keep_key") or (
                    status.get("key_unlocked") and not sync.get("passphrase"))):
                key_body = ({"passphrase": sync["passphrase"]} if sync.get("passphrase")
                            else {"generate": True})
                made = call("POST", "/api/hublink/sync/key", key_body)
                _remember_passphrase(plan, made.get("generated_passphrase") or "")
                a.ok("Sync key " + ("created" if made.get("generated_passphrase") else "unlocked") + ".")
            call("PUT", "/api/hublink/sync", {"enabled": True, "encrypt_all": bool(sync["encrypted"]),
                                              "mappings": _mappings(link)})
            a.ok(_sync_done(sync, link))
        elif status.get("enabled"):
            call("PUT", "/api/hublink/sync", {"enabled": False, "mappings": list(status.get("mappings") or [])})
            a.ok("Sync turned off.")
    except (SetupError, requests.RequestException) as exc:
        a.warn(f"The linked hub was not fully set ({exc}). Finish it in Settings, Remote hub.")


__all__ = ["ENCRYPTION", "MODES", "PASSPHRASE_WARNING", "actions", "apply_http", "apply_local", "existing_link", "probe",
           "step_link"]

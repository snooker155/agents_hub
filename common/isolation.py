"""
Isolated workspaces (docs/isolation.md): a perimeter around a workspace, with
the agent free inside it.

Inside an isolated workspace an agent may do anything (a shell, code, files,
its own memory, tasks, views), and nothing it does reaches outside except
reading the sites on the workspace's list, through the hub:

* **Untrusted execution has no network at all.** ``run_shell`` and
  ``run_code`` run in a throwaway sandbox container (``--network none``,
  read only root, no capabilities, no keys in its environment) with only the
  workspace's folder mounted (:func:`run_shell_sandboxed`). ``curl`` has
  nowhere to go. The agent loop itself is the hub's own code and stays on the
  hub, next to the state it needs: a container that ran it would need write
  access to the hub's database, which would be a way out by itself.
* **No tool works around the perimeter.** A workspace's agents hold only the
  tools of :data:`ALLOWED_TOOLS`: an allowlist, so a tool added to the hub
  later is out until somebody classifies it. Connectors, MCP servers, chat
  channels, outbound notifications, deploys, git pushes, the hub's own
  operations and Blender are not on it. An agent the workspace owns cannot be
  given one (:func:`check_agent_tools` at save time); an agent shared with
  other workspaces (the main agent) has them taken off when it runs here
  (:func:`filter_tools`, at build time).
* **Reading the internet is GET only, from the listed sites only.**
  ``fetch_url``, ``web_search`` and the read only browser tools enforce
  :func:`allow_domains` (tools/web.py, tools/browser.py); ``browser_act`` is
  not allowed. The model is called by the hub, as for every workspace.
* **The hub's own ways out are closed for the workspace:** MCP servers,
  chat channel bindings, widgets and the personal memory pool
  (:func:`ensure_not_isolated`, :func:`update`).

The settings live on the workspace record (``settings``): ``isolated``,
``isolation_allow_domains``, ``isolation_changed_at``, ``isolation_changed_by``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── the allowlist ────────────────────────────────────────────────────────────

#: Tools that run inside the container and touch only the workspace's own
#: state, or nothing. Everything else is out (see the module docstring).
_INSIDE_TOOLS = frozenset({
    # files in the workspace
    "read_file", "write_file", "delete_file", "list_files", "search_text", "apply_unified_diff",
    "create_file", "list_workspace_files", "read_workspace_file", "save_workspace_file",
    # the workspace's memory
    "read_memory", "write_memory", "search_memory", "read_structured_memory",
    "write_structured_memory", "append_journal", "extract_from_text", "save_extraction",
    "remember", "recall", "record_episode", "recall_episodes", "memory_block_append",
    "memory_block_replace", "forget", "link",
    # asking the person, thinking, arithmetic
    "ask_user", "think", "plan", "save_plan", "get_plan", "list_plans", "update_plan_status",
    "delete_plan", "assess_complexity", "calculator",
    # a shell and code, the point of the perimeter
    "run_shell", "run_code",
    # the workspace's tasks and their schedule
    "create_task", "add_subtask", "get_task", "list_tasks", "update_task", "stop_task",
    "block_task", "set_task_dependencies", "create_sequence", "get_task_result",
    "schedule_task", "list_scheduled", "cancel_scheduled", "update_scheduled",
    # other agents of this workspace (each child runs isolated in turn)
    "run_agent_tool", "delegate_task_tool", "wait_for_agent_tool", "get_agent_status_tool",
    "assign_agent_tool", "start_agent_tool", "stop_agent_tool", "reject_assignment_tool",
    "list_agents_tool", "list_flows_tool", "run_flow_tool", "list_models_tool",
    "handoff_to_agent", "consult_advisor",
    # the workspace's flows, teams, loops, scenarios and their runs
    "create_flow_tool", "get_flow_tool", "modify_flow_tool", "delete_flow_tool", "validate_flow_tool",
    "list_teams_tool", "create_team_tool", "get_team_tool", "modify_team_tool", "delete_team_tool",
    "list_loops_tool", "create_loop_tool", "get_loop_tool", "modify_loop_tool", "delete_loop_tool",
    "validate_loop_tool", "run_team_tool", "get_team_run_tool", "stop_team_run_tool",
    "run_loop_tool", "get_loop_run_tool", "stop_loop_run_tool",
    # views, served by the hub's own pages only (view_serve opens a proxy: out)
    "create_view", "view_add_asset", "slides_export", "view_apply_ops", "view_get",
    "view_add_control", "view_remove_control", "view_revert", "view_snapshot", "graph_add_node",
    "graph_add_edge", "graph_remove", "graph_set_layout", "scene_camera", "scene_light",
    "scene_environment", "view_set_timeline", "sim_configure", "math_plot", "view_annotate",
    "view_link", "slides_add", "slides_style", "document_set", "view_compute", "suggest_view",
    "add_graph_node", "add_graph_edge", "delete_graph_node", "delete_graph_edge", "clear_graph",
    "read_graph_view",
    # skills attached to the agent, and the hub's own documentation
    "get_skill", "read_skill_file", "create_skill", "search_docs", "read_doc",
})

#: Reading the internet, done by the hub's own tools (GET only, the
#: workspace's domains only). browser_act is not here: clicking and typing
#: are writes.
GATEWAY_TOOLS = frozenset({"fetch_url", "web_search", "browser_open", "browser_read",
                           "browser_screenshot", "browser_close"})

ALLOWED_TOOLS = _INSIDE_TOOLS | GATEWAY_TOOLS

RULE_ID = "isolated_workspace"


def tool_allowed(tool_id: str) -> bool:
    tid = str(tool_id or "").strip()
    if not tid:
        return False
    # Declared secrets ride along as pseudo tool ids (agents/registry.py); a
    # secret only reaches the container's environment, which is inside.
    if tid.startswith("secrets:"):
        return True
    return tid in ALLOWED_TOOLS


def offenders(tool_ids: Iterable[str]) -> List[str]:
    """The tools of ``tool_ids`` an isolated workspace does not allow, in order."""
    out: List[str] = []
    for tid in tool_ids or []:
        tid = str(tid)
        if tid and not tool_allowed(tid) and tid not in out:
            out.append(tid)
    return out


def filter_tools(tool_ids: Iterable[str]) -> Tuple[List[str], List[str]]:
    """``(kept, removed)``: what an agent keeps when it runs in an isolated workspace."""
    kept: List[str] = []
    removed: List[str] = []
    for tid in tool_ids or []:
        tid = str(tid)
        (kept if tool_allowed(tid) else removed).append(tid)
    return kept, removed


# ── the workspace's side ─────────────────────────────────────────────────────

class IsolationError(ValueError):
    """An action an isolated workspace does not allow. ``status`` is the HTTP
    status a route answers with. A ValueError, like CapabilityViolation, so
    the routes' existing ``except ValueError -> 400`` handlers surface it."""

    def __init__(self, message: str, *, status: int = 409, code: str = "isolated_workspace") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _settings(workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {}
    try:
        from workspace import get_workspace_metadata
        return dict((get_workspace_metadata(str(workspace)) or {}).get("settings") or {})
    except Exception:  # noqa: BLE001 - an unreadable workspace is not known to be isolated
        log.debug("isolation: settings of %s unreadable", workspace, exc_info=True)
        return {}


def is_isolated(workspace: Optional[str]) -> bool:
    return bool(_settings(workspace).get("isolated"))


def allow_domains(workspace: Optional[str]) -> List[str]:
    """The hosts an isolated workspace may read from (exact or a subdomain of one)."""
    return [str(h).strip().lower() for h in (_settings(workspace).get("isolation_allow_domains") or [])
            if str(h).strip()]


def host_allowed(workspace: Optional[str], host: Optional[str]) -> bool:
    host = str(host or "").strip().lower().rstrip(".")
    if not host:
        return False
    for allowed in allow_domains(workspace):
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def ensure_not_isolated(workspace: Optional[str], what: str) -> None:
    """Raise :class:`IsolationError` when ``workspace`` is isolated: ``what``
    (an MCP server, a channel binding, a widget) would be a way out."""
    if is_isolated(workspace):
        raise IsolationError(f"Workspace '{workspace}' is isolated: {what} would be a way around "
                             "its perimeter, so it cannot be added here.")


def check_agent_tools(workspace: Optional[str], tools: Iterable[str]) -> None:
    """Save time: an agent an isolated workspace owns holds only allowed tools."""
    if not workspace or not is_isolated(workspace):
        return
    bad = offenders(tools)
    if bad:
        raise IsolationError(
            f"Workspace '{workspace}' is isolated; these tools reach outside its containers and "
            f"cannot be added: {', '.join(bad)}", status=400)


# ── readiness and the switch ─────────────────────────────────────────────────

def _docker_ok() -> Tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "the docker command is not installed on the hub's host"
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                             capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        return False, f"docker did not answer: {type(exc).__name__}"
    if out.returncode != 0:
        return False, (out.stderr or "docker is not running").strip()[:200]
    return True, f"Docker {out.stdout.strip()}"


def sandbox_image() -> str:
    """The image shell commands run in: ``AGENTS_HUB_ISOLATED_SHELL_IMAGE``,
    else run_code's Python image (it has bash and Python both)."""
    from common.config import live_setting, settings
    from sandbox.docker import parse_images
    chosen = (live_setting("AGENTS_HUB_ISOLATED_SHELL_IMAGE", "") or "").strip()
    return chosen or parse_images(getattr(settings, "code_runner_images", "") or "")["python"]


def readiness() -> List[Dict[str, Any]]:
    """The checks an isolated workspace needs from the hub, each ``{id, ok, detail}``."""
    checks: List[Dict[str, Any]] = []
    ok, detail = _docker_ok()
    checks.append({"id": "docker", "ok": ok,
                   "detail": detail if ok else f"{detail}: shell commands and code run in a sandbox container"})
    image = sandbox_image()
    present = False
    if ok:
        try:
            # ``image ls -q`` rather than ``image inspect``: with the
            # containerd image store, inspect by tag can miss an image that is
            # there and runs.
            listed = subprocess.run(["docker", "image", "ls", "-q", image], capture_output=True,
                                    text=True, timeout=10)
            present = listed.returncode == 0 and bool(listed.stdout.strip())
        except Exception:  # noqa: BLE001 - reported as not present
            present = False
    checks.append({"id": "sandbox_image", "ok": present,
                   "detail": f"{image} is present" if present
                   else f"pull {image} first (docker pull {image}); the sandbox has no network to pull it"})
    return checks


def ready() -> bool:
    return all(c["ok"] for c in readiness())


def offending_agents(workspace: str) -> List[Dict[str, Any]]:
    """Agents the workspace owns that hold tools it would not allow."""
    out: List[Dict[str, Any]] = []
    try:
        from agents.registry import list_agents
        specs = list_agents()
    except Exception:  # noqa: BLE001 - an unreadable registry offends nobody
        return out
    for spec in specs:
        if str(getattr(spec, "owner_workspace", "") or "") != workspace:
            continue
        bad = offenders(getattr(spec, "tools", None) or [])
        if bad:
            out.append({"agent_id": spec.id, "tools": bad})
    return out


def state(workspace: str) -> Dict[str, Any]:
    """What the Isolation section of the workspace settings shows."""
    settings = _settings(workspace)
    return {
        "workspace": workspace,
        "isolated": bool(settings.get("isolated")),
        "allow_domains": allow_domains(workspace),
        "changed_at": settings.get("isolation_changed_at"),
        "changed_by": settings.get("isolation_changed_by"),
        "readiness": readiness(),
        "offending_agents": offending_agents(workspace),
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "gateway_tools": sorted(GATEWAY_TOOLS),
    }


def update(workspace: str, *, isolated: Optional[bool] = None,
           allow_domains_: Optional[Iterable[str]] = None, actor: Optional[str] = None) -> Dict[str, Any]:
    """Change the switch and the domain list. Turning isolation on needs every
    readiness check and no offending owned agent; turning it off needs nothing."""
    from workspace import get_workspace_metadata, update_workspace_metadata
    settings = dict((get_workspace_metadata(workspace) or {}).get("settings") or {})
    if allow_domains_ is not None:
        from tools.web import clean_host_list
        try:
            settings["isolation_allow_domains"] = clean_host_list(list(allow_domains_))
        except ValueError as exc:
            raise IsolationError(f"allow_domains: {exc}", status=400) from exc
    if isolated is not None and bool(isolated) != bool(settings.get("isolated")):
        if isolated:
            failed = [c for c in readiness() if not c["ok"]]
            if failed:
                raise IsolationError("The hub is not ready for an isolated workspace: "
                                     + "; ".join(f"{c['id']}: {c['detail']}" for c in failed), status=409)
            bad = offending_agents(workspace)
            if bad:
                raise IsolationError(
                    "Remove the tools that reach outside first: " + "; ".join(
                        f"{a['agent_id']} ({', '.join(a['tools'])})" for a in bad), status=409)
            _ensure_no_ways_out(workspace)
        settings["isolated"] = bool(isolated)
        settings["isolation_changed_at"] = datetime.now(timezone.utc).isoformat()
        settings["isolation_changed_by"] = actor or None
    update_workspace_metadata(workspace, {"settings": settings})
    if isolated:
        # The personal memory pool is shared with the person's other
        # workspaces: writing to it would carry data out of the perimeter.
        try:
            from memory import personal
            personal.set_workspace_enabled(workspace, False)
        except Exception:  # noqa: BLE001 - logged; the build also leaves the pool out
            log.warning("isolation: personal memory of %s not switched off", workspace, exc_info=True)
    return state(workspace)


def _ensure_no_ways_out(workspace: str) -> None:
    """The workspace's existing ways around the perimeter, which turning
    isolation on would leave open: MCP servers, chat channel bindings,
    widgets. Each must be removed by hand first."""
    found: List[str] = []
    try:
        from mcp_client import store as mcp_store
        servers = [r.get("id") for r in mcp_store.list_servers(workspace)]
        if servers:
            found.append(f"MCP servers {', '.join(map(str, servers))}")
    except Exception:  # noqa: BLE001 - an unreadable store holds nothing to report
        log.debug("isolation: MCP servers of %s unreadable", workspace, exc_info=True)
    try:
        from connectors.channels import registry as channels
        for spec in channels.all_channels():
            # The default bot's chats bound here, and a bot of this
            # workspace's own (connectors/channels/store.py), each a way out.
            keys = spec.store.for_workspace("default").chat_keys_for_workspace(workspace)
            if keys:
                found.append(f"{spec.name} chats bound here ({len(keys)})")
            if workspace != "default" and spec.store.defines(workspace):
                found.append(f"this workspace's own {spec.name} bot")
    except Exception:  # noqa: BLE001 - see above
        log.debug("isolation: channel bindings of %s unreadable", workspace, exc_info=True)
    try:
        from connectors.telegram import telegram_store
        default_bot = telegram_store.for_workspace("default")
        bound = [b for b in (default_bot.list_bindings() or []) if b.get("workspace") == workspace]
        if bound:
            found.append(f"Telegram chats bound here ({len(bound)})")
        if workspace != "default" and telegram_store.defines(workspace):
            found.append("this workspace's own Telegram bot")
    except Exception:  # noqa: BLE001 - see above
        log.debug("isolation: telegram bindings of %s unreadable", workspace, exc_info=True)
    try:
        from widgets import store as widget_store
        widgets = widget_store.list_widgets(workspace) or []
        if widgets:
            found.append(f"widgets ({len(widgets)})")
    except Exception:  # noqa: BLE001 - see above
        log.debug("isolation: widgets of %s unreadable", workspace, exc_info=True)
    if found:
        raise IsolationError("Remove the ways around the perimeter first: " + "; ".join(found), status=409)


# ── the sandbox ──────────────────────────────────────────────────────────────

#: Limits of one sandboxed shell command, unless the run_code settings say otherwise.
SHELL_MEMORY = "1g"
SHELL_CPUS = "1"
SHELL_PIDS = 256


def build_shell_command(command: str, *, workspace_dir: str, image: str, name: str,
                        memory: str = SHELL_MEMORY, cpus: str = SHELL_CPUS,
                        pids_limit: int = SHELL_PIDS, user: Optional[str] = None) -> List[str]:
    """The ``docker run`` argv for one shell command of an isolated workspace.
    Pure: no I/O. No network, a read only root, no capabilities, nothing in
    the environment but HOME, and the workspace folder at ``/work`` (read and
    write: the agent works there)."""
    cmd = [
        "docker", "run", "--rm", "--quiet",
        "--name", name,
        "--label", "agents_hub.kind=isolated_shell",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=256m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", str(memory),
        "--cpus", str(cpus),
        "--pids-limit", str(int(pids_limit)),
        "-e", "HOME=/tmp",
        "-e", "WORK=/work",
    ]
    if user:
        cmd += ["--user", user]
    cmd += ["-v", f"{workspace_dir}:/work", "-w", "/work", image, "bash", "-lc", command]
    return cmd


def run_shell_sandboxed(command: str, workspace_dir: str, timeout: int = 30) -> Dict[str, Any]:
    """Run one shell command in the no-network sandbox. Returns
    ``{exit_code, stdout, stderr, timed_out, error}``; never raises."""
    import uuid
    from sandbox.docker import _host_path, docker_available
    if not docker_available():
        return {"exit_code": -1, "stdout": "", "stderr": "", "timed_out": False,
                "error": "docker is not available, and an isolated workspace runs shell commands "
                         "only in its sandbox"}
    name = f"agents-hub-iso-{uuid.uuid4().hex[:12]}"
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    cmd = build_shell_command(command, workspace_dir=_host_path(workspace_dir), image=sandbox_image(),
                              name=name, user=user)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return {"exit_code": -1, "stdout": _text(exc.stdout), "stderr": _text(exc.stderr),
                "timed_out": True, "error": f"timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001 - reported as the result's error
        return {"exit_code": -1, "stdout": "", "stderr": "", "timed_out": False, "error": str(exc)}
    if proc.returncode == 125:
        return {"exit_code": -1, "stdout": "", "stderr": proc.stderr or "", "timed_out": False,
                "error": "docker could not start the sandbox container"}
    return {"exit_code": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "timed_out": False, "error": ""}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def current_workspace() -> Optional[str]:
    """The workspace the running agent works in, the way the tools resolve it."""
    try:
        from common.workspace_context import resolve_active_workspace
        return resolve_active_workspace() or None
    except Exception:  # noqa: BLE001 - no workspace known
        return None


def current_isolated() -> bool:
    """Whether the running agent works in an isolated workspace."""
    return is_isolated(current_workspace())


__all__ = [
    "ALLOWED_TOOLS", "GATEWAY_TOOLS", "IsolationError", "RULE_ID", "allow_domains",
    "build_shell_command", "check_agent_tools", "current_isolated", "current_workspace",
    "ensure_not_isolated", "filter_tools", "host_allowed", "is_isolated", "offenders",
    "offending_agents", "readiness", "ready", "run_shell_sandboxed", "sandbox_image", "state",
    "tool_allowed", "update",
]

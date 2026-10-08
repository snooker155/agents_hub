"""The hub where people already work (docs/distribution.md).

Four ways out, one page (Distribution):

* the MCP server for Claude Code, Cursor and other MCP clients
  (``routes/mcp_server.py``); this module only reports its address;
* the Obsidian plugin (``clients/obsidian-agents-hub``), served as a zip;
* the Slack app: a manifest to create it from, an "Add to Slack" link, and the
  teams that installed it (``routes/slack.py`` holds the OAuth routes);
* the Teams app: the package to upload to an organisation's catalog or to
  submit to the Teams store, and the tenants that installed it.

Endpoints, each for one bot (``?workspace=``, the default's when omitted):

    GET    /api/distribution                           what is ready, and the addresses
    GET    /api/distribution/obsidian-plugin.zip       the plugin, ready to unpack
    GET    /api/distribution/slack/manifest            the Slack app manifest
    POST   /api/distribution/slack/install-link        an approved-on-arrival install link
    GET    /api/distribution/teams/app-package         the Teams app package (zip)
    GET    /api/distribution/{channel}/installs        organisations that installed it
    POST   /api/distribution/{channel}/installs        add one by id (a Teams tenant)
    PATCH  /api/distribution/{channel}/installs/{org}  approve, pick workspace and agent
    DELETE /api/distribution/{channel}/installs/{org}  forget it

Approving an organisation opens the bot to every chat in it, so the install
routes need the owner role of the bot's workspace (an admin in ``multi``
mode); reading what is installed needs a member's.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from common import identity
from common.auth import WS_OWNER

log = logging.getLogger("routes.distribution")

router = APIRouter(prefix="/api/distribution", tags=["distribution"])

_ROOT = Path(__file__).resolve().parents[3]
OBSIDIAN_DIR = _ROOT / "clients" / "obsidian-agents-hub"
OBSIDIAN_FILES = ("main.js", "manifest.json", "styles.css")
TEAMS_ASSETS = _ROOT / "connectors" / "teams" / "assets"
TEAMS_MANIFEST_VERSION = "1.17"

CHANNELS = ("slack", "teams")
APP_NAME = "Agents Hub"
SHORT_DESCRIPTION = "Talk to your Agents Hub agents where you work."
LONG_DESCRIPTION = (
    "Agents Hub runs AI agents with their own tools, memory and knowledge on your "
    "organisation's own server. Mention the bot in a channel or write to it directly: "
    "it answers as the agent your hub's operator chose for your workspace, and every "
    "conversation is recorded on the hub.")


class InstallUpdate(BaseModel):
    status: Optional[str] = None
    workspace: Optional[str] = None
    agent_id: Optional[str] = None
    name: Optional[str] = None


class InstallCreate(InstallUpdate):
    org_id: str


class InstallLink(BaseModel):
    workspace: Optional[str] = None
    agent_id: Optional[str] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _base(request: Request) -> str:
    from routes.slack import public_base
    return public_base(request)


def _ws(workspace: Optional[str]) -> str:
    from common.workspace_context import normalize_workspace_name
    return normalize_workspace_name(workspace or "") or "default"


def _service(channel: str, workspace: Optional[str]):
    """The bot of ``channel`` for ``workspace``: its own, else the default's."""
    if channel not in CHANNELS:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    from connectors.channels import registry
    svc = registry.effective_service(channel, _ws(workspace))
    if svc is None:
        raise HTTPException(status_code=404, detail=f"No {channel} bot here")
    return svc


def _require_owner(request: Request, workspace: str) -> None:
    identity.require_role(identity.request_principal(request), workspace=workspace, role=WS_OWNER)


def _query(svc: Any) -> str:
    return f"?workspace={quote(svc.workspace)}" if svc.own_workspace else ""


def _obsidian_version() -> Optional[str]:
    try:
        return json.loads((OBSIDIAN_DIR / "manifest.json").read_text("utf-8")).get("version")
    except (OSError, ValueError):
        return None


def _channel_overview(channel: str, workspace: str, base: str) -> Dict[str, Any]:
    try:
        svc = _service(channel, workspace)
    except HTTPException:
        return {"available": False}
    store = svc.store
    out: Dict[str, Any] = {
        "available": True,
        "workspace": svc.workspace,
        "configured": svc.configured(),
        "enabled": store.is_enabled(),
        "distribution": str(store.get("distribution") or "private"),
        "app_name": str(store.get("app_name") or APP_NAME),
        "installs": store.public_installs(),
    }
    if channel == "slack":
        oauth = store.is_configured("client_id", "client_secret")
        out.update({
            "mode": str(store.get("mode") or "socket"),
            "oauth_ready": oauth and bool(store.get("signing_secret")),
            "has_signing_secret": bool(store.get("signing_secret")),
            "public_install_url": (base + "/api/channels/slack/install" + _query(svc)
                                   if out["distribution"] == "public" else None),
            "redirect_url": base + "/api/channels/slack/oauth",
            "events_url": base + "/api/channels/slack/events" + _query(svc),
        })
    else:
        out.update({
            "app_id": str(store.get("app_id") or ""),
            "messaging_endpoint": base + "/api/channels/teams/messages" + _query(svc),
        })
    return out


# ── overview ─────────────────────────────────────────────────────────────────

@router.get("")
async def overview(request: Request, workspace: Optional[str] = None):
    from common.config import settings
    from common import hub_urls
    base = _base(request)
    ws = _ws(workspace)
    warnings = []
    if not hub_urls.public_base():
        warnings.append("public_url_unset")
    if not base.startswith("https://"):
        warnings.append("not_https")
    return {
        "public_url": base,
        "public_url_configured": bool(hub_urls.public_base()),
        "auth_mode": str(getattr(settings, "auth_mode", "single") or "single"),
        "warnings": warnings,
        "mcp": {"url": base + "/v1/mcp", "server_name": "agents-hub",
                "workspace_header": "X-Agents-Hub-Workspace"},
        "obsidian": {"available": all((OBSIDIAN_DIR / f).is_file() for f in OBSIDIAN_FILES),
                     "version": _obsidian_version(), "plugin_id": "agents-hub"},
        "slack": _channel_overview("slack", ws, base),
        "teams": _channel_overview("teams", ws, base),
    }


# ── Obsidian ─────────────────────────────────────────────────────────────────

def obsidian_zip() -> bytes:
    """The plugin as Obsidian expects it in ``.obsidian/plugins/agents-hub/``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in OBSIDIAN_FILES:
            path = OBSIDIAN_DIR / name
            if not path.is_file():
                raise HTTPException(status_code=404, detail=f"The plugin's {name} is missing")
            zf.write(path, f"agents-hub/{name}")
    return buf.getvalue()


@router.get("/obsidian-plugin.zip")
async def obsidian_plugin():
    return Response(obsidian_zip(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="agents-hub-obsidian.zip"'})


# ── Slack ────────────────────────────────────────────────────────────────────

def slack_manifest(svc: Any, base: str) -> Dict[str, Any]:
    """The app manifest Slack creates the app from (api.slack.com/apps, "From a
    manifest"), every URL pointing at this hub and this bot."""
    from routes.slack import BOT_EVENTS, BOT_SCOPES
    name = str(svc.store.get("app_name") or APP_NAME)[:35]
    return {
        "display_information": {"name": name, "description": SHORT_DESCRIPTION,
                                "long_description": LONG_DESCRIPTION,
                                "background_color": "#0b1f3a"},
        "features": {
            "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                         "messages_tab_read_only_enabled": False},
            "bot_user": {"display_name": name, "always_online": True},
        },
        "oauth_config": {"redirect_urls": [base + "/api/channels/slack/oauth"],
                         "scopes": {"bot": list(BOT_SCOPES)}},
        "settings": {
            "event_subscriptions": {"request_url": base + "/api/channels/slack/events" + _query(svc),
                                    "bot_events": list(BOT_EVENTS)},
            "interactivity": {"is_enabled": True,
                              "request_url": base + "/api/channels/slack/interactions" + _query(svc)},
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


@router.get("/slack/manifest")
async def get_slack_manifest(request: Request, workspace: Optional[str] = None):
    svc = _service("slack", workspace)
    manifest = slack_manifest(svc, _base(request))
    create = "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(
        json.dumps(manifest, separators=(",", ":")))
    return {"manifest": manifest, "create_app_url": create}


@router.post("/slack/install-link")
async def slack_install_link(request: Request, data: InstallLink,
                             workspace: Optional[str] = None):
    from routes.slack import authorize_url
    svc = _service("slack", workspace)
    _require_owner(request, svc.workspace)
    target = svc.own_workspace or _ws(data.workspace)
    agent_id = (data.agent_id or "").strip()
    if not agent_id:
        # Arriving approved with no agent would open the team to a bot that
        # answers nobody: pick the agent its chats run as up front.
        raise HTTPException(status_code=400, detail="Pick the agent the team's chats run as")
    _check_workspace(target)
    _check_agent(agent_id, target)
    principal = identity.request_principal(request)
    return {"url": authorize_url(request, bot_workspace=svc.workspace, approve=True,
                                 target_workspace=target, agent_id=agent_id,
                                 started_by=getattr(principal, "id", None))}


# ── Teams ────────────────────────────────────────────────────────────────────

def _or(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def teams_manifest(svc: Any, base: str) -> Dict[str, Any]:
    """manifest.json of the Teams app package (schema 1.17): one bot, in
    personal chats, teams and group chats, at this hub's messaging endpoint."""
    store = svc.store
    app_id = str(store.get("app_id") or "").strip()
    if not app_id:
        raise HTTPException(status_code=409, detail="Set the bot's Microsoft App ID first")
    name = str(store.get("app_name") or APP_NAME)
    website = _or(store.get("website_url"), base)
    host = urlsplit(base).hostname or ""
    # Teams sends a picked command as its bare title; TeamsService reads
    # these as the channel commands of the same name.
    commands = [{"title": "help", "description": "What the bot can do"},
                {"title": "reset", "description": "Start a fresh conversation"},
                {"title": "status", "description": "Which workspace and agent answer here"}]
    scopes = ["personal", "team", "groupchat"]
    return {
        "$schema": f"https://developer.microsoft.com/en-us/json-schemas/teams/v{TEAMS_MANIFEST_VERSION}/MicrosoftTeams.schema.json",
        "manifestVersion": TEAMS_MANIFEST_VERSION,
        "version": str(store.get("app_version") or "1.0.0"),
        "id": app_id,
        "developer": {
            "name": str(store.get("developer_name") or name),
            "websiteUrl": website,
            "privacyUrl": _or(store.get("privacy_url"), website),
            "termsOfUseUrl": _or(store.get("terms_url"), website),
        },
        "name": {"short": name[:30], "full": name[:100]},
        "description": {"short": SHORT_DESCRIPTION[:80], "full": LONG_DESCRIPTION[:4000]},
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#0B1F3A",
        "bots": [{"botId": app_id, "scopes": scopes, "supportsFiles": False,
                  "isNotificationOnly": False,
                  "commandLists": [{"scopes": scopes, "commands": commands}]}],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [host] if host and host not in ("localhost", "127.0.0.1") else [],
    }


def teams_package(svc: Any, base: str) -> bytes:
    manifest = teams_manifest(svc, base)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.write(TEAMS_ASSETS / "color.png", "color.png")
        zf.write(TEAMS_ASSETS / "outline.png", "outline.png")
    return buf.getvalue()


@router.get("/teams/app-package")
async def get_teams_package(request: Request, workspace: Optional[str] = None):
    svc = _service("teams", workspace)
    return Response(teams_package(svc, _base(request)), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="agents-hub-teams.zip"'})


@router.get("/teams/manifest")
async def get_teams_manifest(request: Request, workspace: Optional[str] = None):
    return {"manifest": teams_manifest(_service("teams", workspace), _base(request))}


# ── installations ────────────────────────────────────────────────────────────

def _check_agent(agent_id: str, workspace: str) -> None:
    from widgets.agents import agent_usable
    if not agent_usable(agent_id, workspace):
        raise HTTPException(status_code=400,
                            detail=f"Agent '{agent_id}' is not available in workspace '{workspace}'")


def _check_workspace(name: str) -> None:
    if name == "default":
        return
    from workspace import get_workspace_folder
    if get_workspace_folder(name) is None:
        raise HTTPException(status_code=400, detail=f"Workspace '{name}' does not exist")


def _apply(svc: Any, org_id: str, data: InstallUpdate, request: Request) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    if data.name is not None:
        fields["name"] = data.name.strip()
    workspace = svc.own_workspace or (_ws(data.workspace) if data.workspace is not None else None)
    if workspace is not None:
        _check_workspace(workspace)
        fields["workspace"] = workspace
    if data.agent_id is not None:
        fields["agent_id"] = data.agent_id.strip()
    current = svc.store.get_install(org_id) or {}
    final_ws = fields.get("workspace", current.get("workspace"))
    final_agent = fields.get("agent_id", current.get("agent_id"))
    if data.status is not None:
        if data.status not in ("pending", "approved"):
            raise HTTPException(status_code=400, detail="status is pending or approved")
        fields["status"] = data.status
        if data.status == "approved":
            if not final_ws or not final_agent:
                raise HTTPException(status_code=400,
                                    detail="Pick a workspace and an agent before approving")
            principal = identity.request_principal(request)
            fields["approved_at"] = datetime.now(timezone.utc).isoformat()
            fields["approved_by"] = getattr(principal, "id", None)
    if final_agent and final_ws:
        _check_agent(final_agent, final_ws)
    install = svc.store.upsert_install(org_id, **fields)
    _notify(svc)
    return {k: v for k, v in install.items() if k not in svc.store.INSTALL_SECRETS}


def _notify(svc: Any) -> None:
    try:
        from common.session_broker import notify_change
        notify_change(f"channel_{svc.name}", workspace=svc.workspace)
    except Exception:  # noqa: BLE001 - a missed refresh is not worth failing the write
        log.debug("distribution: change notification failed", exc_info=True)


@router.get("/{channel}/installs")
async def list_installs(channel: str, workspace: Optional[str] = None):
    svc = _service(channel, workspace)
    return {"workspace": svc.workspace, "installs": svc.store.public_installs()}


@router.post("/{channel}/installs")
async def add_install(channel: str, data: InstallCreate, request: Request,
                      workspace: Optional[str] = None):
    svc = _service(channel, workspace)
    _require_owner(request, svc.workspace)
    org_id = data.org_id.strip()
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")
    if svc.store.get_install(org_id):
        raise HTTPException(status_code=409, detail=f"'{org_id}' is already listed")
    svc.store.upsert_install(org_id, via="hub")
    return _apply(svc, org_id, data, request)


@router.patch("/{channel}/installs/{org_id}")
async def update_install(channel: str, org_id: str, data: InstallUpdate, request: Request,
                         workspace: Optional[str] = None):
    svc = _service(channel, workspace)
    _require_owner(request, svc.workspace)
    if not svc.store.get_install(org_id):
        raise HTTPException(status_code=404, detail=f"No installation '{org_id}'")
    return _apply(svc, org_id, data, request)


@router.delete("/{channel}/installs/{org_id}")
async def delete_install(channel: str, org_id: str, request: Request,
                         workspace: Optional[str] = None):
    svc = _service(channel, workspace)
    _require_owner(request, svc.workspace)
    if not svc.store.remove_install(org_id):
        raise HTTPException(status_code=404, detail=f"No installation '{org_id}'")
    _notify(svc)
    return {"ok": True}


__all__ = ["router", "slack_manifest", "teams_manifest", "teams_package", "obsidian_zip"]

"""Distribution (routes/distribution.py, docs/distribution.md): the Slack app
installed by many teams over OAuth, the Teams app package and its tenants,
the Obsidian plugin zip, and the approval that lets an organisation in.

No network: Slack's API is an in-memory fake, the agent turn a stub.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import connectors.channels.service as service_mod  # noqa: E402
from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402
from connectors.slack.service import SlackService  # noqa: E402
from connectors.teams.service import TeamsService  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example.com")
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def helper_agent():
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id="helper", name="Helper", type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent"))


@pytest.fixture
def slack_app():
    from connectors.slack import SPEC
    SPEC.store.set_config({"mode": "events", "signing_secret": "shhh", "client_id": "123.456",
                           "client_secret": "s3cret", "distribution": "private",
                           "bot_token": ""}, clear=["bot_token"])
    return SPEC


def _capture_sends(svc):
    sent = []

    async def fake(chat_key, text, **kw):
        sent.append((chat_key, text, kw))

    svc.send_text = fake
    return sent


def _fake_turn(monkeypatch, seen):
    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.append((chat_key, binding.get("workspace"), binding.get("agent_id"), text))
        return TurnResult(text="hi there", ok=True)
    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)


# ── the store and the gate ───────────────────────────────────────────────────

def test_an_approved_organisation_lets_its_chats_in_and_binds_them(monkeypatch):
    store = ChannelStore("slack-dist", secret_fields=("bot_token",))
    svc = SlackService(store)
    sent = _capture_sends(svc)
    seen = []
    _fake_turn(monkeypatch, seen)
    store.upsert_install("T1", name="Acme", bot_token="xoxb-acme", bot_user_id="UBOT")
    dm = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hello",
          "user": "U2", "ts": "1", "client_msg_id": "m1"}

    # Pending: the chat hears why, once, and nothing runs.
    asyncio.run(svc.handle_event(dict(dm), team_id="T1"))
    asyncio.run(svc.handle_event(dict(dm, client_msg_id="m2"), team_id="T1"))
    assert len(sent) == 1 and "not approved" in sent[0][1]
    assert seen == [] and store.org_of("D1") == "T1"

    store.upsert_install("T1", status="approved", workspace="default", agent_id="helper")
    asyncio.run(svc.handle_event(dict(dm, client_msg_id="m3"), team_id="T1"))
    assert seen == [("D1", "default", "helper", "hello")]
    binding = store.get_binding("D1")
    assert binding["from_install"] is True
    # The bot's own messages, as this team's bot user, are ignored.
    asyncio.run(svc.handle_event(dict(dm, client_msg_id="m4", user="UBOT"), team_id="T1"))
    assert len(seen) == 1

    # Every call for the chat uses its team's token.
    assert svc._token_for("D1") == "xoxb-acme"
    assert store.public_installs()[0]["has_bot_token"] is True
    assert "bot_token" not in store.public_installs()[0]

    # Uninstalling forgets the team, its chats and the bindings it made.
    asyncio.run(svc.handle_event({"type": "app_uninstalled"}, event_id="e9", team_id="T1"))
    assert store.get_install("T1") is None and store.get_binding("D1") is None
    assert store.org_of("D1") is None


def test_an_operator_binding_survives_an_installation(monkeypatch):
    store = ChannelStore("slack-dist2", secret_fields=("bot_token",))
    svc = SlackService(store)
    _capture_sends(svc)
    seen = []
    _fake_turn(monkeypatch, seen)
    store.upsert_install("T1", status="approved", workspace="default", agent_id="helper",
                         bot_token="xoxb-acme")
    store.upsert_binding(chat_key="C1", agent_id="other", workspace="default")
    event = {"type": "app_mention", "channel": "C1", "text": "hi", "user": "U2", "ts": "1"}
    asyncio.run(svc.handle_event(event, team_id="T1"))
    assert seen == [("C1", "default", "other", "hi")]
    store.remove_install("T1")
    assert store.get_binding("C1") is not None


def test_slack_without_a_token_of_its_own_counts_as_configured():
    store = ChannelStore("slack-dist3", secret_fields=("bot_token", "client_secret"))
    svc = SlackService(store)
    assert not svc.configured()
    store.set_config({"client_id": "1.2", "client_secret": "s"})
    assert svc.configured()
    asyncio.run(svc._connect())
    assert svc._status["identity"] == "0 installation(s)"


# ── Teams tenants ────────────────────────────────────────────────────────────

def _teams_activity(text="hello", tenant="tenant-1"):
    return {"type": "message", "id": "a1", "text": text, "serviceUrl": "https://smba.example/",
            "conversation": {"id": "conv-1", "conversationType": "personal", "tenantId": tenant},
            "channelData": {"tenant": {"id": tenant}},
            "from": {"id": "user-1"}, "recipient": {"id": "bot-1"}}


def test_a_public_teams_app_files_unknown_tenants_as_pending(monkeypatch):
    store = ChannelStore("teams-dist", secret_fields=("app_password",))
    svc = TeamsService(store)
    sent = _capture_sends(svc)
    seen = []
    _fake_turn(monkeypatch, seen)

    asyncio.run(svc.handle_activity(_teams_activity()))
    assert store.get_install("tenant-1") is None and sent == []

    store.set_config({"distribution": "public"})
    asyncio.run(svc.handle_activity(_teams_activity()))
    assert store.get_install("tenant-1")["status"] == "pending"
    assert len(sent) == 1 and "not approved" in sent[0][1]

    store.upsert_install("tenant-1", status="approved", workspace="default", agent_id="helper")
    asyncio.run(svc.handle_activity(_teams_activity("again")))
    assert seen == [("conv-1", "default", "helper", "again")]


# ── routes ───────────────────────────────────────────────────────────────────

def test_overview_reports_every_way_out(client, slack_app):
    body = client.get("/api/distribution").json()
    assert body["mcp"]["url"] == "https://hub.example.com/v1/mcp"
    assert body["obsidian"]["available"] is True and body["obsidian"]["version"]
    assert body["slack"]["oauth_ready"] is True
    assert body["slack"]["public_install_url"] is None
    assert body["teams"]["messaging_endpoint"] == "https://hub.example.com/api/channels/teams/messages"


def test_slack_manifest_points_at_this_hub(client, slack_app):
    body = client.get("/api/distribution/slack/manifest").json()
    manifest = body["manifest"]
    assert manifest["oauth_config"]["redirect_urls"] == ["https://hub.example.com/api/channels/slack/oauth"]
    assert manifest["settings"]["event_subscriptions"]["request_url"] == \
        "https://hub.example.com/api/channels/slack/events"
    assert "app_uninstalled" in manifest["settings"]["event_subscriptions"]["bot_events"]
    assert body["create_app_url"].startswith("https://api.slack.com/apps?new_app=1&manifest_json=")


def test_the_public_install_link_needs_a_public_app(client, slack_app):
    assert client.get("/api/channels/slack/install", follow_redirects=False).status_code == 404
    slack_app.store.set_config({"distribution": "public"})
    response = client.get("/api/channels/slack/install", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["client_id"] == ["123.456"] and "chat:write" in query["scope"][0]


def _fake_slack_oauth(monkeypatch, team="T9"):
    calls = []

    class _Resp:
        def json(self):
            return {"ok": True, "access_token": "xoxb-t9", "bot_user_id": "UB9",
                    "app_id": "A1", "scope": "chat:write", "team": {"id": team, "name": "Nine"}}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None, **kw):
            calls.append((url, data))
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


def test_an_install_the_operator_started_arrives_approved(client, slack_app, helper_agent,
                                                          monkeypatch):
    link = client.post("/api/distribution/slack/install-link",
                       json={"workspace": "default", "agent_id": "helper"}).json()["url"]
    state = parse_qs(urlsplit(link).query)["state"][0]
    calls = _fake_slack_oauth(monkeypatch)
    response = client.get(f"/api/channels/slack/oauth?code=abc&state={state}",
                          follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/distribution?installed=slack&org=T9"
    assert calls[0][1]["redirect_uri"] == "https://hub.example.com/api/channels/slack/oauth"
    install = slack_app.store.get_install("T9")
    assert install["status"] == "approved" and install["agent_id"] == "helper"
    assert install["bot_token"] == "xoxb-t9" and install["via"] == "hub"


def test_a_catalog_install_waits_and_a_forged_state_is_refused(client, slack_app, helper_agent,
                                                               monkeypatch):
    slack_app.store.set_config({"distribution": "public"})
    location = client.get("/api/channels/slack/install", follow_redirects=False).headers["location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    _fake_slack_oauth(monkeypatch, team="T7")
    page = client.get(f"/api/channels/slack/oauth?code=abc&state={state}")
    assert page.status_code == 200 and "approve" in page.text
    assert slack_app.store.get_install("T7")["status"] == "pending"

    forged = client.get(f"/api/channels/slack/oauth?code=abc&state={state[:-3]}xyz")
    assert forged.status_code == 400

    # Approving needs a workspace and an agent.
    refused = client.patch("/api/distribution/slack/installs/T7", json={"status": "approved"})
    assert refused.status_code == 400
    ok = client.patch("/api/distribution/slack/installs/T7",
                      json={"status": "approved", "workspace": "default", "agent_id": "helper"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert "bot_token" not in ok.json()
    listed = client.get("/api/distribution/slack/installs").json()["installs"]
    assert [i["org_id"] for i in listed] == ["T7"]
    assert client.delete("/api/distribution/slack/installs/T7").status_code == 200
    assert slack_app.store.get_install("T7") is None


def test_the_teams_package_is_a_valid_upload(client, helper_agent):
    from connectors.teams import SPEC
    SPEC.store.set_config({"app_id": "", "app_name": ""})
    assert client.get("/api/distribution/teams/app-package").status_code == 409
    SPEC.store.set_config({"app_id": "00000000-1111-2222-3333-444444444444",
                           "app_name": "Acme Agents", "developer_name": "Acme"})
    response = client.get("/api/distribution/teams/app-package")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == ["color.png", "manifest.json", "outline.png"]
        manifest = json.loads(zf.read("manifest.json"))
        from PIL import Image
        assert Image.open(io.BytesIO(zf.read("color.png"))).size == (192, 192)
        outline = Image.open(io.BytesIO(zf.read("outline.png")))
        assert outline.size == (32, 32) and outline.mode == "RGBA"
    assert manifest["id"] == manifest["bots"][0]["botId"] == "00000000-1111-2222-3333-444444444444"
    assert manifest["name"]["short"] == "Acme Agents"
    assert manifest["developer"]["privacyUrl"].startswith("https://")
    assert manifest["validDomains"] == ["hub.example.com"]

    added = client.post("/api/distribution/teams/installs",
                        json={"org_id": "tenant-x", "status": "approved",
                              "workspace": "default", "agent_id": "helper"})
    assert added.status_code == 200 and added.json()["status"] == "approved"
    assert client.post("/api/distribution/teams/installs",
                       json={"org_id": "tenant-x"}).status_code == 409


def test_the_obsidian_zip_unpacks_into_the_plugin_folder(client):
    response = client.get("/api/distribution/obsidian-plugin.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == ["agents-hub/main.js", "agents-hub/manifest.json",
                                         "agents-hub/styles.css"]
        assert json.loads(zf.read("agents-hub/manifest.json"))["id"] == "agents-hub"


def test_a_teams_command_from_the_list_arrives_bare_and_still_works(monkeypatch):
    store = ChannelStore("teams-dist-cmd", secret_fields=("app_password",))
    svc = TeamsService(store)
    sent = _capture_sends(svc)
    store.upsert_install("tenant-1", status="approved", workspace="default", agent_id="helper")
    store.note_chat_org("conv-1", "tenant-1")
    asyncio.run(svc.handle_activity(_teams_activity("help")))
    assert sent and "/help" in sent[0][1]


def test_an_install_link_needs_an_agent(client, slack_app):
    refused = client.post("/api/distribution/slack/install-link", json={"workspace": "default"})
    assert refused.status_code == 400

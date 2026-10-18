"""Watchers (watchers/, docs/watchers.md): observers that wake a proactive agent.

The IMAP and HTTP probes run against fakes (no network, no mail server); the
service's pass is exercised end to end into a pulse's pending events with the
plans stores pointed at throwaway files, as the proactive tests do.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.registry import AgentSpec, add_agent, replace_all_raw
from plans import service as ps
from plans.storage import FireStore, PlanStore
from proactive import service as proactive
from watchers import kinds, service
from watchers.store import store

UTC = timezone.utc
BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    replace_all_raw([])
    for w in store.list():
        store.delete(w.id)
    monkeypatch.setattr(ps, "plan_store", PlanStore(path=tmp_path / "plans.json"))
    monkeypatch.setattr(ps, "fire_store", FireStore())
    yield
    replace_all_raw([])
    for w in store.list():
        store.delete(w.id)


@pytest.fixture
def secrets(monkeypatch):
    values = {"HEADERS": json.dumps({"Authorization": "Bearer t"})}
    monkeypatch.setattr(service, "_secret_resolver", lambda ws: (lambda name: values.get(name)))
    return values


def _watcher(**kw):
    data = dict(name="status", kind="http",
                config={"url": "https://example.org/status.json", "json_path": "state"})
    data.update(kw)
    return service.create("default", data)


def _listener(agent_id, watcher_id, workspace="default"):
    add_agent(AgentSpec(id=agent_id, name=agent_id.title(), type="langchain",
                        entrypoint="agents.definitions.demo:build", owner_workspace=workspace))
    return proactive.save_profile(agent_id, {
        "enabled": True, "cron": "0 0 1 1 *", "brief": "watch the status page",
        "triggers": [{"kind": "watch", "watcher_id": watcher_id}],
    })


# -------------------- model and config --------------------

def test_config_validation():
    cfg = kinds.validate_config("http", {"url": "https://x.org/a"})
    assert cfg["method"] == "GET" and cfg["json_path"] == ""
    with pytest.raises(ValueError, match="url"):
        kinds.validate_config("http", {})
    with pytest.raises(ValueError, match="http"):
        kinds.validate_config("http", {"url": "ftp://x"})
    with pytest.raises(ValueError, match="GET or HEAD"):
        kinds.validate_config("http", {"url": "https://x.org", "method": "POST"})
    with pytest.raises(ValueError, match="unknown watcher kind"):
        kinds.validate_config("carrier_pigeon", {})


def test_create_update_and_interval_bounds():
    w = _watcher(interval_seconds=60)
    assert w.id.startswith("watch_") and w.active and w.state == {}
    with pytest.raises(service.WatcherError, match="interval_seconds"):
        service.update(w.id, {"interval_seconds": 1})
    with pytest.raises(service.WatcherError, match="name"):
        service.create("default", {"name": "", "kind": "http", "config": {"url": "https://x.org"}})
    # Changing the source resets the observed state.
    store.update(w.id, state={"hash": "old"}, last_error="old")
    again = service.update(w.id, {"config": {**w.config, "json_path": "other"}})
    assert again.state == {} and again.last_error is None
    assert service.list_watchers("default")[0].id == w.id
    assert service.list_watchers("other") == []


def test_pause_resume_and_disable():
    w = _watcher()
    assert service.pause(w.id).paused_reason == "manual"
    assert not service.get(w.id).active
    assert service.resume(w.id).active
    off = service.update(w.id, {"enabled": False})
    assert not off.active
    assert not service.is_due(off)


# -------------------- probes --------------------

class _Page:
    """A JSON resource whose value the test changes between probes."""

    def __init__(self, state):
        self.state = state

    def fetch(self, cfg, headers):
        return json.dumps({"state": self.state})


def test_http_probe_watches_a_json_field():
    w = service.create("default", {"name": "status", "kind": "http",
                                   "config": {"url": "https://example.org/api", "json_path": "data.status"}})
    body = {"data": {"status": "green", "noise": 1}}
    fetch = lambda cfg, headers: json.dumps(body)  # noqa: E731
    first = kinds.probe_http(w, lambda n: None, fetch=fetch)
    assert first.events == [] and first.summary == "baseline taken"
    w = store.update(w.id, state=first.state)
    body["data"]["noise"] = 2
    same = kinds.probe_http(w, lambda n: None, fetch=fetch)
    assert same.events == [] and same.summary == "unchanged"
    body["data"]["status"] = "red"
    changed = kinds.probe_http(w, lambda n: None, fetch=fetch)
    assert len(changed.events) == 1 and changed.events[0]["value"] == "red"
    assert changed.events[0]["previous_preview"] == "green"
    with pytest.raises(kinds.ProbeError, match="matched nothing"):
        kinds.probe_http(w, lambda n: None, fetch=lambda cfg, h: json.dumps({"data": {}}))


def test_http_probe_sends_headers_from_a_secret():
    w = service.create("default", {"name": "api", "kind": "http",
                                   "config": {"url": "https://example.org/api", "headers_secret": "HEADERS"}})
    seen = {}

    def fetch(cfg, headers):
        seen.update(headers)
        return "ok"
    kinds.probe_http(w, lambda n: json.dumps({"Authorization": "Bearer t"}), fetch=fetch)
    assert seen == {"Authorization": "Bearer t"}
    with pytest.raises(kinds.ProbeError, match="JSON object"):
        kinds.probe_http(w, lambda n: "not json", fetch=fetch)


def test_http_fetch_refuses_private_hosts(monkeypatch):
    from common import ssrf
    monkeypatch.setattr(ssrf, "resolve_and_check", lambda host: (False, "host 'x' resolves to non-public address"))
    with pytest.raises(kinds.ProbeError, match="non-public"):
        kinds._http_fetch({"url": "http://x/", "method": "GET"}, {})


# -------------------- the pass: probe, wake, record --------------------

def test_run_due_wakes_the_listening_agent(secrets, monkeypatch):
    w = _watcher(interval_seconds=60)
    profile = _listener("statusbot", w.id)
    _listener("deaf", "watch_other")
    page = _Page("up")
    monkeypatch.setattr(kinds, "_http_fetch", page.fetch)

    first = service.run_due()
    assert len(first) == 1 and first[0]["ok"] and first[0]["events"] == 0
    assert ps.get_job(profile["job_id"]).pending_events == []
    stored = service.get(w.id)
    assert stored.state.get("hash") and stored.last_checked_at is not None

    # Not due again until the interval passed.
    assert service.run_due() == []
    store.update(w.id, last_checked_at=datetime.now(UTC) - timedelta(seconds=120))
    page.state = "down"
    second = service.run_due()
    assert second[0]["events"] == 1 and second[0]["woken"] == ["statusbot"]
    job = ps.get_job(profile["job_id"])
    assert len(job.pending_events) == 1
    event = job.pending_events[0]
    assert event["kind"] == "watch" and event["data"]["watcher_id"] == w.id
    assert event["data"]["value"] == "down" and "[status]" in event["summary"]
    assert job.run_at <= datetime.now(UTC) + timedelta(seconds=31)
    assert service.get(w.id).fired == 1
    assert ps.get_job(proactive.get_profile("deaf")["job_id"]).pending_events == []


def test_errors_count_and_pause(secrets, monkeypatch):
    w = _watcher(auto_pause_after=2)
    sent = []
    monkeypatch.setattr(ps, "create_notification", lambda **kw: sent.append(kw))

    def boom(cfg, headers):
        raise kinds.ProbeError("HTTP request failed: refused")
    monkeypatch.setattr(kinds, "_http_fetch", boom)

    r1 = service.probe_once(w.id)
    assert r1["ok"] is False and r1["consecutive_errors"] == 1 and not r1["paused"]
    r2 = service.probe_once(w.id)
    assert r2["paused"] is True
    stored = service.get(w.id)
    assert stored.paused_reason == "errors" and "refused" in stored.last_error
    assert len(sent) == 1 and "paused" in sent[0]["title"]
    assert not service.is_due(stored)
    assert service.resume(w.id).consecutive_errors == 0


def test_dry_run_reports_without_storing_or_waking(secrets, monkeypatch):
    w = _watcher()
    profile = _listener("statusbot", w.id)
    page = _Page("up")
    monkeypatch.setattr(kinds, "_http_fetch", page.fetch)
    service.probe_once(w.id, dry_run=False)
    baseline = service.get(w.id).state
    page.state = "down"
    result = service.probe_once(w.id, dry_run=True)
    assert result["dry_run"] and len(result["events"]) == 1 and result["would_wake"] == ["statusbot"]
    assert service.get(w.id).state == baseline
    assert ps.get_job(profile["job_id"]).pending_events == []


def test_summary_lists_listeners():
    w = _watcher()
    _listener("statusbot", w.id)
    body = service.summary("default")
    assert body["active"] == 1 and body["total"] == 1
    assert body["watchers"][0]["listeners"] == [{"agent_id": "statusbot", "name": "Statusbot"}]
    assert body["watchers"][0]["next_check_at"] is None  # never checked yet


def test_profile_requires_a_watcher_id():
    add_agent(AgentSpec(id="a", name="A", type="langchain", entrypoint="agents.definitions.demo:build"))
    with pytest.raises(ValueError, match="watcher_id"):
        proactive.save_profile("a", {"enabled": True, "triggers": [{"kind": "watch"}]})


def test_watch_is_an_untrusted_trigger():
    from proactive.events import UNTRUSTED_TRIGGER_KINDS
    assert "watch" in UNTRUSTED_TRIGGER_KINDS


# -------------------- routes --------------------

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import watchers as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_routes_crud_and_actions(client, secrets, monkeypatch):
    kinds_resp = client.get("/api/watchers/kinds").json()
    assert {k["kind"] for k in kinds_resp["kinds"]} == {"http"}

    resp = client.post("/api/watchers", json={
        "workspace": "default", "name": "status page", "kind": "http",
        "config": {"url": "https://example.org/status.json", "json_path": "state"}, "interval_seconds": 30,
    })
    assert resp.status_code == 201, resp.text
    wid = resp.json()["id"]
    assert resp.json()["active"] is True

    bad = client.post("/api/watchers", json={"workspace": "default", "name": "x", "kind": "http", "config": {}})
    assert bad.status_code == 400 and "url" in bad.json()["detail"]

    assert client.get("/api/watchers", params={"workspace": "default"}).json()[0]["id"] == wid
    assert client.get("/api/watchers/summary", params={"workspace": "default"}).json()["active"] == 1

    assert client.post(f"/api/watchers/{wid}/pause").json()["paused_reason"] == "manual"
    assert client.post(f"/api/watchers/{wid}/resume").json()["active"] is True

    monkeypatch.setattr(kinds, "_http_fetch", lambda cfg, headers: json.dumps({"state": "up"}))
    probe = client.post(f"/api/watchers/{wid}/probe").json()
    assert probe["ok"] and probe["dry_run"] and probe["summary"] == "baseline taken"
    assert client.get(f"/api/watchers/{wid}").json()["state"] == {}
    real = client.post(f"/api/watchers/{wid}/probe", params={"dry_run": "false"}).json()
    assert real["ok"] and real["watcher"]["state"]["hash"]

    patched = client.patch(f"/api/watchers/{wid}", json={"interval_seconds": 45})
    assert patched.json()["interval_seconds"] == 45
    assert client.delete(f"/api/watchers/{wid}").json() == {"deleted": True}
    assert client.get(f"/api/watchers/{wid}").status_code == 404

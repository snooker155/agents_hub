"""Project deployments (deployments/, docs/project-deployments.md).

Under test: the record and its validation, the detector's proposals for the
usual folder shapes, the docker runner's command line (through a captured
``subprocess.run``), a real local-mode deploy of Python's http.server (start,
health, logs, stop, and the supervisor restarting an exited service and
pausing a crash loop), the routes, the published ``/apps/<slug>/`` page with
its share key, the ``deployment`` preview ticket, the hub-internal exemption
in the hub's ``validate_url`` and the browser service's policy, and the
agent tools.

Run: ``python -m pytest tests/test_project_deployments.py -q``
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = str(ROOT / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from deployments import detect, runner, service, store  # noqa: E402
from deployments.models import DeployService, ProjectDeployment, ServiceRuntime  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_store():
    store.reset_cache()
    yield
    store.reset_cache()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fake project whose folder is ``tmp_path / 'app'``: the service layer's
    two lookups are pointed at it, so no ProjectStore or workspace folder is
    involved."""
    folder = tmp_path / "app"
    folder.mkdir()
    proj = SimpleNamespace(id="proj-1", name="Demo App", workspace="default",
                           repo=SimpleNamespace(local_path=None))
    monkeypatch.setattr(service, "require_project", lambda pid: proj if pid == proj.id else (_ for _ in ()).throw(service.DeploymentError("Project not found", 404)))
    monkeypatch.setattr(service, "_projects", lambda: SimpleNamespace(get=lambda pid: proj if pid == proj.id else None, list=lambda: [proj]))
    monkeypatch.setattr(service, "project_root", lambda p: folder)
    proj.folder = folder
    return proj


@pytest.fixture
def client(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _http_server_service(port: int, name: str = "web") -> DeployService:
    return DeployService(name=name, kind="frontend", language="python", port=port,
                         command=f"{sys.executable} -m http.server $PORT --bind 127.0.0.1",
                         health_path="/")


# ── the record ───────────────────────────────────────────────────────────────

def test_a_service_name_and_port_are_validated():
    with pytest.raises(ValueError):
        DeployService(name="Bad Name", port=80)
    with pytest.raises(ValueError):
        DeployService(name="ok", port=70000)
    with pytest.raises(ValueError):
        DeployService(name="ok", path="../escape")
    svc = DeployService(name="Web", port="3000", env={"A": 1})
    assert svc.name == "web" and svc.port == 3000 and svc.env == {"A": "1"}


def test_the_primary_service_is_the_frontend_unless_named():
    dep = ProjectDeployment(project_id="p", workspace="w", services=[
        DeployService(name="api", kind="backend", port=8000),
        DeployService(name="web", kind="frontend", port=5173),
    ])
    assert dep.primary().name == "web"
    dep.primary_service = "api"
    assert dep.primary().name == "api"
    assert dep.service("web").name == "web" and dep.service("nope") is None


def test_the_journal_is_capped():
    dep = ProjectDeployment(project_id="p", workspace="w")
    for i in range(250):
        dep.add_event("x", str(i))
    assert len(dep.events) == 200 and dep.events[-1].detail == "249"


def test_two_services_with_one_name_are_refused():
    with pytest.raises(ValueError):
        ProjectDeployment(project_id="p", workspace="w", services=[
            DeployService(name="a", port=1), DeployService(name="a", port=2)])


# ── detection ────────────────────────────────────────────────────────────────

def test_a_vite_project_is_a_node_frontend_on_5173(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite"}, "devDependencies": {"vite": "^5"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    dep = detect.detect(tmp_path, project_id="p", workspace="w")
    assert dep.mode == "docker" and len(dep.services) == 1
    svc = dep.services[0]
    assert svc.kind == "frontend" and svc.port == 5173 and svc.install_command == "npm ci"
    assert "--host 0.0.0.0" in svc.command


def test_frontend_and_backend_subfolders_become_two_services(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(json.dumps({
        "scripts": {"dev": "next dev"}, "dependencies": {"next": "14", "react": "18"}}))
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "backend" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    dep = detect.detect(tmp_path, project_id="p", workspace="w")
    by_name = {s.name: s for s in dep.services}
    assert set(by_name) == {"frontend", "backend"}
    assert by_name["frontend"].kind == "frontend" and by_name["frontend"].port == 3000
    assert by_name["backend"].kind == "backend" and by_name["backend"].command.startswith("uvicorn main:app")
    assert "pip install -r requirements.txt" in by_name["backend"].install_command
    assert dep.primary().name == "frontend"


def test_a_compose_file_makes_a_compose_deployment(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(textwrap.dedent("""
        services:
          web:
            build: ./web
            ports: ["8080:80"]
          db:
            image: postgres
    """))
    dep = detect.detect(tmp_path, project_id="p", workspace="w")
    assert dep.mode == "compose" and dep.compose_file == "docker-compose.yml"
    assert [s.name for s in dep.services] == ["web"] and dep.services[0].port == 80


def test_a_bare_dockerfile_uses_its_expose_port(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM nginx\nEXPOSE 8081\n")
    dep = detect.detect(tmp_path, project_id="p", workspace="w")
    assert dep.services[0].dockerfile == "Dockerfile" and dep.services[0].port == 8081


def test_an_empty_or_missing_folder_proposes_nothing(tmp_path):
    assert detect.detect(tmp_path, project_id="p", workspace="w").services == []
    assert detect.detect(None, project_id="p", workspace="w").services == []


# ── the docker runner's command line ─────────────────────────────────────────

def test_docker_run_publishes_the_port_and_mounts_the_folder(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="cid\n", stderr="")

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr("managers.container_manager.get_or_create_network", lambda: "agents-hub")
    monkeypatch.setattr(runner, "free_port", lambda: 45678)
    monkeypatch.setattr(runner, "in_container", lambda: False)
    dep = ProjectDeployment(project_id="p", workspace="w", env={"SHARED": "1"})
    svc = DeployService(name="web", language="node", port=5173, path="frontend",
                        install_command="npm ci", command="npm run dev", env={"OWN": "2"})
    rt = runner.docker_runner.start(dep, tmp_path, svc, public_path="/apps/demo-abc/",
                                    limits={"memory": "512m"})
    assert rt.state == "starting" and rt.host_port == 45678 and rt.url == "http://127.0.0.1:45678"
    run_cmd = [c for c in calls if c[:2] == ["docker", "run"]][0]
    text = " ".join(run_cmd)
    assert "-p 127.0.0.1:45678:5173" in text
    assert "--memory 512m" in text
    assert f"{(tmp_path / 'frontend').resolve()}:/app" in text
    assert "-e SHARED=1" in text and "-e OWN=2" in text and "-e PORT=5173" in text
    assert "-e AGENTS_HUB_PUBLIC_PATH=/apps/demo-abc/" in text
    assert run_cmd[-3:] == ["sh", "-lc", "npm ci && npm run dev"]
    assert run_cmd[-4] == "node:20-bookworm-slim"
    # The previous container of the same name is removed first.
    assert calls[0][:3] == ["docker", "rm", "-f"]


def test_docker_run_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=125, stdout="", stderr="docker: no such image\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr("managers.container_manager.get_or_create_network", lambda: "agents-hub")
    dep = ProjectDeployment(project_id="p", workspace="w")
    rt = runner.docker_runner.start(dep, tmp_path, DeployService(name="web", port=80, command="x"),
                                    public_path="/apps/x/")
    assert rt.state == "failed" and "no such image" in rt.error


# ── a real local deploy ──────────────────────────────────────────────────────

def _wait(pred, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return False


def test_local_mode_deploys_a_python_http_server_end_to_end(project):
    port = runner.free_port()
    dep = service.for_project(project.id, create=True)
    assert dep is not None and dep.slug and dep.share_token
    dep = service.update_config(project.id, {"mode": "local", "services": [
        _http_server_service(port).model_dump()]})
    try:
        dep = service.deploy(dep, by="test")
        assert dep.desired == "running"
        assert _wait(lambda: service.refresh(store.get(dep.id)).status == "running")
        current = store.get(dep.id)
        rt = current.runtime["web"]
        assert rt.pid and rt.host_port == port and rt.healthy
        assert service.target_url(current) == f"http://127.0.0.1:{port}"
        assert service.links(current)["path"] == f"/apps/{current.slug}/"
        # Its output landed in the log file and the journal saw the start.
        httpx.get(f"http://127.0.0.1:{port}/", timeout=5)
        assert _wait(lambda: "GET /" in service.logs(store.get(dep.id))["text"], timeout=5)
        kinds = [e.kind for e in store.get(dep.id).events]
        assert "deploy" in kinds and "service_started" in kinds
    finally:
        dep = service.stop(store.get(dep.id), by="test")
    assert dep.status == "stopped" and dep.runtime == {} and dep.desired == "stopped"
    assert not runner.port_open(port)


def test_a_service_that_ignores_port_is_found_on_the_port_it_took(project, monkeypatch):
    """The app's own script listens on a port of its own (here a second free
    port) instead of $PORT: the hub adopts the real one, journals it and calls
    the service running, with the reason recorded while it did not answer."""
    declared, actual = runner.free_port(), runner.free_port()
    dep = service.for_project(project.id, create=True)
    dep = service.update_config(project.id, {"mode": "local", "services": [
        {"name": "web", "kind": "frontend", "language": "python", "port": declared,
         "command": f"{sys.executable} -m http.server {actual} --bind 127.0.0.1", "health_path": "/"}]})
    monkeypatch.setattr(service, "STARTUP_GRACE_SECONDS", 0)
    try:
        dep = service.deploy(dep)
        assert _wait(lambda: service.refresh(store.get(dep.id)).runtime["web"].host_port == actual)
        current = service.refresh(store.get(dep.id))
        assert current.status == "running" and current.runtime["web"].healthy
        assert current.runtime["web"].url == f"http://127.0.0.1:{actual}"
        assert any(e.kind == "port_adopted" and str(actual) in e.detail for e in current.events)
    finally:
        service.stop(store.get(dep.id))


def test_health_of_names_the_reason():
    svc = DeployService(name="web", port=5173, health_path="/")
    ok, reason = runner.health_of(ServiceRuntime(state="running"), svc)
    assert not ok and "no port" in reason
    ok, reason = runner.health_of(ServiceRuntime(state="running", host_port=runner.free_port()), svc)
    assert not ok and "nothing answers" in reason and "5173" in reason


def test_a_missing_command_fails_the_deploy(project):
    dep = service.for_project(project.id, create=True)
    dep = service.update_config(project.id, {"mode": "local", "services": [
        {"name": "web", "port": runner.free_port(), "language": "python", "path": "missing"}]})
    dep = service.deploy(dep)
    assert dep.status == "failed" and dep.desired == "stopped"
    assert "service folder not found" in (dep.last_error or "")


def test_the_supervisor_restarts_an_exited_service_and_pauses_a_crash_loop(project, monkeypatch):
    dep = service.for_project(project.id, create=True)
    dep = service.update_config(project.id, {"mode": "local", "services": [
        {"name": "web", "port": runner.free_port(), "language": "python", "command": "exit 3",
         "health_path": None}]})
    monkeypatch.setattr(service, "STARTUP_GRACE_SECONDS", 0)
    dep = service.deploy(dep)
    assert dep.desired == "running"
    from deployments import supervisor
    paused = False
    for _ in range(8):
        assert _wait(lambda: service.refresh(store.get(dep.id)).runtime["web"].state == "exited"
                     or store.get(dep.id).status == "paused", timeout=10)
        supervisor.reconcile_once()
        current = store.get(dep.id)
        if current.status == "paused":
            paused = True
            break
    assert paused, "three restarts within the window should pause the deployment"
    current = store.get(dep.id)
    assert current.restart_count >= 3 and "crash loop" in current.paused_reason
    service.stop(current)


# ── routes ───────────────────────────────────────────────────────────────────

def test_the_routes_configure_and_expose_a_deployment(project, client):
    (project.folder / "index.html").write_text("<h1>hi</h1>")
    r = client.get(f"/api/projects/{project.id}/deployment")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stopped" and body["services"][0]["language"] == "static"
    assert body["links"]["path"].startswith("/apps/") and body["share_token"]
    assert body["links"]["external_url"].endswith(f"?key={body['share_token']}")

    r = client.put(f"/api/projects/{project.id}/deployment", json={
        "mode": "local", "services": [{"name": "web", "port": 8123, "command": "true"}],
        "env": {"X": "1"}, "primary_service": "web"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local" and r.json()["env"] == {"X": "1"}

    r = client.put(f"/api/projects/{project.id}/deployment", json={"primary_service": "nope"})
    assert r.status_code == 422

    r = client.put(f"/api/projects/{project.id}/deployment/visibility", json={"visibility": "public"})
    assert r.status_code == 200 and r.json()["visibility"] == "public"
    assert "key=" not in r.json()["links"]["external_url"]

    old_slug = r.json()["slug"]
    r = client.post(f"/api/projects/{project.id}/deployment/link/reset")
    assert r.status_code == 200 and r.json()["slug"] != old_slug

    r = client.get(f"/api/projects/{project.id}/deployment/events")
    assert r.status_code == 200 and {e["kind"] for e in r.json()["items"]} >= {"created", "configured", "visibility", "link_reset"}

    r = client.get("/api/deployments/apps")
    assert r.status_code == 200
    assert [a["project_id"] for a in r.json()["items"]] == [project.id]
    assert "share_token" not in r.json()["items"][0]

    r = client.get("/api/projects/nope/deployment")
    assert r.status_code == 404


def test_deploy_route_refuses_an_empty_deployment(project, client):
    client.get(f"/api/projects/{project.id}/deployment")
    r = client.post(f"/api/projects/{project.id}/deployment/deploy")
    assert r.status_code == 422


def test_the_deploy_route_answers_at_once_and_the_page_can_poll(project, client, monkeypatch):
    port = runner.free_port()
    client.get(f"/api/projects/{project.id}/deployment")
    client.put(f"/api/projects/{project.id}/deployment", json={
        "mode": "local", "services": [_http_server_service(port).model_dump()]})
    r = client.post(f"/api/projects/{project.id}/deployment/deploy")
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("building", "starting")
    try:
        assert _wait(lambda: client.get(f"/api/projects/{project.id}/deployment").json()["status"] == "running")
        r = client.get(f"/api/projects/{project.id}/deployment/logs", params={"service": "web"})
        assert r.status_code == 200 and r.json()["service"] == "web"
    finally:
        r = client.post(f"/api/projects/{project.id}/deployment/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopped"


# ── /apps/<slug>/ ────────────────────────────────────────────────────────────

@pytest.fixture
def running_app(project, monkeypatch):
    """A deployment whose primary service 'runs' at a fake upstream served by
    an httpx.MockTransport, so the /apps/ proxy is tested without a process."""
    dep = service.for_project(project.id, create=True)
    dep = service.update_config(project.id, {"mode": "local", "services": [
        {"name": "web", "kind": "frontend", "port": 9931, "command": "true"},
        {"name": "api", "kind": "backend", "port": 9932, "command": "true"}]})

    def mark(d):
        d.desired = "running"
        d.status = "running"
        d.runtime["web"] = ServiceRuntime(state="running", host_port=9931, url="http://localhost:9931", healthy=True)
        d.runtime["api"] = ServiceRuntime(state="running", host_port=9932, url="http://localhost:9932", healthy=True)
    dep = store.mutate(dep.id, mark)
    monkeypatch.setattr(service, "refresh", lambda d: d)

    from routes import preview
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "/landing"})
        return httpx.Response(200, text=f'<html><head></head><body><a href="/x">{request.url.host}:{request.url.port}{request.url.path}</a></body></html>',
                              headers={"content-type": "text/html"})

    monkeypatch.setattr(preview, "_client_factory",
                        lambda pinned: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False))
    return SimpleNamespace(dep=dep, id=dep.id, slug=dep.slug, share_token=dep.share_token, seen=seen)


def test_a_private_app_needs_its_key_once_then_a_cookie(running_app, client):
    slug, key = running_app.slug, running_app.share_token
    assert client.get(f"/apps/{slug}/").status_code == 403
    assert client.get(f"/apps/{slug}/?key=wrong").status_code == 403

    r = client.get(f"/apps/{slug}/?key={key}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/apps/{slug}/"
    assert "ah_app_" in r.headers.get("set-cookie", "")

    r = client.get(f"/apps/{slug}/")  # the cookie carries on
    assert r.status_code == 200
    assert f'<base href="/apps/{slug}/">' in r.text and f'href="/apps/{slug}/x"' in r.text
    assert "localhost:9931/" in r.text

    # Another service by its name; a redirect stays under the prefix.
    r = client.get(f"/apps/{slug}/~api/go", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/apps/{slug}/~api/landing"
    assert running_app.seen[-1].url.port == 9932

    # No slash: redirected to the canonical form, query kept.
    r = client.get(f"/apps/{slug}?key={key}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/apps/{slug}/?key={key}"


def test_a_public_app_needs_no_key_and_a_stopped_one_says_so(running_app, client, project):
    slug = running_app.slug
    service.set_visibility(running_app.dep, "public")
    assert client.get(f"/apps/{slug}/").status_code == 200
    store.mutate(running_app.id, lambda d: d.runtime.clear())
    assert client.get(f"/apps/{slug}/").status_code == 503
    assert client.get("/apps/no-such-app/").status_code == 404


def test_a_preview_ticket_of_kind_deployment(running_app, client):
    r = client.post("/api/preview/tickets", json={"kind": "deployment", "deployment_id": running_app.id})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    r = client.get(url)
    assert r.status_code == 200 and "localhost:9931/" in r.text
    r = client.post("/api/preview/tickets", json={"kind": "deployment", "deployment_id": running_app.id, "service": "api"})
    assert client.get(r.json()["url"]).text.count("localhost:9932/") == 1
    r = client.post("/api/preview/tickets", json={"kind": "deployment", "deployment_id": running_app.id, "service": "nope"})
    assert r.status_code == 404
    r = client.post("/api/preview/tickets/renew", json={"ticket": url.split("/")[2]})
    assert r.status_code == 200


# ── the hub's own pages are reachable for the browser ────────────────────────

def test_is_internal_url_covers_only_the_app_paths_on_the_hubs_origins(monkeypatch):
    from common import hub_urls
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example.com")
    monkeypatch.setenv("AGENTS_HUB_BROWSER_HUB_URL", "http://host.docker.internal:8000")
    monkeypatch.setattr(hub_urls, "_setting", lambda key: __import__("os").environ.get(key, ""))
    assert hub_urls.internal_origins() == ["https://hub.example.com", "http://host.docker.internal:8000"]
    assert hub_urls.is_internal_url("https://hub.example.com/apps/demo-1a2b/")
    assert hub_urls.is_internal_url("http://host.docker.internal:8000/preview/t/x.js")
    assert not hub_urls.is_internal_url("https://hub.example.com/api/agents")
    assert not hub_urls.is_internal_url("https://hub.example.com/")
    assert not hub_urls.is_internal_url("https://evil.example.com/apps/demo-1a2b/")
    assert not hub_urls.is_internal_url("http://host.docker.internal:9000/apps/x/")


def test_validate_url_lets_the_hubs_app_pages_through(monkeypatch):
    from tools import web
    monkeypatch.setattr("common.hub_urls.internal_origins", lambda: ["http://localhost:8000"])
    ok, _ = web.validate_url("http://localhost:8000/apps/demo-1a2b/")
    assert ok
    ok, reason = web.validate_url("http://localhost:8000/api/agents")
    assert not ok and "non-public" in reason


def test_the_browser_service_policy_makes_the_same_exemption():
    spec = importlib.util.spec_from_file_location("pd_browser_policy", ROOT / "deploy" / "browser" / "policy.py")
    policy = importlib.util.module_from_spec(spec)
    sys.modules["pd_browser_policy"] = policy
    spec.loader.exec_module(policy)
    p = policy.Policy.from_dict({"internal_origins": ["http://host.docker.internal:8000"],
                                 "internal_paths": ["/apps/", "/preview/"],
                                 "deny_domains": ["blocked.example"]})
    assert policy.check_url("http://host.docker.internal:8000/apps/demo/", p) == (True, "")
    assert policy.check_url("http://host.docker.internal:8000/apps/demo/assets/a.js", p) == (True, "")
    ok, reason = policy.check_url("http://host.docker.internal:8000/api/agents", p)
    assert not ok
    # An allowlist does not have to list the hub, and the deny list still wins.
    strict = policy.Policy.from_dict({"internal_origins": ["http://hub:8000"], "internal_paths": ["/apps/"],
                                      "allowlist_enabled": True, "allow_domains": ["docs.python.org"]})
    assert policy.check_url("http://hub:8000/apps/x/", strict, resolve=False) == (True, "")
    assert not policy.check_url("http://hub:8000/apps/x/", policy.Policy.from_dict(
        {"internal_origins": ["http://hub:8000"], "internal_paths": ["/apps/"], "deny_domains": ["hub"]}), resolve=False)[0]
    assert p.to_dict()["internal_paths"] == ["/apps/", "/preview/"]


# ── the agent's tools ────────────────────────────────────────────────────────

def test_the_tools_deploy_report_and_stop(project, monkeypatch):
    from tools import project_deploy
    monkeypatch.setattr(project_deploy, "_resolve_project", lambda ref: project if ref in (project.id, project.name) else None)
    port = runner.free_port()
    out = json.loads(project_deploy.deploy_project.invoke({
        "project": "Demo App", "mode": "local", "wait_seconds": 30,
        "services": [_http_server_service(port).model_dump()]}))
    try:
        assert out["ok"], out
        assert out["status"] == "running" and out["services"][0]["healthy"] is True
        assert out["browser_url"].endswith(f"?key={store.for_project(project.id).share_token}")
        assert "/apps/" in out["browser_url"] and out["browser_url"].startswith("http")
        status = json.loads(project_deploy.project_deployment_status.invoke({"project": project.id}))
        assert status["ok"] and status["status"] == "running"
        logs = json.loads(project_deploy.project_deployment_logs.invoke({"project": project.id, "tail": 20}))
        assert logs["ok"] and logs["service"] == "web"
    finally:
        stopped = json.loads(project_deploy.stop_project_deployment.invoke({"project": project.id}))
    assert stopped["ok"] and stopped["status"] == "stopped"
    missing = json.loads(project_deploy.deploy_project.invoke({"project": "nothing"}))
    assert not missing["ok"] and missing["code"] == "not_found"


def test_the_tools_are_in_the_catalog_and_the_approval_list():
    from tools.approval import NEEDS_APPROVAL
    from tools.registry import get_tools_by_category
    ids = {t.id for t in get_tools_by_category("project_management")}
    assert {"deploy_project", "project_deployment_status", "project_deployment_logs",
            "stop_project_deployment"} <= ids
    assert {"deploy_project", "stop_project_deployment"} <= NEEDS_APPROVAL

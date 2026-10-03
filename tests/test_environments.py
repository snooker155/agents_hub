"""Environments: execution profiles for runs, resident instances and
scheduled jobs.

Covers the model's validation, the service rules (unique names per scope,
one default per scope, archive, delete refused while in use, resolution
precedence), what a launch gets (launch_fields for each network type), the
docker side (build_run_command with network none and custom limits, the
derived image tag, options reaching start_container), the hub tools' own
allowlist check, the egress proxy against a real local server, a resident
instance started in an environment, and the REST routes.
"""
from __future__ import annotations

import base64
import http.client
import http.server
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import config  # noqa: E402
from environments import egress, service  # noqa: E402
from environments.launch import launch_fields  # noqa: E402
from environments.models import Environment  # noqa: E402
from managers import container_manager as cm  # noqa: E402


@pytest.fixture
def dot_env(monkeypatch):
    """Control the live settings without touching the checkout's .env."""
    state = {}
    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    for key in ("AGENT_EXECUTION_MODE", "AGENTS_HUB_EGRESS_PROXY", "AGENTS_HUB_EGRESS_PROXY_PORT",
                "AGENTS_HUB_EGRESS_PROXY_PUBLIC_HOST", "AGENTS_HUB_NETWORK",
                "AGENTS_HUB_ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    return state


# ── model ────────────────────────────────────────────────────────────────────

def test_model_normalizes_hosts_and_defaults():
    env = Environment(name="  Sandbox ", network={
        "type": "limited",
        "allowed_hosts": ["https://API.Example.com/v1", "*.github.com", "example.org:443", "github.com"],
    })
    assert env.name == "Sandbox"
    assert env.mode == "inherit"
    assert env.network.allowed_hosts == ["api.example.com", "github.com", "example.org"]
    assert env.workspace is None and env.is_default is False


@pytest.mark.parametrize("field,value", [
    ("packages", ["--index-url=http://evil"]),
    ("packages", ["requests; rm -rf /"]),
    ("image", "-v /:/host"),
    ("env", {"AGENTS_HUB_API_TOKEN": "x"}),
    ("env", {"HTTP_PROXY": "x"}),
    ("env", {"1BAD": "x"}),
    ("name", "   "),
    ("mode", "kubernetes"),
    ("sandbox_provider", "aws-lambda"),
])
def test_model_refuses_unsafe_values(field, value):
    with pytest.raises(Exception):
        Environment(**{"name": "x", field: value})


def test_model_sandbox_provider_defaults_and_choices():
    assert Environment(name="x").sandbox_provider == "inherit"
    for choice in ("inherit", "docker", "local", "e2b", "modal"):
        assert Environment(name="x", sandbox_provider=choice).sandbox_provider == choice


def test_model_refuses_bad_limits_and_hosts():
    with pytest.raises(Exception):
        Environment(name="x", limits={"memory": "lots"})
    with pytest.raises(Exception):
        Environment(name="x", limits={"pids_limit": 3})
    with pytest.raises(Exception):
        Environment(name="x", network={"type": "limited", "allowed_hosts": ["user@host.com"]})


def test_model_sandbox_size_defaults_and_choices():
    assert Environment(name="x").size is None
    for choice in ("small", "medium", "large"):
        assert Environment(name="x", size=choice).size == choice
    # Case-insensitive and "" normalizes to unset, like the other optional fields.
    assert Environment(name="x", size="SMALL").size == "small"
    assert Environment(name="x", size="").size is None
    with pytest.raises(Exception):
        Environment(name="x", size="huge")


def test_docker_options_size_preset_and_overrides():
    from environments.launch import docker_options

    small = Environment(name="x", mode="docker", size="small")
    assert docker_options(small) == {"cpus": "1", "memory": "1g", "pids_limit": 128, "size": "small"}

    # An explicit limit wins over the matching preset value; the rest of the
    # preset still fills in.
    overridden = Environment(name="x", mode="docker", size="medium", limits={"cpus": "3"})
    opts = docker_options(overridden)
    assert opts["cpus"] == "3" and opts["memory"] == "4g" and opts["pids_limit"] == 256 and opts["size"] == "medium"

    # No size: behaves exactly as before this field existed.
    plain = Environment(name="x", mode="docker", limits={"memory": "512m"})
    assert docker_options(plain) == {"memory": "512m"}
    assert docker_options(Environment(name="x")) is None


def test_packages_accept_plain_requirements():
    env = Environment(name="x", packages=["requests>=2.31,<3", "pandas[excel]==2.2.1", "numpy"])
    assert env.packages == ["requests>=2.31,<3", "pandas[excel]==2.2.1", "numpy"]


def test_effective_hosts_add_package_registries():
    env = Environment(name="x", network={"type": "limited", "allowed_hosts": ["example.com"],
                                         "allow_package_managers": True})
    hosts = env.network.effective_hosts()
    assert hosts[0] == "example.com" and "pypi.org" in hosts and "registry.npmjs.org" in hosts


# ── service ──────────────────────────────────────────────────────────────────

def test_names_are_unique_per_scope():
    service.create_environment({"name": "Sandbox"})
    service.create_environment({"name": "sandbox", "workspace": "a"})  # another scope
    with pytest.raises(service.EnvironmentConflict):
        service.create_environment({"name": "SANDBOX"})
    with pytest.raises(service.EnvironmentConflict):
        service.create_environment({"name": "Sandbox", "workspace": "a"})


def test_invalid_payload_is_a_400():
    with pytest.raises(service.EnvironmentInvalid) as info:
        service.create_environment({"name": "x", "packages": ["-e ."]})
    assert info.value.status == 400 and "packages" in str(info.value)


def test_one_default_per_scope():
    a = service.create_environment({"name": "a", "workspace": "w", "is_default": True})
    b = service.create_environment({"name": "b", "workspace": "w", "is_default": True})
    g = service.create_environment({"name": "g", "is_default": True})
    assert service.get_environment(a.id).is_default is False
    assert service.get_environment(b.id).is_default is True
    assert service.get_environment(g.id).is_default is True
    service.set_default(a.id)
    assert service.get_environment(b.id).is_default is False


def test_resolve_for_precedence():
    glob = service.create_environment({"name": "global", "is_default": True})
    own = service.create_environment({"name": "own", "workspace": "w", "is_default": True})
    pick = service.create_environment({"name": "pick", "workspace": "w"})
    other = service.create_environment({"name": "other", "workspace": "elsewhere"})

    assert service.resolve_for("w", pick.id).id == pick.id        # explicit wins
    assert service.resolve_for("w").id == own.id                  # then the workspace default
    assert service.resolve_for("x").id == glob.id                 # then the global default
    with pytest.raises(service.EnvironmentConflict):
        service.resolve_for("w", other.id)                        # another workspace's
    with pytest.raises(service.EnvironmentNotFound):
        service.resolve_for("w", "nope")

    service.archive_environment(own.id)
    assert service.resolve_for("w").id == glob.id                 # archived default drops out
    with pytest.raises(service.EnvironmentConflict):
        service.resolve_for("w", own.id)
    assert service.resolve_for("w", own.id, allow_archived=True).id == own.id


def test_archive_is_read_only():
    env = service.create_environment({"name": "x", "is_default": True})
    archived = service.archive_environment(env.id)
    assert archived.archived_at and archived.is_default is False
    with pytest.raises(service.EnvironmentConflict):
        service.update_environment(env.id, {"description": "changed"})
    with pytest.raises(service.EnvironmentConflict):
        service.set_default(env.id)
    assert env.id not in [e.id for e in service.list_environments()]
    assert env.id in [e.id for e in service.list_environments(include_archived=True)]


def test_update_merges_nested_fields():
    env = service.create_environment({"name": "x", "network": {"type": "limited",
                                                              "allowed_hosts": ["a.com"]}})
    updated = service.update_environment(env.id, {"network": {"allow_package_managers": True}})
    assert updated.network.type == "limited"
    assert updated.network.allowed_hosts == ["a.com"]
    assert updated.network.allow_package_managers is True


def test_sandbox_provider_is_editable_and_round_trips():
    env = service.create_environment({"name": "x", "sandbox_provider": "e2b"})
    assert env.sandbox_provider == "e2b"
    updated = service.update_environment(env.id, {"sandbox_provider": "modal"})
    assert updated.sandbox_provider == "modal"
    assert service.to_dict(updated)["sandbox_provider"] == "modal"


def test_size_is_editable_and_round_trips():
    env = service.create_environment({"name": "x", "size": "small"})
    assert env.size == "small"
    updated = service.update_environment(env.id, {"size": "large"})
    assert updated.size == "large"
    assert service.to_dict(updated)["size"] == "large"
    cleared = service.update_environment(env.id, {"size": None})
    assert cleared.size is None


def test_delete_refused_while_in_use(monkeypatch):
    env = service.create_environment({"name": "x"})
    monkeypatch.setattr(service, "_instances_using",
                        lambda env_id: [{"instance_id": "i1", "state": "active", "environment_id": env_id}])
    with pytest.raises(service.EnvironmentConflict):
        service.delete_environment(env.id)
    monkeypatch.setattr(service, "_instances_using",
                        lambda env_id: [{"instance_id": "i1", "state": "stopped", "environment_id": env_id}])
    job = SimpleNamespace(id="j1", title="nightly", kind="agent_task", status="scheduled",
                          workspace=None, environment_id=env.id)
    monkeypatch.setattr(service, "_jobs_using", lambda env_id: [job])
    with pytest.raises(service.EnvironmentConflict):
        service.delete_environment(env.id)
    job.status = "cancelled"
    assert service.delete_environment(env.id) is True
    assert service.get_environment(env.id) is None


def test_usage_lists_runs_from_the_run_record():
    from common import db
    env = service.create_environment({"name": "x"})
    with db.transaction() as conn:
        conn.execute("INSERT INTO runs (run_id, agent_id, status, created_at, extra) VALUES (?, ?, ?, ?, ?)",
                     ("r1", "a", "finished", "2026-09-24T00:00:00+00:00",
                      db.dumps({"environment_id": env.id})))
        conn.execute("INSERT INTO runs (run_id, agent_id, status, created_at, extra) VALUES (?, ?, ?, ?, ?)",
                     ("r2", "a", "finished", "2026-09-24T00:00:00+00:00", db.dumps({})))
    runs = service.usage(env.id)["runs"]
    assert [r["run_id"] for r in runs] == ["r1"]


# ── launch fields ────────────────────────────────────────────────────────────

def _task(env_id=None):
    return SimpleNamespace(id="t1", environment_id=env_id)


def test_launch_fields_empty_without_environment(dot_env):
    assert launch_fields(_task(), "w") == {}


def test_launch_fields_unrestricted(dot_env):
    env = service.create_environment({"name": "plain", "env": {"FOO": "bar"}, "mode": "docker",
                                      "limits": {"memory": "1g"}})
    fields = launch_fields(_task(env.id), "w")
    assert fields["environment_id"] == env.id
    assert fields["execution_mode"] == "docker"
    assert fields["env"]["FOO"] == "bar"
    assert fields["env"]["AGENTS_HUB_ENVIRONMENT_NAME"] == "plain"
    assert "AGENTS_HUB_NETWORK" not in fields["env"]
    assert fields["docker"] == {"memory": "1g"}


def test_launch_fields_network_none(dot_env):
    env = service.create_environment({"name": "offline", "network": {"type": "none"}})
    fields = launch_fields(_task(env.id), "w")
    assert fields["env"]["AGENTS_HUB_NETWORK"] == "none"
    assert "AGENTS_HUB_ALLOWED_HOSTS" not in fields["env"]
    assert "HTTP_PROXY" not in fields["env"]  # the proxy is off here
    assert fields["docker"] is None  # never docker's --network none
    assert fields["execution_mode"] is None  # inherit: the workspace decides


def test_launch_fields_network_none_with_proxy(dot_env):
    """No internet, but the model is still reachable: an empty run allowlist
    behind the proxy, which passes only the infrastructure hosts."""
    dot_env["AGENTS_HUB_EGRESS_PROXY"] = "1"
    env = service.create_environment({"name": "offline", "network": {"type": "none"}})
    fields = launch_fields(_task(env.id), "w")
    proxy = fields["env"]["HTTPS_PROXY"]
    token = proxy[len("http://"):].split("@", 1)[0]
    entry = egress.lookup(token)
    assert entry["hosts"] == []
    assert egress.host_allowed("api.openai.com", entry["hosts"] + entry["infra"])
    assert not egress.host_allowed("example.com", entry["hosts"] + entry["infra"])
    assert fields["env"]["AGENTS_HUB_NETWORK"] == "none"


def test_launch_fields_limited_without_proxy(dot_env):
    env = service.create_environment({"name": "fenced", "network": {
        "type": "limited", "allowed_hosts": ["example.com"], "allow_package_managers": True}})
    fields = launch_fields(_task(env.id), "w")
    assert fields["env"]["AGENTS_HUB_NETWORK"] == "limited"
    assert fields["env"]["AGENTS_HUB_ALLOWED_HOSTS"].split(",")[:2] == ["example.com", "pypi.org"]
    assert "HTTP_PROXY" not in fields["env"]
    assert fields["docker"] is None


def test_launch_fields_limited_with_proxy(dot_env):
    dot_env["AGENTS_HUB_EGRESS_PROXY"] = "1"
    dot_env["AGENTS_HUB_EGRESS_PROXY_PORT"] = "9123"
    env = service.create_environment({"name": "fenced", "mode": "docker", "network": {
        "type": "limited", "allowed_hosts": ["example.com"]}})
    fields = launch_fields(_task(env.id), "w")
    proxy = fields["env"]["HTTPS_PROXY"]
    assert proxy.startswith("http://") and proxy.endswith("@host.docker.internal:9123")
    assert fields["env"]["http_proxy"] == proxy
    assert fields["env"]["NO_PROXY"] == "localhost,127.0.0.1,host.docker.internal"
    token = proxy[len("http://"):].split("@", 1)[0]
    entry = egress.lookup(token)
    assert entry["hosts"] == ["example.com"] and entry["environment_id"] == env.id


def test_launch_fields_falls_back_to_the_default(dot_env):
    default = service.create_environment({"name": "d", "workspace": "w", "is_default": True})
    assert launch_fields(_task(), "w")["environment_id"] == default.id
    assert launch_fields(_task("missing"), "w")["environment_id"] == default.id


def test_archived_task_environment_keeps_its_fence(dot_env):
    env = service.create_environment({"name": "offline", "network": {"type": "none"}})
    service.archive_environment(env.id)
    assert launch_fields(_task(env.id), "w")["env"]["AGENTS_HUB_NETWORK"] == "none"


# ── docker ───────────────────────────────────────────────────────────────────

@pytest.fixture
def no_host_translation(monkeypatch):
    monkeypatch.delenv("HOST_PROJECT_ROOT", raising=False)


def _run_cmd(**overrides):
    kwargs = dict(
        container_name="agents-hub-run-abc123", agent_id="swe_agent",
        image="agents-hub/base:latest", network="agents-hub",
        translated_cmd=["python", "-m", "runtime.agent_run", "swe_agent"],
        state_dir="/host/.agents_hub", tasks_dir="/host/tasks", env={},
    )
    kwargs.update(overrides)
    return cm.build_run_command(**kwargs)


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def test_build_run_command_network_none_and_limits(no_host_translation):
    argv = _run_cmd(network="none", memory="512m", cpus="0.5", pids_limit=64, image="python:3.12-slim")
    assert _flag(argv, "--network") == "none"
    assert "host.docker.internal:host-gateway" not in argv
    assert _flag(argv, "--memory") == "512m"
    assert _flag(argv, "--cpus") == "0.5"
    assert _flag(argv, "--pids-limit") == "64"
    assert argv[argv.index("python:3.12-slim") + 1] == "python"


def test_build_run_command_default_network_keeps_host_mapping(no_host_translation):
    argv = _run_cmd()
    assert _flag(argv, "--network") == "agents-hub"
    assert "host.docker.internal:host-gateway" in argv


def test_environment_image_tag_is_stable():
    a = cm.environment_image_tag("agents-hub/base:latest", ["requests", "numpy"])
    b = cm.environment_image_tag("agents-hub/base:latest", ["numpy", "requests", "numpy"])
    c = cm.environment_image_tag("agents-hub/base:latest", ["numpy"])
    d = cm.environment_image_tag("python:3.12", ["numpy", "requests"])
    assert a == b and a != c and a != d
    assert a.startswith("agents-hub-env:") and len(a.split(":", 1)[1]) == 12


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_ensure_environment_image_builds_once(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "DOCKERFILE_DIR", tmp_path)
    built = set()
    calls = []

    def fake_run(cmd, timeout=300, capture=True):
        calls.append(cmd)
        if cmd[:3] == ["docker", "images", "-q"]:
            return _Done(stdout="abc\n" if cmd[3] in built else "")
        if cmd[:2] == ["docker", "build"]:
            built.add(cmd[3])
            return _Done()
        raise AssertionError(cmd)

    monkeypatch.setattr(cm, "_run", fake_run)
    tag = cm.ensure_environment_image("agents-hub/base:latest", ["numpy", "requests"])
    assert tag == cm.environment_image_tag("agents-hub/base:latest", ["numpy", "requests"])
    dockerfile = next(tmp_path.glob("env-*.Dockerfile")).read_text()
    assert "FROM agents-hub/base:latest" in dockerfile
    assert "RUN pip install --no-cache-dir numpy requests" in dockerfile
    builds = len([c for c in calls if c[:2] == ["docker", "build"]])
    assert cm.ensure_environment_image("agents-hub/base:latest", ["requests", "numpy"]) == tag
    assert len([c for c in calls if c[:2] == ["docker", "build"]]) == builds == 1


def test_ensure_environment_image_falls_back_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "DOCKERFILE_DIR", tmp_path)
    monkeypatch.setattr(cm, "_run", lambda cmd, timeout=300, capture=True:
                        _Done(returncode=1, stderr="no such image") if cmd[1] == "build" else _Done())
    assert cm.ensure_environment_image("base:1", ["numpy"]) == "base:1"
    assert cm.ensure_environment_image("base:1", []) == "base:1"


def test_start_run_container_passes_options(monkeypatch):
    from runtime import docker_runner
    seen = {}
    monkeypatch.setattr(docker_runner, "start_container", lambda **kw: seen.update(kw) or {"success": True})
    monkeypatch.setattr("common.snapshot.write_snapshots", lambda key: "/tmp/snap")
    monkeypatch.setattr(cm, "ensure_environment_image", lambda base, pkgs: f"derived-of-{base}")
    docker_runner.start_run_container("r1", "a", ["python"], options={
        "memory": "1g", "cpus": "2", "pids_limit": 128,
        "image": "python:3.12", "packages": ["numpy"]})
    assert "network" not in seen and seen["memory"] == "1g" and seen["cpus"] == "2"
    assert seen["pids_limit"] == 128 and seen["image"] == "derived-of-python:3.12"
    assert seen["hardened"] is True

    seen.clear()
    docker_runner.start_run_container("r2", "a", ["python"])
    assert "network" not in seen and "image" not in seen


def test_node_container_honours_options(monkeypatch, no_host_translation):
    captured = {}
    monkeypatch.setattr(cm, "get_or_create_network", lambda: "agents-hub")
    monkeypatch.setattr(cm, "image_tag_for_agent", lambda agent_id: "agents-hub/base:latest")

    def fake_run(cmd, timeout=300, capture=True):
        captured["cmd"] = cmd
        return _Done(stdout="cid\n")

    monkeypatch.setattr(cm, "_run", fake_run)
    result = cm.start_node_container("n1", "a", ["python", "-m", "runtime.instance_run"], env={},
                                     options={"network": "none", "memory": "256m", "image": "img:1"},
                                     extra_env={"AGENTS_HUB_NETWORK": "none", "FOO": "bar"})
    argv = captured["cmd"]
    assert result["success"] and result["image"] == "img:1"
    # A stray "network" key is ignored: the container stays on the bridge.
    assert _flag(argv, "--network") == "agents-hub" and _flag(argv, "--memory") == "256m"
    assert "AGENTS_HUB_NETWORK=none" in argv and "FOO=bar" in argv
    assert "host.docker.internal:host-gateway" in argv


# ── the hub tools' own check ─────────────────────────────────────────────────

def test_web_policy_honours_the_environment(monkeypatch):
    from tools import web
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "limited")
    monkeypatch.setenv("AGENTS_HUB_ALLOWED_HOSTS", "example.com,pypi.org")
    assert web.check_domain_policy("https://docs.example.com/x")[0] is True
    ok, reason = web.check_domain_policy("https://evil.test/")
    assert ok is False and "allowlist" in reason
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "none")
    ok, reason = web.check_domain_policy("https://example.com/")
    assert ok is False and "no network" in reason


def test_browser_policy_honours_the_environment(monkeypatch):
    from tools import browser
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "limited")
    monkeypatch.setenv("AGENTS_HUB_ALLOWED_HOSTS", "example.com")
    policy = browser.session_policy()
    assert policy["allowlist_enabled"] is True and policy["allow_domains"] == ["example.com"]
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "none")
    policy = browser.session_policy()
    assert policy["allowlist_enabled"] is True and policy["allow_domains"] == []


# ── the egress proxy ─────────────────────────────────────────────────────────

class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's naming
        body = f"hello {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def origin():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hello)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy():
    egress.stop_background()
    running = egress.start_background(host="127.0.0.1", port_=0, force=True)
    yield running
    egress.stop_background()


def _auth(token):
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _get_through(proxy_port, url, token):
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=10)
    headers = {"Proxy-Authorization": _auth(token)} if token else {}
    conn.request("GET", url, headers=headers)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def test_proxy_forwards_allowed_and_refuses_the_rest(proxy, origin):
    token = egress.register(["127.0.0.1"], environment_id="e1")
    status, body = _get_through(proxy.port, f"http://127.0.0.1:{origin}/ping?x=1", token)
    assert status == 200 and body == "hello /ping?x=1"

    status, body = _get_through(proxy.port, f"http://localhost:{origin}/ping", token)
    assert status == 403 and "allowlist" in body

    status, _ = _get_through(proxy.port, f"http://127.0.0.1:{origin}/ping", None)
    assert status == 407
    status, _ = _get_through(proxy.port, f"http://127.0.0.1:{origin}/ping", "forged")
    assert status == 407
    assert proxy.stats["allowed"] >= 1 and proxy.stats["denied"] >= 1


def test_proxy_tunnels_connect(proxy, origin):
    token = egress.register(["127.0.0.1"])
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as sock:
        sock.sendall((f"CONNECT 127.0.0.1:{origin} HTTP/1.1\r\nHost: 127.0.0.1:{origin}\r\n"
                      f"Proxy-Authorization: {_auth(token)}\r\n\r\n").encode())
        head = sock.recv(4096)
        assert head.startswith(b"HTTP/1.1 200")
        sock.sendall("GET /inside HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    assert b"hello /inside" in data

    with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as sock:
        sock.sendall((f"CONNECT example.com:443 HTTP/1.1\r\n"
                      f"Proxy-Authorization: {_auth(token)}\r\n\r\n").encode())
        assert sock.recv(4096).startswith(b"HTTP/1.1 403")


def test_host_matching_and_expiry():
    assert egress.host_allowed("api.example.com", ["example.com"])
    assert egress.host_allowed("EXAMPLE.com.", ["example.com"])
    assert not egress.host_allowed("badexample.com", ["example.com"])
    assert not egress.host_allowed("example.com", [])
    token = egress.register(["a.com"], ttl=60)
    assert egress.lookup(token) is not None
    egress._TOKENS[token]["expires_at"] = 0
    assert egress.lookup(token) is None
    egress.revoke(token)


def test_tokens_survive_a_fresh_process_through_the_docstore():
    token = egress.register(["a.com"])
    egress._TOKENS.clear()
    assert egress.lookup(token)["hosts"] == ["a.com"]


# ── a resident instance in an environment ────────────────────────────────────

@pytest.fixture
def launched(monkeypatch):
    from agents import registry as agent_registry
    from instances import carrier
    calls = {"container": [], "subprocess": []}

    class _Spec:
        name = "Code Reviewer"
        node_type = "worker"
        http_expose = False

    monkeypatch.setattr(agent_registry, "get_agent", lambda agent_id: _Spec())

    def _fake_start_node_container(instance_id, agent_id, inner_cmd, workspace=None, env=None, **kwargs):
        calls["container"].append({"env": env, **kwargs})
        return {"success": True, "container_id": "deadbeef",
                "container_name": f"agents-hub-node-{instance_id[:12]}",
                "image": "agents-hub/base:latest", "error": None}

    class _FakeProc:
        pid = 4242

    def _fake_popen(*args, **kwargs):
        calls["subprocess"].append(kwargs)
        return _FakeProc()

    monkeypatch.setattr(cm, "start_node_container", _fake_start_node_container)
    monkeypatch.setattr(carrier.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(cm, "container_running", lambda name: False)
    return calls


def test_instance_starts_in_its_environment(dot_env, launched):
    from instances import carrier
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    env = service.create_environment({"name": "box", "mode": "docker", "env": {"FOO": "bar"},
                                      "network": {"type": "none"}, "limits": {"cpus": "1"}})
    instance = carrier.start("code_reviewer", environment_id=env.id)
    assert instance["carrier_mode"] == "docker"
    assert instance["environment_id"] == env.id and instance["environment_name"] == "box"
    call = launched["container"][0]
    assert call["options"] == {"cpus": "1"}
    assert call["extra_env"]["FOO"] == "bar" and call["extra_env"]["AGENTS_HUB_NETWORK"] == "none"


def test_instance_local_environment_reaches_the_subprocess(dot_env, launched):
    from instances import carrier
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    env = service.create_environment({"name": "fenced", "network": {
        "type": "limited", "allowed_hosts": ["example.com"]}})
    carrier.start("code_reviewer", environment_id=env.id)
    child_env = launched["subprocess"][0]["env"]
    assert child_env["AGENTS_HUB_ALLOWED_HOSTS"] == "example.com"
    assert child_env["AGENTS_HUB_ENVIRONMENT_ID"] == env.id


def test_instance_without_environment_is_unchanged(dot_env, launched):
    from instances import carrier
    dot_env["AGENT_EXECUTION_MODE"] = "docker"
    instance = carrier.start("code_reviewer")
    assert "options" not in launched["container"][0] and "extra_env" not in launched["container"][0]
    assert instance.get("environment_id") is None


def test_instance_refuses_an_archived_environment(dot_env, launched):
    from instances import carrier
    env = service.create_environment({"name": "old"})
    service.archive_environment(env.id)
    with pytest.raises(ValueError, match="archived"):
        carrier.start("code_reviewer", environment_id=env.id)


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import environments as env_routes

    app = FastAPI()
    app.include_router(env_routes.router)
    return TestClient(app)


def test_routes_crud(client):
    r = client.post("/api/environments", json={
        "name": "Sandbox", "workspace": "w", "mode": "docker", "packages": ["numpy"],
        "network": {"type": "limited", "allowed_hosts": ["example.com"]},
        "limits": {"memory": "1g"}, "env": {"FOO": "1"}})
    assert r.status_code == 200, r.text
    env = r.json()
    assert env["usage_counts"] == {"instances": 0, "jobs": 0}
    assert env["network"] == {"type": "limited", "allowed_hosts": ["example.com"],
                              "allow_package_managers": False}
    assert env["limits"] == {"memory": "1g", "cpus": None, "pids_limit": None}
    assert env["sandbox_provider"] == "inherit"

    r = client.patch(f"/api/environments/{env['id']}", json={"sandbox_provider": "local"})
    assert r.status_code == 200 and r.json()["sandbox_provider"] == "local"

    assert client.post("/api/environments", json={"name": "sandbox", "workspace": "w"}).status_code == 409
    assert client.post("/api/environments", json={"name": "x", "mode": "vm"}).status_code == 400
    client.post("/api/environments", json={"name": "Global"})
    client.post("/api/environments", json={"name": "Other", "workspace": "other"})

    names = [e["name"] for e in client.get("/api/environments", params={"workspace": "w"}).json()]
    assert sorted(names) == ["Global", "Sandbox"]

    r = client.patch(f"/api/environments/{env['id']}", json={"description": "d", "limits": {"cpus": "2"}})
    assert r.status_code == 200 and r.json()["limits"] == {"memory": "1g", "cpus": "2", "pids_limit": None}

    r = client.post(f"/api/environments/{env['id']}/default")
    assert r.json()["is_default"] is True
    resolved = client.get("/api/environments/resolve", params={"workspace": "w"}).json()
    assert resolved["id"] == env["id"]

    usage = client.get(f"/api/environments/{env['id']}/usage").json()
    assert usage == {"instances": [], "jobs": [], "runs": []}

    r = client.post(f"/api/environments/{env['id']}/archive")
    assert r.json()["archived_at"]
    assert client.patch(f"/api/environments/{env['id']}", json={"name": "y"}).status_code == 409
    assert client.get("/api/environments/resolve", params={"workspace": "w"}).json() is None
    ids = [e["id"] for e in client.get("/api/environments", params={
        "workspace": "w", "include_archived": True}).json()]
    assert env["id"] in ids

    assert client.delete(f"/api/environments/{env['id']}").json() == {"deleted": True}
    assert client.get(f"/api/environments/{env['id']}").status_code == 404


def test_sandbox_providers_route(client, monkeypatch):
    from sandbox import docker as docker_mod
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    r = client.get("/api/environments/sandbox/providers")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"docker", "local", "e2b", "modal"}
    assert body["docker"]["available"] is True
    assert body["e2b"]["available"] is False and body["e2b"]["reason"]


def test_routes_audit_writes(client):
    from common import db
    r = client.post("/api/environments", json={"name": "Audited"})
    env_id = r.json()["id"]
    client.delete(f"/api/environments/{env_id}")
    actions = [row["action"] for row in db.get_conn().execute(
        "SELECT action FROM audit_log WHERE object_id = ? ORDER BY id", (env_id,)).fetchall()]
    assert actions == ["environment.create", "environment.delete"]


def test_build_route(client, monkeypatch):
    local = client.post("/api/environments", json={"name": "L", "mode": "local"}).json()
    assert client.post(f"/api/environments/{local['id']}/build").status_code == 400
    env = client.post("/api/environments", json={"name": "D", "mode": "docker", "image": "python:3.12",
                                                  "packages": ["numpy"]}).json()
    monkeypatch.setattr(cm, "build_environment_image",
                        lambda base, pkgs: {"ok": True, "image": f"built:{base}", "error": None})
    assert client.post(f"/api/environments/{env['id']}/build").json() == {
        "ok": True, "image": "built:python:3.12", "error": None}


def test_instances_route_accepts_environment(launched, dot_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import instances as instance_routes

    monkeypatch.setattr("agents.registry.get_agent",
                        lambda agent_id: SimpleNamespace(name="Reviewer", domain=""))
    app = FastAPI()
    app.include_router(instance_routes.router)
    instances_client = TestClient(app)
    env = service.create_environment({"name": "box"})
    r = instances_client.post("/api/instances", json={"agent_id": "a", "environment_id": env.id})
    assert r.status_code == 201, r.text
    assert r.json()["environment_id"] == env.id and r.json()["environment_name"] == "box"
    service.archive_environment(env.id)
    r = instances_client.post("/api/instances", json={"agent_id": "a", "environment_id": env.id})
    assert r.status_code == 400


# ── through the launcher ─────────────────────────────────────────────────────

def test_a_task_run_launches_in_its_environment(dot_env, monkeypatch):
    """prepare_run -> _launch_extras -> launch_fields -> launch_prepared ->
    docker_runner.start_run_container(options=...), with the environment's
    mode replacing the global local mode."""
    from agents import agent_launcher
    from managers import run_manager as rm
    from runtime import docker_runner
    from tasks import service as ts

    monkeypatch.setattr("agents.registry.get_agent", lambda agent_id: object())
    monkeypatch.setattr(agent_launcher.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("the environment pins docker"))
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    calls = []

    def _fake(run_id, agent_id, inner_cmd, cwd=None, env=None, options=None):
        calls.append({"env": env, "options": options})
        return {"success": True, "container_name": "agents-hub-run-x", "image": "i", "error": None}

    monkeypatch.setattr(docker_runner, "start_run_container", _fake)
    env = service.create_environment({"name": "box", "mode": "docker", "env": {"FOO": "bar"},
                                      "network": {"type": "none"}, "limits": {"pids_limit": 100}})
    task = ts.create_task("in a box", environment_id=env.id)
    run_id, _ = agent_launcher.start_run(str(task.id), "swe_agent", None)

    assert calls[0]["options"] == {"pids_limit": 100}
    assert calls[0]["env"]["FOO"] == "bar" and calls[0]["env"]["AGENTS_HUB_NETWORK"] == "none"
    rec = rm.get_run_by_id(run_id)
    assert rec["environment_id"] == env.id and rec["execution_mode"] == "docker"

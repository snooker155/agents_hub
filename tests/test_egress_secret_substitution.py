"""Secrets bound to hosts (common/secrets.py, environments/secret_egress.py,
environments/egress.py): the run holds a placeholder, the egress proxy puts the
real value into requests to the secret's hosts only, terminating TLS for those
hosts with a hub CA, and refuses a placeholder on its way anywhere else.

No network: every server here listens on 127.0.0.1.
"""
from __future__ import annotations

import base64
import http.client
import http.server
import json
import os
import ssl
import stat
import threading

import pytest

from environments import egress, secret_egress


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    from common.config import settings
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    monkeypatch.setattr(settings, "capability_guard", "block")
    monkeypatch.setattr(settings, "secret_key", "correct horse battery staple", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)
    monkeypatch.delenv("AGENTS_HUB_EGRESS_PROXY", raising=False)
    monkeypatch.delenv("AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK", raising=False)


@pytest.fixture
def proxy_on(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_EGRESS_PROXY", "1")


def _agent(agent_id: str, secrets=()):
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id=agent_id, name=agent_id, type="local", entrypoint="m:f",
                        secrets=list(secrets)), user_edit=False)


# ── hosts on a secret ───────────────────────────────────────────────────────

def test_normalize_hosts():
    from common.secrets import SecretsError, normalize_hosts
    assert normalize_hosts("https://API.GitHub.com/x, github.com:443  *.slack.com") == [
        "api.github.com", "github.com", "slack.com"]
    assert normalize_hosts(["a.com", "a.com", ""]) == ["a.com"]
    with pytest.raises(SecretsError):
        normalize_hosts(["bad host!"])


def test_hosts_are_stored_listed_kept_on_replace_and_editable():
    from common import secrets
    row = secrets.set_secret("ws", "GH", "ghp_value_long", allowed_hosts=["github.com"])
    assert row["allowed_hosts"] == ["github.com"]
    # Replacing the value without naming hosts keeps them.
    assert secrets.set_secret("ws", "GH", "ghp_value_newer")["allowed_hosts"] == ["github.com"]
    assert secrets.set_secret_hosts("ws", "GH", [])["allowed_hosts"] == []
    assert secrets.list_secrets("ws")[0]["allowed_hosts"] == []
    assert secrets.resolve_bound("ws", None, None, ["GH"]) == {"GH": ("ghp_value_newer", [])}


# ── hand-out ────────────────────────────────────────────────────────────────

def test_a_bound_secret_refuses_the_launch_when_the_proxy_is_off():
    from common import secrets
    _agent("bound_agent", secrets=["GH", "PLAIN"])
    secrets.set_secret("ws", "GH", "ghp_real_value_1", allowed_hosts=["github.com"])
    secrets.set_secret("ws", "PLAIN", "plain-value-123")
    with pytest.raises(secrets.SecretEgressUnavailable) as excinfo:
        secrets.env_for_run("ws", "bound_agent")
    assert "AGENTS_HUB_EGRESS_PROXY" in str(excinfo.value)
    assert "ghp_real_value_1" not in str(excinfo.value)


def test_the_explicit_fallback_hands_it_over_in_plain_text(monkeypatch):
    from common import secrets
    _agent("fallback_agent", secrets=["GH"])
    secrets.set_secret("ws", "GH", "ghp_real_value_2", allowed_hosts=["github.com"])
    monkeypatch.setenv("AGENTS_HUB_SECRET_PLAINTEXT_FALLBACK", "1")
    assert secrets.env_for_run("ws", "fallback_agent") == {"GH": "ghp_real_value_2"}


def test_with_the_proxy_the_run_gets_a_placeholder_and_is_routed(proxy_on):
    from common import secrets
    from common.subprocess_env import route_secrets
    _agent("routed_agent", secrets=["GH", "PLAIN"])
    secrets.set_secret("ws", "GH", "ghp_real_value_3", allowed_hosts=["github.com"])
    secrets.set_secret("ws", "PLAIN", "plain-value-456")
    env = secrets.env_for_run("ws", "routed_agent")
    assert env["PLAIN"] == "plain-value-456"
    placeholder = env["GH"]
    assert secret_egress.PLACEHOLDER_RE.fullmatch(placeholder)
    assert "ghp_real_value_3" not in json.dumps(env)
    assert env[secret_egress.PLACEHOLDERS_ENV] == placeholder
    assert secret_egress.unseal(placeholder)["value"] == "ghp_real_value_3"

    routed = route_secrets(dict(env, AGENT_WORKSPACE="ws"), execution_mode="local")
    token = routed["HTTPS_PROXY"].split("//", 1)[1].split("@", 1)[0]
    entry = egress.lookup(token)
    assert entry["open"] is True
    assert entry["secret_hosts"] == ["github.com"]
    assert entry["placeholders"] == [placeholder]
    for key in secret_egress.CA_BUNDLE_VARS:
        assert routed[key].endswith("ca-bundle.pem")
    assert routed["NODE_EXTRA_CA_CERTS"].endswith("hub-ca.pem")


def test_an_environment_token_gains_the_secret_hosts(proxy_on):
    env_token = egress.register(["pypi.org"], environment_id="env1")
    placeholder = secret_egress.seal("GH", "ghp_real_value_4", ["github.com"])
    env = {secret_egress.PLACEHOLDERS_ENV: placeholder,
           "HTTPS_PROXY": egress.proxy_url(env_token, "local")}
    secret_egress.route_env(env, execution_mode="docker")
    assert env["HTTPS_PROXY"] == egress.proxy_url(env_token, "docker")
    entry = egress.lookup(env_token)
    assert entry["hosts"] == ["pypi.org"] and not entry.get("open")
    assert entry["secret_hosts"] == ["github.com"] and entry["placeholders"] == [placeholder]


def test_a_run_without_placeholders_is_untouched():
    env = {"PATH": "/bin"}
    assert secret_egress.route_env(dict(env)) == env


def test_container_env_points_the_bundle_at_the_mounted_state_dir():
    from common.paths import AGENTS_HUB_ROOT
    from managers.container_manager import CONTAINER_STATE_DIR, container_env
    out = container_env({"SSL_CERT_FILE": str(AGENTS_HUB_ROOT / "egress" / "ca-bundle.pem"),
                         "REQUESTS_CA_BUNDLE": "/etc/ssl/other.pem"})
    assert out["SSL_CERT_FILE"] == f"{CONTAINER_STATE_DIR}/egress/ca-bundle.pem"
    assert out["REQUESTS_CA_BUNDLE"] == "/etc/ssl/other.pem"


def test_container_env_drops_the_secret_key_from_a_run_holding_placeholders():
    from managers.container_manager import container_env
    base = {"AGENTS_HUB_SECRET_KEY": "k", "GH": "ahsec_x"}
    assert container_env(base)["AGENTS_HUB_SECRET_KEY"] == "k"
    out = container_env({**base, "AGENTS_HUB_SECRET_PLACEHOLDERS": "ahsec_x"})
    assert "AGENTS_HUB_SECRET_KEY" not in out
    assert out["GH"] == "ahsec_x"


def test_the_ca_key_is_encrypted_with_the_hub_secret_key(tmp_path, monkeypatch):
    from common.config import settings
    from cryptography.hazmat.primitives import serialization
    key_path, _ = secret_egress.ensure_ca(tmp_path)
    data = key_path.read_bytes()
    assert b"ENCRYPTED" in data
    with pytest.raises(TypeError):
        serialization.load_pem_private_key(data, password=None)
    cert_pem, _ = secret_egress.mint_leaf("api.example.com", tmp_path)
    assert b"BEGIN CERTIFICATE" in cert_pem
    # A key written with no secret key configured stays readable.
    monkeypatch.setattr(settings, "secret_key", "", raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    secret_egress.ensure_ca(plain)
    assert b"ENCRYPTED" not in (plain / "ca.key").read_bytes()
    monkeypatch.setattr(settings, "secret_key", "correct horse battery staple", raising=False)
    assert b"BEGIN CERTIFICATE" in secret_egress.mint_leaf("b.example.com", plain)[0]


def test_in_process_get_honours_the_hosts():
    from common import secrets
    _agent("inproc_agent", secrets=["GH"])
    secrets.set_secret("ws", "GH", "ghp_real_value_5", allowed_hosts=["github.com"])
    with secrets.activate("ws", "inproc_agent"):
        assert secrets.get("GH") is None
        assert secrets.get("GH", host="evil.example") is None
        assert secrets.get("GH", host="api.github.com") == "ghp_real_value_5"


# ── substitution ────────────────────────────────────────────────────────────

def test_substitution_in_headers_basic_auth_and_query():
    ph = secret_egress.seal("GH", "ghp_real_value_6", ["github.com"])
    basic = base64.b64encode(f"x-access-token:{ph}".encode()).decode()
    target, headers, used = secret_egress.substitute_head(
        f"/repos?token={ph}",
        [("Authorization", f"Basic {basic}"), ("X-Api-Key", f"key {ph}"), ("Accept", "*/*")],
        "api.github.com", [ph])
    assert target == "/repos?token=ghp_real_value_6"
    assert base64.b64decode(headers[0][1].split(" ", 1)[1]).decode() == "x-access-token:ghp_real_value_6"
    assert headers[1] == ("X-Api-Key", "key ghp_real_value_6")
    assert headers[2] == ("Accept", "*/*")
    assert used == ["GH", "GH", "GH"]
    assert secret_egress.head_has_placeholder("/", [("Authorization", f"Basic {basic}")])


def test_a_placeholder_to_a_foreign_host_or_not_held_is_refused():
    ph = secret_egress.seal("GH", "ghp_real_value_7", ["github.com"])
    with pytest.raises(secret_egress.Refused) as excinfo:
        secret_egress.substitute_head("/", [("Authorization", f"Bearer {ph}")], "evil.example", [ph])
    assert excinfo.value.names == ["GH"]
    with pytest.raises(secret_egress.Refused):
        secret_egress.substitute_head("/", [("Authorization", f"Bearer {ph}")], "github.com", [])
    with pytest.raises(secret_egress.Refused):
        secret_egress.substitute_head("/", [("X", "ahsec_" + "A" * 32)], "github.com", None)


# ── the hub CA ──────────────────────────────────────────────────────────────

def test_ca_key_is_private_and_leaves_chain_to_it(tmp_path):
    from cryptography import x509
    key_path, cert_path = secret_egress.ensure_ca(tmp_path / "ca")
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    assert secret_egress.ensure_ca(tmp_path / "ca") == (key_path, cert_path)  # reused
    ca = x509.load_pem_x509_certificate(cert_path.read_bytes())
    for host, kind in (("api.github.com", x509.DNSName), ("127.0.0.1", x509.IPAddress)):
        cert_pem, _key_pem = secret_egress.mint_leaf(host, tmp_path / "ca")
        leaf = x509.load_pem_x509_certificate(cert_pem)
        leaf.verify_directly_issued_by(ca)
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert [str(v) for v in san.get_values_for_type(kind)] == [host]
    assert isinstance(secret_egress.leaf_context("api.github.com", tmp_path / "ca"), ssl.SSLContext)


def test_the_bundle_holds_the_hub_ca(monkeypatch, tmp_path):
    monkeypatch.setenv(secret_egress.CA_DIR_ENV, str(tmp_path / "ca"))
    bundle = secret_egress.ca_bundle_path()
    ca_pem = (tmp_path / "ca" / "ca.pem").read_bytes()
    assert ca_pem in bundle.read_bytes()
    assert secret_egress.hub_ca_cert_path().read_bytes() == ca_pem


# ── end to end through the proxy ────────────────────────────────────────────

class _Echo(http.server.BaseHTTPRequestHandler):
    """Answers with the request's path and the headers that may carry a secret."""

    def do_GET(self):  # noqa: N802 - http.server's naming
        body = json.dumps({"path": self.path,
                           "authorization": self.headers.get("Authorization", ""),
                           "x_key": self.headers.get("X-Key", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream(tmp_path):
    """An HTTPS server on 127.0.0.1 with a certificate from its own test CA,
    and the client context that trusts that CA (handed to the proxy)."""
    up_ca = tmp_path / "upstream-ca"
    secret_egress.ensure_ca(up_ca)
    cert_pem, key_pem = secret_egress.mint_leaf("127.0.0.1", up_ca)
    pair = tmp_path / "upstream.pem"
    pair.write_bytes(cert_pem + key_pem)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(pair))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    trust = ssl.create_default_context(cafile=str(up_ca / "ca.pem"))
    yield server.server_address[1], trust
    server.shutdown()
    server.server_close()


@pytest.fixture
def plain_origin():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def mitm_proxy(upstream, tmp_path):
    _port, trust = upstream
    # The run's trust bundle names this CA (route_env makes it in production).
    secret_egress.ensure_ca(tmp_path / "hub-ca")
    bg = egress._Background(egress.EgressProxy("127.0.0.1", 0, upstream_context=trust,
                                               ca_directory=tmp_path / "hub-ca"))
    bg.thread.start()
    bg.ready.wait(timeout=10)
    assert bg.error is None
    yield bg.proxy, tmp_path / "hub-ca"
    bg.stop()


def _auth(token):
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _https_through(proxy_port, ca_dir, upstream_port, token, headers):
    ctx = ssl.create_default_context(cafile=str(ca_dir / "ca.pem"))
    conn = http.client.HTTPSConnection("127.0.0.1", proxy_port, context=ctx, timeout=10)
    conn.set_tunnel("127.0.0.1", upstream_port, headers={"Proxy-Authorization": _auth(token)})
    try:
        conn.request("GET", "/whoami?q=1", headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def test_end_to_end_the_value_reaches_only_the_bound_host(mitm_proxy, upstream):
    proxy, ca_dir = mitm_proxy
    up_port, _trust = upstream
    ph = secret_egress.seal("API_KEY", "real-value-xyz-123", ["127.0.0.1"])
    token = egress.register([], open_network=True, secret_hosts=["127.0.0.1"], placeholders=[ph])

    status, body = _https_through(proxy.port, ca_dir, up_port, token,
                                  {"Authorization": f"Bearer {ph}", "X-Key": "plain"})
    assert status == 200
    seen = json.loads(body)
    # The upstream saw the real value; the client never held it.
    assert seen["authorization"] == "Bearer real-value-xyz-123"
    assert seen["x_key"] == "plain" and seen["path"] == "/whoami?q=1"
    assert proxy.stats["substituted"] == 1

    # A placeholder this run does not hold is refused, even to the bound host.
    other = secret_egress.seal("OTHER", "other-value-999", ["127.0.0.1"])
    status, body = _https_through(proxy.port, ca_dir, up_port, token,
                                  {"Authorization": f"Bearer {other}"})
    assert status == 403 and "other-value-999" not in body
    assert proxy.stats["secret_refused"] == 1


def test_end_to_end_a_placeholder_to_a_foreign_host_is_refused_and_audited(mitm_proxy, plain_origin):
    proxy, _ca_dir = mitm_proxy
    ph = secret_egress.seal("API_KEY", "real-value-abc-456", ["api.example.com"])
    token = egress.register([], open_network=True, secret_hosts=["api.example.com"],
                            placeholders=[ph])
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    conn.request("GET", f"http://127.0.0.1:{plain_origin}/leak",
                 headers={"Proxy-Authorization": _auth(token), "X-Key": ph})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    assert resp.status == 403 and "API_KEY is not bound to 127.0.0.1" in body

    # Without a placeholder the same request goes through (an open token).
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    conn.request("GET", f"http://127.0.0.1:{plain_origin}/fine",
                 headers={"Proxy-Authorization": _auth(token)})
    resp = conn.getresponse()
    assert resp.status == 200 and json.loads(resp.read())["path"] == "/fine"
    conn.close()

    from common import audit
    rows = audit.query(action="egress.secret_refused")
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    assert any((r.get("details") or {}).get("secrets") == ["API_KEY"] for r in rows)


# ── routes ──────────────────────────────────────────────────────────────────

def test_routes_set_and_edit_hosts(monkeypatch):
    from fastapi.testclient import TestClient

    from common.config import settings
    from dashboard.backend.main import app
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    client = TestClient(app)
    client.post("/api/workspaces", json={"name": "hostsws"})
    put = client.put("/api/workspaces/hostsws/secrets/GH_TOKEN",
                     json={"value": "ghp-route-value-1", "allowed_hosts": ["https://GitHub.com/"]})
    assert put.status_code == 200, put.text
    assert put.json()["allowed_hosts"] == ["github.com"]
    edited = client.put("/api/workspaces/hostsws/secrets/GH_TOKEN/hosts",
                        json={"allowed_hosts": ["api.github.com", "uploads.github.com"]})
    assert edited.status_code == 200, edited.text
    listed = client.get("/api/workspaces/hostsws/secrets").json()
    assert listed[0]["allowed_hosts"] == ["api.github.com", "uploads.github.com"]
    assert "ghp-route-value-1" not in repr(listed)
    bad = client.put("/api/workspaces/hostsws/secrets/GH_TOKEN/hosts", json={"allowed_hosts": ["no way!"]})
    assert bad.status_code == 400
    missing = client.put("/api/workspaces/hostsws/secrets/NOPE/hosts", json={"allowed_hosts": []})
    assert missing.status_code == 400

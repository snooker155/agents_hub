"""`ah setup` for a team (cli/onboard/deploy.py): the ha topology and its
compose overlay, Keycloak in the stack, worker hosts, the Kubernetes values,
and the run from inside the setup container. Whole runs write files without
starting anything; where docker or helm are installed, what they write is fed
to `docker compose config` and `helm template`."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
import yaml
from rich.console import Console

from cli.onboard import deploy as D
from cli.onboard import probe as P
from cli.onboard import wizard as W
from cli.onboard.ui import AnswersAsker, SetupError


def _asker(answers: dict) -> AnswersAsker:
    return AnswersAsker(Console(width=120, record=True), answers)


@pytest.fixture
def fake_probe(monkeypatch):
    monkeypatch.setattr(P, "probe", lambda provider, api_key="", base_url="", timeout=10.0:
                        P.ProbeResult(True, ["gpt-5.4", "gpt-5.4-mini"]) if provider == "openai"
                        else P.ProbeResult(False, error="could not connect"))
    monkeypatch.setattr(W, "_has_docker", lambda: False)


HA = {
    "shape": "docker", "flow": "advanced", "topology": "ha", "api_replicas": "3", "worker_replicas": "4",
    "worker_concurrency": "6", "redis": "bundled", "object_storage": "bundled", "database": "postgres-bundled",
    "auth": "multi", "admin": {"username": "admin", "password": "long-enough-1"},
    "sso": "keycloak", "public_url": "https://hub.example.com",
    "providers": {"openai": {"api_key": "sk-test"}}, "voice": {"mode": "skip"}, "demo": False,
    "https": True, "domain": "hub.example.com", "rag": False, "services": ["browser"], "rate_limit": "120",
    "egress_proxy": False,
}


def _ha_run(tmp_path, **extra):
    target = tmp_path / "hub"
    W.run(_asker({**HA, "dir": str(target), **extra}), yes=True, no_start=True)
    return target


def test_ha_run_writes_overlay_realm_caddy_and_env(tmp_path, fake_probe):
    target = _ha_run(tmp_path)
    env = W.read_env(target / ".env")
    assert env["COMPOSE_FILE"] == f"docker-compose.yml:{D.OVERLAY}"
    assert set(env["COMPOSE_PROFILES"].split(",")) >= {"ha", "postgres", "keycloak", "https", "browser"}
    assert env["BACKEND_SERVERS"] == "backend-api:8000"
    assert env["AGENTS_HUB_WORKER_CONCURRENCY"] == "6"
    # Generated, never the compose files' fallbacks.
    assert env["REDIS_PASSWORD"] and env["REDIS_PASSWORD"] in env["AGENTS_HUB_BROKER_URL"]
    assert env["MINIO_ROOT_PASSWORD"] not in ("", "agents_hub_minio")
    assert env["AWS_SECRET_ACCESS_KEY"] == env["MINIO_ROOT_PASSWORD"]
    assert env["POSTGRES_PASSWORD"] in env["AGENTS_HUB_DATABASE_URL"]
    # Keycloak: public issuer on the hub's domain through Caddy, internal URL for the backend.
    assert env["AUTH_OIDC_ISSUER"] == "https://hub.example.com:8443/realms/agents-hub"
    assert env["AUTH_OIDC_INTERNAL_URL"] == D.KEYCLOAK_INTERNAL
    assert env["KEYCLOAK_ADMIN_PASSWORD"]

    overlay = (target / D.OVERLAY).read_text()
    assert "replicas: 3" in overlay and "replicas: 4" in overlay and "replicas: 0" in overlay
    assert "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in overlay and '"8443:8443"' in overlay
    assert "reverse_proxy keycloak:8080" in (target / "caddy" / "Caddyfile").read_text()

    realm = json.loads((target / "keycloak" / "realm.json").read_text())
    client = next(c for c in realm["clients"] if c["clientId"] == D.CLIENT_ID)
    assert realm["users"] == [] and realm["sslRequired"] == "external"
    assert client["secret"] == env["AUTH_OIDC_CLIENT_SECRET"]
    assert client["redirectUris"] == ["https://hub.example.com/api/auth/oidc/callback"]


def test_a_rerun_keeps_the_passwords_in_force(tmp_path, fake_probe):
    target = _ha_run(tmp_path)
    first = W.read_env(target / ".env")
    W.run(_asker({**HA, "dir": str(target), "existing": "update"}), yes=True, no_start=True)
    second = W.read_env(target / ".env")
    for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD", "MINIO_ROOT_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD",
                "AUTH_OIDC_CLIENT_SECRET", "AGENTS_HUB_SECRET_KEY"):
        assert second[key] == first[key], key


def test_back_to_one_backend_drops_the_overlay_from_compose_file(tmp_path, fake_probe):
    target = _ha_run(tmp_path, sso="none", https=False)
    W.run(_asker({**HA, "dir": str(target), "existing": "update", "topology": "single", "sso": "none",
                  "https": False, "database": "postgres-bundled"}), yes=True, no_start=True)
    env = W.read_env(target / ".env")
    assert env["COMPOSE_FILE"] == "docker-compose.yml"
    assert "ha" not in env["COMPOSE_PROFILES"].split(",")


def test_ha_has_no_sqlite_option(tmp_path, fake_probe):
    with pytest.raises(SetupError, match="expected one of"):
        _ha_run(tmp_path, database="sqlite")


def test_worker_hosts_get_folders_with_the_main_host_address(tmp_path, fake_probe):
    target = _ha_run(tmp_path, worker_hosts={"enabled": True, "shared_dir": "/mnt/hub", "address": "10.0.0.5",
                                             "names": "w1, gpu box", "replicas": "3", "docker": True})
    overlay = (target / D.OVERLAY).read_text()
    for port in ("5432:5432", "6379:6379", "9000:9000"):
        assert port in overlay
    assert "AGENTS_HUB_ROOT: /mnt/hub" in overlay and "- /mnt/hub:/mnt/hub" in overlay
    for name in ("w1", "gpu-box"):
        folder = target / "workers" / name
        compose = yaml.safe_load((folder / "docker-compose.yml").read_text())
        worker = compose["services"]["worker"]
        assert worker["deploy"]["replicas"] == 3
        assert worker["environment"]["AGENTS_HUB_WORKER_MODES"] == "local,docker"
        assert "/var/run/docker.sock:/var/run/docker.sock" in worker["volumes"]
        env = W.read_env(folder / ".env")
        assert (folder / ".env").stat().st_mode & 0o777 == 0o600
        assert "@10.0.0.5:5432/" in env["AGENTS_HUB_DATABASE_URL"]
        assert "@10.0.0.5:6379/" in env["AGENTS_HUB_BROKER_URL"]
        assert env["AGENTS_HUB_BLOB_ENDPOINT"] == "http://10.0.0.5:9000"
        assert env["AGENTS_HUB_ROOT"] == "/mnt/hub"
        assert env["AGENTS_HUB_SECRET_KEY"] == W.read_env(target / ".env")["AGENTS_HUB_SECRET_KEY"]
        assert not any(k.startswith(("COMPOSE_", "KEYCLOAK_", "MINIO_ROOT_", "AGENTS_HUB_BROWSER_")) for k in env)


@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI")
def test_compose_accepts_what_the_wizard_wrote(tmp_path, fake_probe):
    target = _ha_run(tmp_path, worker_hosts={"enabled": True, "shared_dir": "/mnt/hub", "address": "10.0.0.5",
                                             "names": "w1", "replicas": "2", "docker": False})
    out = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=target,
                         capture_output=True, text=True)
    if out.returncode != 0 and "Cannot connect" in out.stderr:
        pytest.skip("docker compose unavailable")
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    assert services["backend"]["deploy"]["replicas"] == 0
    assert services["backend-api"]["deploy"]["replicas"] == 3
    assert list(services["frontend"]["depends_on"]) == ["backend-api"]
    assert services["frontend"]["depends_on"]["backend-api"]["required"] is False
    assert services["backend-api"]["environment"]["AGENTS_HUB_ROOT"] == "/mnt/hub"


def test_kubernetes_values_split_secrets_and_render_with_the_chart(tmp_path, fake_probe):
    target = tmp_path / "k8s"
    W.run(_asker({
        "shape": "kubernetes", "dir": str(target),
        "k8s": {"release": "hub", "namespace": "agents", "host": "hub.corp.example", "tls": "cert-manager",
                "issuer": "le", "tag": "0.12.0"},
        "api_replicas": "3", "worker_replicas": "5", "redis": "url", "redis_url": "redis://:pw@redis.corp:6379/0",
        "object_storage": "s3", "s3": {"url": "s3://b/", "access_key_id": "AKIA", "secret_access_key": "sss"},
        "database_url": "postgresql://hub:pw@pg.corp:5432/hub", "auth": "multi", "sso": "none",
        "providers": {"openai": {"api_key": "sk-test"}}, "rag": False,
    }), yes=True, no_start=True)
    values = yaml.safe_load((target / "values.yaml").read_text())
    secret = yaml.safe_load((target / "secrets.yaml").read_text())["secret"]
    assert (target / "secrets.yaml").stat().st_mode & 0o777 == 0o600
    assert values["backend"]["replicas"] == 3 and values["worker"]["replicas"] == 5
    assert "BACKEND_SERVERS" not in values["env"]
    assert values["ingress"]["annotations"]["cert-manager.io/cluster-issuer"] == "le"
    assert values["image"]["tag"] == "0.12.0"
    for key in ("AGENTS_HUB_DATABASE_URL", "AGENTS_HUB_BROKER_URL", "OPENAI_API_KEY", "AGENTS_HUB_SECRET_KEY",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert key in secret and key not in values["env"], key
    assert not (target / ".env.k8s").exists()
    if shutil.which("helm"):
        out = subprocess.run(["helm", "template", "hub", str(D.CHART), "-f", str(target / "values.yaml"),
                              "-f", str(target / "secrets.yaml")], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


def test_inside_the_container_the_shapes_follow_the_folder(tmp_path, fake_probe):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SetupError, match="docker, kubernetes"):
        W.run(_asker({"shape": "compose"}), in_docker=True, workdir=str(empty), yes=True, no_start=True)
    W.run(_asker({"shape": "docker", "flow": "quick", "database": "sqlite", "auth": "single",
                  "topology": "single", "providers": []}), in_docker=True, workdir=str(empty), yes=True,
          no_start=True)
    assert (empty / "docker-compose.yml").exists() and (empty / ".env").exists()
    with pytest.raises(SetupError, match="--workdir"):
        W.run(_asker({}), in_docker=True)


def test_inside_the_container_the_api_is_reached_through_the_host_gateway():
    assert D.api_base("http://localhost:8080", True) == "http://host.docker.internal:8080"
    assert D.api_base("http://localhost:8080", False) == "http://localhost:8080"


def test_hand_back_gives_root_owned_files_to_the_folder_owner(tmp_path, monkeypatch):
    import os
    (tmp_path / "keycloak").mkdir()
    (tmp_path / "keycloak" / "realm.json").write_text("{}")
    (tmp_path / ".env").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("")
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    real_lstat = os.lstat

    class Root:
        def __init__(self, st):
            self.st_uid, self.st_mtime, self.st_mode = 0, st.st_mtime, st.st_mode

    monkeypatch.setattr(type(tmp_path), "lstat", lambda self: Root(real_lstat(self)))
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: calls.append(str(path)))
    D.hand_back(tmp_path, since=0)
    handed = {p.removeprefix(str(tmp_path)) for p in calls}
    assert {"/.env", "/keycloak", "/keycloak/realm.json"} <= handed
    assert "/src/code.py" not in handed


# ---- reruns: what is in force stays in force --------------------------------


def _recording_asker(answers: dict) -> AnswersAsker:
    # Wide enough that a command in the farewell panel stays on one line.
    return AnswersAsker(Console(width=400, record=True), answers)


@pytest.fixture
def no_hub(monkeypatch):
    # No HTTP to whatever happens to listen on :8080 on this machine.
    monkeypatch.setattr(W, "_existing_users", lambda plan: None)


K8S = {
    "shape": "kubernetes",
    "k8s": {"release": "hub", "namespace": "agents", "host": "hub.corp.example", "tls": "cert-manager",
            "issuer": "le", "tag": "0.12.0"},
    "api_replicas": "3", "worker_replicas": "5", "redis": "url", "redis_url": "redis://:pw@redis.corp:6379/0",
    "object_storage": "s3", "s3": {"url": "s3://b/", "access_key_id": "AKIA", "secret_access_key": "sss"},
    "database_url": "postgresql://hub:pw@pg.corp:5432/hub", "auth": "token",
    "providers": {"openai": {"api_key": "sk-test"}}, "rag": False,
}


def _k8s_files(target):
    return (yaml.safe_load((target / "values.yaml").read_text()),
            yaml.safe_load((target / "secrets.yaml").read_text())["secret"])


def test_kubernetes_rerun_keeps_the_secret_key_the_token_and_the_answers(tmp_path, fake_probe):
    target = tmp_path / "k8s"
    W.run(_asker({**K8S, "dir": str(target)}), yes=True, no_start=True)
    values1, secret1 = _k8s_files(target)
    # Only what changes is answered: everything else defaults to the files in force.
    W.run(_asker({"shape": "kubernetes", "dir": str(target), "existing": "update", "worker_replicas": "7"}),
          yes=True, no_start=True)
    values2, secret2 = _k8s_files(target)
    assert secret2["AGENTS_HUB_SECRET_KEY"] == secret1["AGENTS_HUB_SECRET_KEY"]
    assert secret2["AGENTS_HUB_API_TOKEN"] == secret1["AGENTS_HUB_API_TOKEN"]
    assert secret2 == secret1
    assert values2["worker"]["replicas"] == 7 and values2["backend"]["replicas"] == 3
    assert values2["ingress"] == values1["ingress"] and values2["image"]["tag"] == "0.12.0"
    assert "--install hub " in (target / "values.yaml").read_text()
    assert "--namespace agents " in (target / "values.yaml").read_text()
    # Start over asks everything again, but never makes a new secret key.
    W.run(_asker({**K8S, "dir": str(target), "existing": "fresh"}), yes=True, no_start=True)
    assert _k8s_files(target)[1]["AGENTS_HUB_SECRET_KEY"] == secret1["AGENTS_HUB_SECRET_KEY"]


def test_kubernetes_from_the_container_never_replaces_a_chart_it_did_not_write(tmp_path, fake_probe):
    folder = tmp_path / "gitops"
    (folder / "chart" / "templates").mkdir(parents=True)
    (folder / "chart" / "Chart.yaml").write_text("name: theirs\n")
    W.run(_asker({**K8S}), in_docker=True, workdir=str(folder), yes=True, no_start=True)
    assert (folder / "chart" / "Chart.yaml").read_text() == "name: theirs\n"
    copy = folder / D.CHART_DIR
    assert (copy / "Chart.yaml").exists() and (copy / D.CHART_MARKER).exists()
    assert str(copy) in (folder / "values.yaml").read_text()
    # A rerun replaces its own copy...
    (copy / "stale.txt").write_text("")
    W.run(_asker({"shape": "kubernetes", "existing": "update"}), in_docker=True, workdir=str(folder), yes=True,
          no_start=True)
    assert not (copy / "stale.txt").exists() and (copy / D.CHART_MARKER).exists()
    # ...but a folder of that name without the marker is someone else's.
    (copy / D.CHART_MARKER).unlink()
    (folder / "values.yaml").unlink()
    with pytest.raises(SetupError, match="not written by setup"):
        W.run(_asker({**K8S, "existing": "update"}), in_docker=True, workdir=str(folder), yes=True,
              no_start=True)
    assert (copy / "Chart.yaml").exists() and not (folder / "values.yaml").exists()


EXTERNAL = {"redis": "url", "redis_url": "redis://:theirs@redis.corp:6379/0", "object_storage": "s3",
            "s3": {"url": "s3://my-bucket/hub/", "endpoint": "", "access_key_id": "AKIAOWN",
                   "secret_access_key": "own-secret"},
            "worker_hosts": {"enabled": True, "shared_dir": "/mnt/hub", "address": "10.0.0.5",
                             "names": "w1, gpu box", "replicas": "3", "docker": True}}


def test_a_quickstart_rerun_keeps_external_services_and_worker_hosts(tmp_path, fake_probe, no_hub):
    target = _ha_run(tmp_path, **EXTERNAL)
    first = W.read_env(target / ".env")
    rerun = {k: v for k, v in HA.items() if k not in ("redis", "object_storage", "worker_hosts")}
    W.run(_asker({**rerun, "dir": str(target), "existing": "update", "flow": "quick"}), yes=True, no_start=True)
    env = W.read_env(target / ".env")
    for key in ("AGENTS_HUB_BROKER_URL", "AGENTS_HUB_BLOB_URL", "AGENTS_HUB_BLOB_ENDPOINT", "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY", "AGENTS_HUB_SETUP_SHARED_DIR", "AGENTS_HUB_SETUP_ADDRESS"):
        assert env[key] == first[key], key
    assert env["AGENTS_HUB_BROKER_URL"] == "redis://:theirs@redis.corp:6379/0"
    overlay = (target / D.OVERLAY).read_text()
    assert "AGENTS_HUB_ROOT: /mnt/hub" in overlay and "- /mnt/hub:/mnt/hub" in overlay
    worker = yaml.safe_load((target / "workers" / "gpu-box" / "docker-compose.yml").read_text())["services"]["worker"]
    assert worker["deploy"]["replicas"] == 3 and "/var/run/docker.sock:/var/run/docker.sock" in worker["volumes"]
    # Advanced without worker host answers offers the same hosts again.
    W.run(_asker({**rerun, "dir": str(target), "existing": "update"}), yes=True, no_start=True)
    assert sorted(p.name for p in (target / "workers").iterdir()) == ["gpu-box", "w1"]
    assert "AGENTS_HUB_ROOT: /mnt/hub" in (target / D.OVERLAY).read_text()


def test_back_to_one_backend_unsets_what_only_ha_set(tmp_path, fake_probe, no_hub):
    target = _ha_run(tmp_path, sso="none", https=False)
    first = W.read_env(target / ".env")
    assert first["BACKEND_SERVERS"] == "backend-api:8000" and "minio:9000" in first["AGENTS_HUB_BLOB_ENDPOINT"]
    W.run(_asker({**HA, "dir": str(target), "existing": "update", "topology": "single", "sso": "none",
                  "https": False}), yes=True, no_start=True)
    env = W.read_env(target / ".env")
    for key in ("BACKEND_SERVERS", "AGENTS_HUB_BROKER_URL", "AGENTS_HUB_BLOB_URL", "AGENTS_HUB_BLOB_ENDPOINT",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert env[key] == "", key
    # Kept: the redis service still asks for it if Redis comes back.
    assert env["REDIS_PASSWORD"] == first["REDIS_PASSWORD"]


def test_bundled_postgres_reuses_the_password_keycloak_already_set(tmp_path, fake_probe, no_hub):
    target = tmp_path / "hub"
    base = {**HA, "dir": str(target), "topology": "single", "database": "sqlite", "https": False,
            "public_url": "http://hub.lan:8080"}
    W.run(_asker(base), yes=True, no_start=True)
    first = W.read_env(target / ".env")
    assert first["POSTGRES_PASSWORD"] and not first["AGENTS_HUB_DATABASE_URL"]
    for existing in ("update", "fresh"):
        W.run(_asker({**base, "existing": existing, "database": "postgres-bundled"}), yes=True, no_start=True)
        env = W.read_env(target / ".env")
        assert env["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"], existing
        assert f":{first['POSTGRES_PASSWORD']}@postgres:" in env["AGENTS_HUB_DATABASE_URL"], existing
        assert env["KEYCLOAK_ADMIN_PASSWORD"] == first["KEYCLOAK_ADMIN_PASSWORD"], existing


# ---- Keycloak keeps the realm it imported first -----------------------------


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.ok, self._body = status, status < 400, body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def _kc_plan(tmp_path, **kw):
    plan = W.Plan(shape="docker", target=tmp_path, env_file=tmp_path / ".env", sso="keycloak",
                  public_url="https://hub.example.com",
                  env={"KEYCLOAK_PORT": "8081", "KEYCLOAK_ADMIN_PASSWORD": "kc-admin"},
                  keycloak={"public_url": "https://hub.example.com:8443", "client_secret": "new-secret"}, **kw)
    return plan


def test_a_running_keycloak_gets_the_client_changes_through_its_admin_api(tmp_path, monkeypatch):
    import requests
    calls = []

    def post(url, data=None, timeout=None):
        calls.append(("POST", url, data))
        return _Resp(body={"access_token": "adm"})

    def get(url, params=None, headers=None, timeout=None):
        calls.append(("GET", url, params))
        return _Resp(body=[{"id": "c-1", "clientId": D.CLIENT_ID, "secret": "old",
                            "redirectUris": ["http://host:8080/api/auth/oidc/callback"], "enabled": True}])

    def put(url, json=None, headers=None, timeout=None):
        calls.append(("PUT", url, json, headers))
        return _Resp(204)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "put", put)
    plan = _kc_plan(tmp_path, in_docker=True)
    assert D.sync_keycloak_client(_asker({}), plan) is True
    token_url = calls[0][1]
    assert token_url == "http://host.docker.internal:8081/realms/master/protocol/openid-connect/token"
    assert calls[0][2]["client_id"] == "admin-cli" and calls[0][2]["password"] == "kc-admin"
    [client_put] = [c for c in calls if c[0] == "PUT" and c[1].endswith("/clients/c-1")]
    body = client_put[2]
    assert body["secret"] == "new-secret" and body["enabled"] is True
    assert body["redirectUris"] == ["https://hub.example.com/api/auth/oidc/callback"]
    assert body["webOrigins"] == ["https://hub.example.com"]
    assert client_put[3] == {"Authorization": "Bearer adm"}
    [realm_put] = [c for c in calls if c[0] == "PUT" and c[1].endswith(f"/admin/realms/{D.REALM}")]
    assert realm_put[2] == {"sslRequired": "external"}


def test_a_refused_keycloak_update_prints_the_manual_steps(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda url, data=None, timeout=None: _Resp(401, {"error": "invalid_grant"}))
    a = _recording_asker({})
    assert D.sync_keycloak_client(a, _kc_plan(tmp_path)) is False
    out = a.console.export_text()
    assert "refused the administrator password" in out
    assert "Valid redirect URIs: https://hub.example.com/api/auth/oidc/callback" in out


def test_a_rerun_that_changes_the_realm_updates_keycloak_or_says_how(tmp_path, fake_probe, no_hub, monkeypatch):
    target = _ha_run(tmp_path, https=False, public_url="http://host:8080")
    # Not started: the steps to do by hand.
    a = _recording_asker({**HA, "dir": str(target), "existing": "update", "https": False,
                          "public_url": "https://hub.example.com"})
    W.run(a, yes=True, no_start=True)
    assert "Valid redirect URIs: https://hub.example.com/api/auth/oidc/callback" in a.console.export_text()
    # Started: setup makes the change itself once the stack answers.
    synced = []
    monkeypatch.setattr(W, "_has_docker", lambda: True)
    monkeypatch.setattr(W, "apply_docker", lambda a, plan, start: start)
    monkeypatch.setattr(W, "_bootstrap_http", lambda a, plan: "tok")
    monkeypatch.setattr(W, "_map_admin_group", lambda a, plan, token: None)
    monkeypatch.setattr(W, "_apply_voice_http", lambda a, plan, session: None)
    monkeypatch.setattr(W.L, "apply_http", lambda a, plan, session: None)
    monkeypatch.setattr(D, "sync_keycloak_client", lambda a, plan: synced.append(plan.public_url))
    W.run(_asker({**HA, "dir": str(target), "existing": "update", "https": False,
                  "public_url": "https://hub2.example.com", "point_cli": False}), yes=True)
    assert synced == ["https://hub2.example.com"]


# ---- worker hosts -----------------------------------------------------------


def test_a_worker_host_with_docker_joins_the_socket_group(tmp_path, fake_probe):
    target = _ha_run(tmp_path, worker_hosts={"enabled": True, "shared_dir": "/mnt/hub", "address": "10.0.0.5",
                                             "names": "gpu, plain", "replicas": "1", "docker": True})
    gpu = yaml.safe_load((target / "workers" / "gpu" / "docker-compose.yml").read_text())["services"]["worker"]
    assert gpu["group_add"] == ["${DOCKER_GID:-999}"]
    assert W.read_env(target / "workers" / "gpu" / ".env")["DOCKER_GID"] == "999"
    plan = W.Plan(shared_dir="/mnt/hub", main_address="10.0.0.5")
    plain = yaml.safe_load(D.worker_compose_text({"name": "p", "replicas": 1, "docker": False}, plan))
    assert "group_add" not in plain["services"]["worker"]
    # The main host's group is not the worker host's.
    assert "DOCKER_GID" not in D.worker_env({"DOCKER_GID": "0", "OPENAI_API_KEY": "k"}, plan)


def test_adding_worker_hosts_to_a_used_install_leaves_it_stopped_with_the_copy_command(
        tmp_path, fake_probe, no_hub, monkeypatch):
    target = _ha_run(tmp_path, sso="none", https=False)
    commands = []
    monkeypatch.setattr(W, "_has_docker", lambda: True)
    monkeypatch.setattr(W, "_run", lambda a, cmd, cwd=None, env=None: commands.append(cmd))
    monkeypatch.setattr(W, "_wait_healthy", lambda a, base, seconds=300: False)
    a = _recording_asker({**HA, "dir": str(target), "existing": "update", "sso": "none", "https": False,
                          "worker_hosts": EXTERNAL["worker_hosts"]})
    W.run(a, yes=True)
    assert not any(cmd[:3] == ["docker", "compose", "up"] for cmd in commands)
    out = a.console.export_text()
    assert "docker compose down" in out
    assert "docker run --rm -v hub_agents_hub_data:/from -v /mnt/hub:/to alpine cp -a /from/. /to/" in out
    # Once the directory is in force, a rerun moves nothing.
    plan = W.Plan(shape="docker", target=target, shared_dir="/mnt/hub",
                  current=W.read_env(target / ".env"))
    assert D.state_move(plan) == []
    assert D.state_move(W.Plan(shape="compose", target=target, shared_dir="/mnt/b",
                               current={"AGENTS_HUB_SETUP_SHARED_DIR": "/mnt/hub"}))[-1] == \
        "sudo cp -a /mnt/hub/. /mnt/b/"


def test_hand_back_also_gives_back_the_backups_setup_made(tmp_path, monkeypatch):
    import os
    import time
    old = time.time() - 3600
    for name in (".env.bak-20260101-000000", "docker-compose.yml.bak-20260101-000000", "notes.txt"):
        (tmp_path / name).write_text("")
        os.utime(tmp_path / name, (old, old))
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    real_lstat = os.lstat

    class Root:
        def __init__(self, st):
            self.st_uid, self.st_mtime, self.st_mode = 0, st.st_mtime, st.st_mode

    monkeypatch.setattr(type(tmp_path), "lstat", lambda self: Root(real_lstat(self)))
    monkeypatch.setattr(os, "lchown", lambda path, uid, gid: calls.append(os.path.basename(path)))
    D.hand_back(tmp_path, since=time.time())
    assert set(calls) == {".env.bak-20260101-000000", "docker-compose.yml.bak-20260101-000000"}


# ---- off the shared directory again ------------------------------------------

def _start_recorder(monkeypatch) -> list:
    commands: list = []
    monkeypatch.setattr(W, "_has_docker", lambda: True)
    monkeypatch.setattr(W, "_run", lambda a, cmd, cwd=None, env=None: commands.append(cmd))
    monkeypatch.setattr(W, "_wait_healthy", lambda a, base, seconds=300: False)
    return commands


@pytest.mark.parametrize("leave", [
    {"worker_hosts": {"enabled": False}},   # Advanced, no worker hosts any more
    {"topology": "single"},                 # back to one backend
])
def test_leaving_the_shared_directory_prints_the_copy_back_and_leaves_the_stack_stopped(
        tmp_path, fake_probe, no_hub, monkeypatch, leave):
    """The reverse of the move into the shared directory: started at once,
    the hub would open on its own volume without the state in use there."""
    target = _ha_run(tmp_path, sso="none", https=False, worker_hosts=EXTERNAL["worker_hosts"])
    commands = _start_recorder(monkeypatch)
    a = _recording_asker({**HA, "dir": str(target), "existing": "update", "sso": "none", "https": False,
                          **({"database": "postgres-bundled"} if "topology" in leave else {}), **leave})
    W.run(a, yes=True)
    assert not any(cmd[:3] == ["docker", "compose", "up"] for cmd in commands)
    out = a.console.export_text()
    assert "Not starting the stack: the hub's state has to be copied back into the hub's own volume" in out
    assert "docker compose down" in out
    assert "docker run --rm -v /mnt/hub:/from -v hub_agents_hub_data:/to alpine cp -a /from/. /to/" in out
    assert W.read_env(target / ".env")["AGENTS_HUB_SETUP_SHARED_DIR"] == ""
    # Once the file no longer names the directory, a rerun copies nothing and starts.
    plan = W.Plan(shape="docker", target=target, current=W.read_env(target / ".env"))
    assert D.state_copy(plan) == ("", [])


def test_the_copy_back_for_compose_goes_to_the_checkout_state_folder(tmp_path):
    plan = W.Plan(shape="compose", target=tmp_path, current={"AGENTS_HUB_SETUP_SHARED_DIR": "/mnt/hub"})
    where, commands = D.state_copy(plan)
    assert where == f"back into {tmp_path / '.agents_hub'}"
    assert commands[-1] == f"sudo cp -a /mnt/hub/. {tmp_path / '.agents_hub'}/"
    # Still on the directory, or never on one: nothing to copy.
    assert D.state_move_back(W.Plan(shape="compose", target=tmp_path, shared_dir="/mnt/hub",
                                    current={"AGENTS_HUB_SETUP_SHARED_DIR": "/mnt/hub"})) == []
    assert D.state_move_back(W.Plan(shape="compose", target=tmp_path, current={"WEB_PORT": "8080"})) == []
    assert D.state_move_back(W.Plan(shape="local", target=tmp_path,
                                    current={"AGENTS_HUB_SETUP_SHARED_DIR": "/mnt/hub"})) == []


# ---- what a worker host set for itself ---------------------------------------

def test_a_rerun_keeps_what_the_worker_host_set_in_its_own_env(tmp_path, fake_probe, no_hub):
    """workers/<name>/.env was rewritten whole on every run, so the
    DOCKER_GID the person set for that machine went back to 999."""
    target = _ha_run(tmp_path, sso="none", https=False, worker_hosts=EXTERNAL["worker_hosts"])
    env_file = target / "workers" / "gpu-box" / ".env"
    text = env_file.read_text().replace('DOCKER_GID="999"', 'DOCKER_GID="988"')
    env_file.write_text(text + 'HTTPS_PROXY="http://proxy.lab:3128"\nAGENTS_HUB_ROOT="/elsewhere"\n')
    W.run(_asker({**HA, "dir": str(target), "existing": "update", "sso": "none", "https": False,
                  "flow": "quick"}), yes=True, no_start=True)
    env = W.read_env(env_file)
    assert env["DOCKER_GID"] == "988"
    assert env["HTTPS_PROXY"] == "http://proxy.lab:3128"
    assert env["AGENTS_HUB_ROOT"] == "/mnt/hub"          # setup's own key is setup's
    assert env_file.read_text().count("DOCKER_GID=") == 1
    assert (env_file.stat().st_mode & 0o777) == 0o600
    assert W.read_env(target / "workers" / "w1" / ".env")["DOCKER_GID"] == "999"


def test_a_quickstart_rerun_keeps_keycloak_in_force(tmp_path, fake_probe, no_hub):
    """QuickStart on one backend skipped the sign-on step, so the keycloak
    profile dropped out of COMPOSE_PROFILES and nobody could sign in."""
    target = tmp_path / "hub"
    base = {**HA, "dir": str(target), "topology": "single", "database": "sqlite", "https": False,
            "public_url": "http://hub.lan:8080"}
    W.run(_asker(base), yes=True, no_start=True)
    first = W.read_env(target / ".env")
    assert "keycloak" in first["COMPOSE_PROFILES"].split(",")
    quick = {k: v for k, v in base.items() if k not in ("sso", "public_url")}
    W.run(_asker({**quick, "existing": "update", "flow": "quick"}), yes=True, no_start=True)
    env = W.read_env(target / ".env")
    assert "keycloak" in env["COMPOSE_PROFILES"].split(",")
    for key in ("AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_SECRET", "KEYCLOAK_ADMIN_PASSWORD", "AUTH_PUBLIC_URL"):
        assert env[key] == first[key], key

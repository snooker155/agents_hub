"""common/stack.py: each probe ok, off and failing with its client replaced,
the compose parser over `docker ps --format json` output, and the doctor
checks built on the probes. No network, no docker, no Redis is touched."""
from __future__ import annotations

import json
import subprocess
import types

import pytest

from common import doctor, stack

SHAPE = {"id", "kind", "label", "status", "summary", "detail", "load", "capacity", "unit", "latency_ms"}


def _shape(comp):
    assert set(comp) == SHAPE
    assert comp["status"] in {"ok", "warn", "fail", "off", "unknown"}


@pytest.mark.sqlite_only
def test_database_on_sqlite_is_ok_with_sizes():
    comp = stack.probe_database()
    _shape(comp)
    assert comp["status"] == "ok" and comp["detail"]["dialect"] == "sqlite"
    assert "size_bytes" in comp["detail"]


def test_database_failure_is_reported(monkeypatch):
    from common import db
    monkeypatch.setattr(db, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    comp = stack.probe_database()
    assert comp["status"] == "fail" and "down" in comp["summary"]


def test_database_postgres_load_and_capacity(monkeypatch):
    from common import db

    class Conn:
        def execute(self, sql, params=()):
            value = {"SHOW server_version": "16.2", "SHOW max_connections": "100"}.get(sql)
            if value is None:
                value = 90 if "numbackends" in sql else 1234
            return types.SimpleNamespace(fetchone=lambda: (value,))

    monkeypatch.setattr(db, "dialect", lambda: "postgres")
    monkeypatch.setattr(db, "get_conn", lambda: Conn())
    comp = stack.probe_database()
    assert (comp["load"], comp["capacity"], comp["unit"]) == (90, 100, "connections")
    assert comp["status"] == "warn" and comp["detail"]["server_version"] == "16.2"


def test_broker_off_fail_and_ok(monkeypatch):
    monkeypatch.setattr(stack, "_broker_url", lambda: "")
    assert stack.probe_broker()["status"] == "off"

    monkeypatch.setattr(stack, "_broker_url", lambda: "redis://x:6379/0")
    from common import broker_bridge

    class Client:
        def ping(self):
            return True

        def info(self):
            return {"connected_clients": 7, "used_memory_human": "1M"}

        def close(self):
            pass

    monkeypatch.setattr(broker_bridge, "get_redis", lambda: object())
    redis = pytest.importorskip("redis")
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: Client())
    comp = stack.probe_broker()
    assert comp["status"] == "ok" and comp["load"] == 7 and comp["detail"]["used_memory_human"] == "1M"

    class Dead(Client):
        def ping(self):
            raise ConnectionError("refused")

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: Dead())
    comp = stack.probe_broker()
    assert comp["status"] == "fail" and "refused" in comp["summary"]


def test_blob_store_off_ok_and_fail(monkeypatch):
    from common import blobs
    monkeypatch.setattr(blobs, "configured", lambda: False)
    assert stack.probe_blob_store()["status"] == "off"

    monkeypatch.setattr(blobs, "configured", lambda: True)
    timeouts = []
    store = types.SimpleNamespace(
        probe=lambda timeout: timeouts.append(timeout) or {"bucket": "b", "roundtrip_ok": True})
    monkeypatch.setattr(blobs, "store", lambda: store)
    comp = stack.probe_blob_store()
    assert comp["status"] == "ok" and comp["detail"]["bucket"] == "b"
    # The store is told the probe's own timeout, not left on its client's default.
    assert timeouts == [stack.PROBE_TIMEOUT_SECONDS]

    def boom(timeout):
        raise OSError("no route")
    monkeypatch.setattr(blobs, "store", lambda: types.SimpleNamespace(probe=boom))
    assert stack.probe_blob_store()["status"] == "fail"


def test_s3_probe_puts_reads_and_deletes():
    from common.blobs import S3BlobStore
    calls = []

    class Client:
        def put_object(self, **kw):
            calls.append(("put", kw["Key"]))
            self.body = kw["Body"]

        def get_object(self, **kw):
            calls.append(("get", kw["Key"]))
            return {"Body": types.SimpleNamespace(read=lambda: self.body)}

        def delete_object(self, **kw):
            calls.append(("delete", kw["Key"]))

    out = S3BlobStore("s3://bkt/pre", endpoint="http://minio:9000", client=Client()).probe()
    assert out["roundtrip_ok"] and out["bucket"] == "bkt"
    assert [c[0] for c in calls] == ["put", "get", "delete"]
    assert calls[0][1].startswith("pre/.health/")


def test_s3_probe_uses_a_short_timeout_client_without_retries(monkeypatch):
    """Regression: the probe ran on the shared client with boto3's defaults
    (60 s connect and read, several attempts), so a black-holed endpoint held
    the doctor and the Cluster page's probe thread for minutes."""
    import sys

    from common.blobs import S3BlobStore
    made = []

    class Client:
        def put_object(self, **kw):
            self.body = kw["Body"]

        def get_object(self, **kw):
            return {"Body": types.SimpleNamespace(read=lambda: self.body)}

        def delete_object(self, **kw):
            pass

    def fake_client(service, **kwargs):
        made.append((service, kwargs))
        return Client()

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=fake_client))
    try:
        import botocore.config  # noqa: F401 - the real Config when requirements-blobs.txt is installed
    except ImportError:
        # CI installs requirements.lock only, without boto: a stand-in that
        # keeps what the probe passes is enough for what this test reads.
        monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
        monkeypatch.setitem(sys.modules, "botocore.config",
                            types.SimpleNamespace(Config=lambda **kw: types.SimpleNamespace(**kw)))
    store = S3BlobStore("s3://bkt/pre", endpoint="http://minio:9000")
    assert store.probe(timeout=2.5)["roundtrip_ok"]
    assert store.probe(timeout=2.5)["roundtrip_ok"]
    assert len(made) == 1  # built once, then reused
    service, kwargs = made[0]
    assert service == "s3" and kwargs["endpoint_url"] == "http://minio:9000"
    config = kwargs["config"]
    assert (config.connect_timeout, config.read_timeout) == (2.5, 2.5)
    assert config.retries["total_max_attempts"] == 1
    # The shared client, the one uploads use, keeps boto3's defaults.
    assert store._client is None


def test_component_gives_up_on_a_probe_that_hangs(monkeypatch):
    """Regression: the doctor's single-probe path had no timeout at all."""
    import threading
    import time

    release = threading.Event()

    def stuck():
        release.wait(10)
        return stack._component("blob_store", "blob_store", "Object store", "ok", "late")

    monkeypatch.setattr(stack, "probe_blob_store", stuck)
    monkeypatch.setattr(stack, "JOIN_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()
    try:
        comp = stack.component("blob_store")
    finally:
        release.set()
    assert time.monotonic() - started < 2
    _shape(comp)
    assert comp["status"] == "unknown" and "did not finish" in comp["summary"]
    # A probe that answers in time still comes back as is.
    monkeypatch.setattr(stack, "probe_oidc",
                        lambda: stack._component("oidc", "oidc", "Identity provider", "off", "x"))
    assert stack.component("oidc")["status"] == "off"


class _Redis:
    def __init__(self):
        self.pinged = 0

    def ping(self):
        self.pinged += 1
        return True

    def info(self):
        return {"connected_clients": 2}

    def close(self):
        pass


def _beat(role):
    from common.members import MemberBeat
    beat = MemberBeat(role)
    beat.is_alive = lambda: True
    return beat


@pytest.mark.parametrize("threads,expected", [
    ([], "ok"),                       # `ah doctor` over the direct backend: no bridge is expected
    (["worker"], "ok"),               # the worker beats but never runs a bridge
    (["api"], "warn"),                # an API replica whose bridge never connected
])
def test_broker_without_a_bridge_pings_redis_and_fails_only_where_a_bridge_belongs(
        monkeypatch, threads, expected):
    """Regression: a missing bridge was a `fail` before Redis was even
    pinged, so `ah doctor` (which never holds a bridge) exited 1 against a
    healthy Redis."""
    redis = pytest.importorskip("redis")
    from common import broker_bridge
    client = _Redis()
    monkeypatch.setattr(stack, "_broker_url", lambda: "redis://x:6379/0")
    monkeypatch.setattr(broker_bridge, "get_redis", lambda: None)
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: client)
    beats = [_beat(role) for role in threads]
    monkeypatch.setattr(stack.threading, "enumerate", lambda: beats)

    comp = stack.probe_broker()
    _shape(comp)
    assert client.pinged == 1
    assert comp["status"] == expected and comp["detail"]["bridge"] is False
    assert comp["load"] == 2

    monkeypatch.setattr(stack, "component", lambda cid: comp)
    status, msg, _detail = doctor.check_broker({})
    assert status == expected  # never "fail": Redis answered
    assert msg.key == f"broker.{expected}"


def test_ah_doctor_passes_the_broker_check_without_a_bridge(monkeypatch):
    """The CLI end of the same regression: the broker row is ok, not the
    failure that made `ah doctor` exit 1."""
    redis = pytest.importorskip("redis")
    from common import broker_bridge
    monkeypatch.setattr(stack, "_broker_url", lambda: "redis://x:6379/0")
    monkeypatch.setattr(broker_bridge, "get_redis", lambda: None)
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _Redis())
    # No member beats in the CLI. Pinned, because a MemberBeat thread an
    # earlier test left alive in this process would make the probe expect a
    # bridge and turn the row into a warning.
    monkeypatch.setattr(stack.threading, "enumerate", lambda: [])
    status, msg, detail = doctor.check_broker({})
    assert status == "ok" and msg.key == "broker.ok" and detail["bridge"] is False


# ── the cluster routes are for the operator ──────────────────────────────────

@pytest.fixture
def cluster_app(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import cluster

    monkeypatch.setattr(cluster, "build_map", lambda: {"hub_nodes": []})
    monkeypatch.setattr(cluster, "_stack", lambda: [])
    app = FastAPI()
    app.include_router(cluster.router)
    app.include_router(cluster.deployment_alias_router)
    return TestClient(app)


ROUTES = [("get", "/api/cluster"), ("get", "/api/cluster/stack"), ("get", "/api/cluster/members"),
          ("get", "/api/deployment"), ("get", "/api/deployment/stack"),
          ("get", "/api/cluster/members/x/logs"), ("delete", "/api/cluster/members/x")]


@pytest.mark.parametrize("method,path", ROUTES)
def test_cluster_routes_refuse_a_plain_member_in_multi_mode(cluster_app, monkeypatch, method, path):
    """Regression: any signed-in account read every account's nodes and the
    internal service URLs, and each poll ran the probes (a bucket write, a
    forced discovery refetch, docker calls)."""
    from common import identity
    from common.auth import MULTI, Principal
    monkeypatch.setattr(identity, "current_mode", lambda: MULTI)
    monkeypatch.setattr(identity, "request_principal",
                        lambda request: Principal(id="u1", username="bob", role="member"))
    assert getattr(cluster_app, method)(path).status_code == 403
    monkeypatch.setattr(identity, "request_principal", lambda request: None)
    assert getattr(cluster_app, method)(path).status_code == 401


def test_cluster_routes_answer_an_admin_and_the_single_operator(cluster_app, monkeypatch):
    from common import identity
    from common.auth import MULTI, Principal
    assert cluster_app.get("/api/cluster").status_code == 200  # single mode: one operator
    monkeypatch.setattr(identity, "current_mode", lambda: MULTI)
    monkeypatch.setattr(identity, "request_principal",
                        lambda request: Principal(id="u2", username="root", role="admin"))
    assert cluster_app.get("/api/cluster").json() == {"hub_nodes": []}
    assert cluster_app.get("/api/deployment/stack").json() == {"components": []}


def test_oidc_off_ok_and_fail(monkeypatch):
    from ee import oidc
    monkeypatch.setattr(oidc, "enabled", lambda: False)
    assert stack.probe_oidc()["status"] == "off"
    monkeypatch.setattr(oidc, "enabled", lambda: True)
    monkeypatch.setattr(oidc, "discovery", lambda force=False: {"issuer": "x"})
    assert stack.probe_oidc()["status"] == "ok"

    def down(force=False):
        raise oidc.OidcError("provider_unreachable", "no route")
    monkeypatch.setattr(oidc, "discovery", down)
    assert stack.probe_oidc()["status"] == "fail"


def test_browser_off_ok_fail_and_full(monkeypatch):
    from common import browser_service as bs
    monkeypatch.setattr(bs, "configured_url", lambda: "")
    assert stack.probe_browser()["status"] == "off"
    monkeypatch.setattr(bs, "configured_url", lambda: "http://b:3000")
    monkeypatch.setattr(bs, "healthz", lambda url, timeout=2.0: {"reachable": True, "sessions": 1, "max_sessions": 4})
    comp = stack.probe_browser()
    assert (comp["status"], comp["load"], comp["capacity"], comp["unit"]) == ("ok", 1, 4, "sessions")
    monkeypatch.setattr(bs, "healthz", lambda url, timeout=2.0: {"reachable": True, "sessions": 4, "max_sessions": 4})
    assert stack.probe_browser()["status"] == "warn"
    monkeypatch.setattr(bs, "healthz", lambda url, timeout=2.0: {"reachable": False, "error": "ConnectError"})
    assert stack.probe_browser()["status"] == "fail"


def test_models_runtime(monkeypatch):
    import httpx
    from providers import local_models as lm
    monkeypatch.setattr(lm, "runtime_settings", lambda: {"url": "", "token": "", "managed": False})
    assert stack.probe_models_runtime()["status"] == "off"
    monkeypatch.setattr(lm, "runtime_settings", lambda: {"url": "http://m:1", "token": "t", "managed": False})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(
        200, json={"loaded": ["a"], "max_loaded": 2, "mode": "lru"}, request=httpx.Request("GET", "http://m:1")))
    comp = stack.probe_models_runtime()
    assert (comp["status"], comp["load"], comp["capacity"], comp["unit"]) == ("ok", 1, 2, "models")

    def refuse(*a, **k):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(httpx, "get", refuse)
    assert stack.probe_models_runtime()["status"] == "fail"


def test_egress_proxy(monkeypatch):
    from common import config
    from environments import egress
    monkeypatch.setattr(egress, "enabled", lambda: False)
    assert stack.probe_egress_proxy()["status"] == "off"
    monkeypatch.setattr(egress, "enabled", lambda: True)
    monkeypatch.setattr(egress, "running", lambda: object())
    assert stack.probe_egress_proxy()["status"] == "ok"
    monkeypatch.setattr(egress, "running", lambda: None)
    monkeypatch.setattr(config, "hub_role", lambda: "api")
    assert stack.probe_egress_proxy()["status"] == "unknown"
    monkeypatch.setattr(config, "hub_role", lambda: "worker")
    assert stack.probe_egress_proxy()["status"] == "warn"


PS = "\n".join(json.dumps(r) for r in [
    {"Names": "p-backend-api-1", "State": "running", "Labels": "a=b,com.docker.compose.service=backend-api"},
    {"Names": "p-backend-api-2", "State": "running", "Labels": "com.docker.compose.service=backend-api"},
    {"Names": "p-worker-1", "State": "running", "Labels": "com.docker.compose.service=worker"},
    {"Names": "p-worker-2", "State": "exited", "Labels": "com.docker.compose.service=worker"},
    {"Names": "p-setup-1", "State": "exited", "Labels": "com.docker.compose.service=setup"},
])


def test_parse_compose_ps_counts_per_service():
    rows = {r["service"]: r for r in stack.parse_compose_ps(PS + "\nnot json\n", {"worker": 2})}
    assert (rows["backend-api"]["running"], rows["backend-api"]["total"], rows["backend-api"]["state"]) == (2, 2, "running")
    assert (rows["worker"]["running"], rows["worker"]["wanted"], rows["worker"]["state"]) == (1, 2, "partial")
    assert rows["setup"]["state"] == "exited"


def test_compose_probe_warns_when_fewer_replicas_than_wanted(monkeypatch):
    monkeypatch.setattr(stack.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(stack, "_wanted_replicas", lambda: {"backend-api": 2, "worker": 2})

    def fake(args):
        out = json.dumps({"com.docker.compose.project": "p"}) if args[0] == "inspect" else PS
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(stack, "_docker", fake)
    comp = stack.probe_compose()
    assert comp["status"] == "warn" and "orker: 1 of 2 running" in comp["summary"]
    assert comp["detail"]["project"] == "p"
    assert {s["service"] for s in comp["detail"]["services"]} == {"backend-api", "worker", "setup"}


def test_compose_probe_unknown_without_docker_or_project(monkeypatch):
    monkeypatch.setattr(stack.shutil, "which", lambda name: None)
    assert stack.probe_compose()["status"] == "unknown"
    monkeypatch.setattr(stack.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(stack, "_docker", lambda args: subprocess.CompletedProcess(args, 0, "{}", ""))
    comp = stack.probe_compose()
    assert comp["status"] == "unknown" and "compose" in comp["summary"]
    monkeypatch.setattr(stack, "_docker", lambda args: subprocess.CompletedProcess(args, 1, "", "no such"))
    assert stack.probe_compose()["status"] == "unknown"


def test_components_runs_every_probe_and_survives_a_crash(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(stack, "probe_broker", boom)
    rows = stack.components()
    assert [r["id"] for r in rows] == ["database", "broker", "blob_store", "oidc", "browser",
                                       "models_runtime", "egress_proxy", "compose"]
    assert next(r for r in rows if r["id"] == "broker")["status"] == "unknown"
    for r in rows:
        _shape(r)


@pytest.mark.parametrize("check,probe,key", [
    (doctor.check_broker, "broker", "broker"),
    (doctor.check_blob_store, "blob_store", "blobStore"),
    (doctor.check_oidc, "oidc", "oidc"),
])
def test_doctor_checks_skip_ok_and_fail(monkeypatch, check, probe, key):
    def result(status, detail=None):
        return stack._component(probe, probe, probe, status, "summary", detail=detail, latency_ms=3)

    for status, expected in (("off", "skip"), ("ok", "ok"), ("fail", "fail")):
        monkeypatch.setattr(stack, "component", lambda cid, s=status: result(s, {"error": "refused"}))
        got, msg, _detail = check({})
        assert got == expected
        assert msg.key == f"{key}.{ {'skip': 'off', 'ok': 'ok', 'fail': 'unreachable'}[expected] }"
    assert "refused" in msg


def test_doctor_lists_the_new_checks_after_outbox():
    ids = [c[0] for c in doctor.CHECKS]
    i = ids.index("outbox")
    assert ids[i + 1:i + 4] == ["broker", "blob_store", "oidc"]

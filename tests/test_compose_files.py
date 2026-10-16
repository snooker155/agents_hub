"""The two compose files' contracts for the guided setup and the production
services (docs/deployment.md): `setup`, Keycloak, and the quickstart's `ha`
topology and browser.

A plain YAML parse, so it needs no docker daemon. The default services (no
profile) must stay exactly as they were: a plain `docker compose up` starts no
setup and no new service.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILE = ROOT / "docker-compose.yml"
QUICKSTART_FILE = ROOT / "deploy" / "quickstart" / "docker-compose.yml"


class _Loader(yaml.SafeLoader):
    """Compose-only tags (!reset, !override) are plain values here."""


for _tag in ("!reset", "!override"):
    _Loader.add_multi_constructor(
        _tag, lambda loader, suffix, node: loader.construct_object(node, deep=True)
    )


def _services(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_Loader)["services"]


def _in_profile(services: dict, profile: str) -> set[str]:
    return {n for n, s in services.items() if profile in (s.get("profiles") or [])}


@pytest.fixture(params=[ROOT_FILE, QUICKSTART_FILE], ids=["root", "quickstart"])
def services(request) -> dict:
    return _services(request.param)


def test_default_services_are_unchanged():
    root = {n for n, s in _services(ROOT_FILE).items() if not s.get("profiles")}
    assert root == {"backend", "frontend", "models"}
    quick = {n for n, s in _services(QUICKSTART_FILE).items() if not s.get("profiles")}
    assert quick == {"init", "backend", "models", "frontend"}


def test_setup_service(services):
    setup = services["setup"]
    assert setup["profiles"] == ["setup"]
    assert setup["command"] == ["setup"]
    assert setup["user"] == "0:0"
    assert setup["tty"] is True and setup["stdin_open"] is True
    assert setup["restart"] == "no"
    assert setup["working_dir"] == "${PWD}"
    assert "${PWD}:${PWD}" in setup["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in setup["volumes"]
    assert "host.docker.internal:host-gateway" in setup["extra_hosts"]
    assert setup["environment"]["AGENTS_HUB_SETUP_HOST_DIR"] == "${PWD}"
    assert "depends_on" not in setup and "ports" not in setup


def test_setup_image_source():
    assert _services(QUICKSTART_FILE)["setup"]["image"].startswith(
        "ghcr.io/snooker155/agents-hub-backend:"
    )
    build = _services(ROOT_FILE)["setup"]["build"]
    assert build["target"] == "backend" and build["context"] == "."


def test_keycloak_profile(services):
    assert _in_profile(services, "keycloak") >= {"keycloak", "keycloak-db-init", "postgres"}
    init = services["keycloak-db-init"]
    assert init["restart"] == "no"
    assert init["depends_on"]["postgres"]["condition"] == "service_healthy"
    kc = services["keycloak"]
    assert kc["image"].startswith("quay.io/keycloak/keycloak:26.")
    assert kc["command"] == ["start", "--import-realm"]
    env = kc["environment"]
    assert env["KC_DB_URL"] == "jdbc:postgresql://postgres:5432/keycloak"
    assert env["KC_HOSTNAME"] == "${KEYCLOAK_PUBLIC_URL:-http://localhost:8081}"
    assert env["KC_BOOTSTRAP_ADMIN_PASSWORD"] == "${KEYCLOAK_ADMIN_PASSWORD:-}"
    assert "${KEYCLOAK_PORT:-8081}:8080" in kc["ports"]
    assert kc["depends_on"]["keycloak-db-init"]["condition"] == "service_completed_successfully"
    assert "./keycloak/realm.json:/opt/keycloak/data/import/realm.json:ro" in kc["volumes"]


def test_redis_password_is_optional(services):
    redis = services["redis"]
    script = redis["command"][-1]
    assert "$$REDIS_PASSWORD" in script and "--requirepass" in script
    assert redis["environment"]["REDIS_PASSWORD"] == "${REDIS_PASSWORD:-}"


def test_quickstart_ha_topology():
    services = _services(QUICKSTART_FILE)
    ha = _in_profile(services, "ha")
    assert ha >= {"backend-api", "worker", "redis", "minio", "minio-init", "postgres"}
    assert services["backend-api"]["environment"]["AGENTS_HUB_ROLE"] == "api"
    worker = services["worker"]
    assert worker["environment"]["AGENTS_HUB_ROLE"] == "worker"
    assert worker["command"] == ["python", "-m", "runtime.worker"]
    assert worker["environment"]["AGENTS_HUB_WORKER_MODES"].endswith(":-local}")
    for name in ("backend-api", "worker"):
        spec = services[name]
        assert spec["deploy"]["replicas"] == 2
        assert spec["environment"]["AGENTS_HUB_ROOT"] == "/data"
        assert "agents_hub_data:/data" in spec["volumes"]
        assert "./.env:/app/.env" in spec["volumes"]
        assert spec["depends_on"]["init"]["condition"] == "service_completed_successfully"
        assert "/var/run/docker.sock:/var/run/docker.sock" not in spec["volumes"]
    assert "scale" in services["redis"]["profiles"]
    assert services["frontend"]["environment"]["BACKEND_SERVERS"] == "${BACKEND_SERVERS:-backend:8000}"


def test_quickstart_browser():
    services = _services(QUICKSTART_FILE)
    browser = services["browser"]
    assert browser["profiles"] == ["browser"]
    assert browser["image"].startswith("ghcr.io/snooker155/agents-hub-browser:")
    assert browser["networks"] == ["browser_net"]
    assert "browser_net" in services["backend"]["networks"]
    assert browser["environment"]["BROWSER_TOKEN"] == "${AGENTS_HUB_BROWSER_TOKEN:-}"


def test_dockerfile_carries_compose_plugin_and_setup_launcher():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG DOCKER_COMPOSE_VERSION=" in text
    assert "/usr/local/lib/docker/cli-plugins/docker-compose" in text
    assert "--in-docker --workdir" in text and "/usr/local/bin/setup" in text

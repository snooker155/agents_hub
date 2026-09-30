"""docker-compose.yml's profile shape.

The default `docker compose up` must keep starting exactly `backend` and
`frontend`, the two services with no `profiles` key at all, no matter what is
added under the `ha` profile (docs/deployment.md). This is a plain YAML
parse, not `docker compose config`: it does not need a docker daemon, or even
docker installed, to run in CI.
"""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _load_services() -> dict:
    with open(COMPOSE_PATH, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return doc["services"]


def test_default_profile_is_backend_and_frontend_only():
    services = _load_services()
    default_services = {
        name for name, spec in services.items() if not spec.get("profiles")
    }
    assert default_services == {"backend", "frontend"}


def test_ha_profile_contains_the_full_topology():
    services = _load_services()
    ha_services = {
        name for name, spec in services.items() if "ha" in (spec.get("profiles") or [])
    }
    assert ha_services == {"postgres", "redis", "minio", "minio-init", "backend-api", "worker"}


def test_worker_service_runs_the_worker_role():
    services = _load_services()
    worker = services["worker"]
    assert worker["environment"]["AGENTS_HUB_ROLE"] == "worker"


def test_backend_api_service_runs_the_api_role():
    services = _load_services()
    backend_api = services["backend-api"]
    assert backend_api["environment"]["AGENTS_HUB_ROLE"] == "api"


def test_backend_api_and_worker_share_the_backend_build():
    """Both are the same image as `backend`, via the `x-backend` anchor."""
    services = _load_services()
    for name in ("backend-api", "worker"):
        build = services[name]["build"]
        assert build["dockerfile"] == "Dockerfile"
        assert build["target"] == "backend"


def test_ha_profile_services_have_two_replicas():
    services = _load_services()
    for name in ("backend-api", "worker"):
        assert services[name]["deploy"]["replicas"] == 2

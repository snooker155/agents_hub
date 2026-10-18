"""The compose file's contract for one person on their own machine
(docs/deployment.md): the backend and the dashboard by default, the browser
service under its profile, and both published ports on 127.0.0.1 unless
HUB_BIND says otherwise, because the API has no login in this edition.

A plain YAML parse, so it needs no docker daemon.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILE = ROOT / "docker-compose.yml"


def _services() -> dict:
    with open(ROOT_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["services"]


def test_default_services_are_the_backend_and_the_dashboard():
    services = _services()
    assert {n for n, s in services.items() if not s.get("profiles")} == {"backend", "frontend"}


def test_the_browser_service_is_behind_its_profile_and_publishes_nothing():
    browser = _services()["browser"]
    assert browser["profiles"] == ["browser"]
    assert "ports" not in browser
    assert browser["networks"] == ["browser_net"]


def test_published_ports_listen_on_localhost_by_default():
    services = _services()
    for name in ("backend", "frontend"):
        for port in services[name]["ports"]:
            assert port.startswith("${HUB_BIND:-127.0.0.1}:"), (name, port)


def test_no_shared_service_of_a_team_install_is_left():
    services = _services()
    for gone in ("redis", "postgres", "keycloak", "minio", "worker", "backend-api", "setup", "models"):
        assert gone not in services

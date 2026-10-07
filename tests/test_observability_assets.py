"""The Grafana dashboard and the Prometheus alert rules (deploy/grafana,
deploy/prometheus, docs/observability.md) only name metrics /metrics serves."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from common import metrics

ROOT = Path(__file__).resolve().parents[1]


def _served_names() -> set[str]:
    names = set()
    for m in metrics.collect():
        names.add(m["name"])
        if m["type"] == "histogram":
            names |= {m["name"] + s for s in ("_bucket", "_sum", "_count")}
    # published only once the SLO window holds data
    return names | {"agents_hub_run_start_seconds", "agents_hub_run_start_seconds_count"}


def _used(expr: str) -> set[str]:
    return set(re.findall(r"\bagents_hub_[a-z_]+", expr))


def test_the_dashboard_only_uses_served_metrics():
    dash = json.loads((ROOT / "deploy" / "grafana" / "agents-hub-dashboard.json").read_text())
    assert dash["panels"]
    served = _served_names()
    for panel in dash["panels"]:
        for target in panel["targets"]:
            missing = _used(target["expr"]) - served
            assert not missing, f"{panel['title']}: {missing}"


def test_the_alert_rules_parse_and_only_use_served_metrics():
    rules = yaml.safe_load((ROOT / "deploy" / "prometheus" / "alerts.yml").read_text())
    served = _served_names()
    alerts = [r for g in rules["groups"] for r in g["rules"]]
    assert {"AgentsHubAgentFailing", "AgentsHubRunsSlow", "AgentsHubToolErrors"} <= {r["alert"] for r in alerts}
    for rule in alerts:
        assert not (_used(rule["expr"]) - served), rule["alert"]


def test_the_observability_page_is_indexed_and_linked():
    index = json.loads((ROOT / "docs" / "index.json").read_text())
    pages = index["docs"]
    entry = next(p for p in pages if p["id"] == "observability")
    text = (ROOT / "docs" / "observability.md").read_text()
    for heading in entry["headings"]:
        assert f"## {heading}" in text or f"### {heading}" in text
    for linking in ("service-health.md", "runbook.md"):
        assert "observability.md" in (ROOT / "docs" / linking).read_text()

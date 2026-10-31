"""
The few Kubernetes API calls the hub makes from inside its pod, with the
pod's service account (no client library): read and set a Deployment's
replica count, read and annotate a pod. Used by common/worker_fleet.py to
scale the worker Deployment the Helm chart installs
(deploy/helm/agents-hub, ``workerScaling``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

SA_DIR = Path(os.environ.get("AGENTS_HUB_K8S_SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount"))


class K8sError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Kubernetes API {status}: {message}")
        self.status = status


def in_cluster() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST")) and (SA_DIR / "token").is_file()


def namespace() -> str:
    try:
        return (SA_DIR / "namespace").read_text(encoding="utf-8").strip() or "default"
    except OSError:
        return "default"


def _base() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{port}"


def request(method: str, path: str, body: Optional[Dict[str, Any]] = None, *,
            content_type: str = "application/json") -> Dict[str, Any]:
    """One API call; the decoded answer. Raises :class:`K8sError` on a
    non-2xx answer."""
    import requests
    token = (SA_DIR / "token").read_text(encoding="utf-8").strip()
    ca = SA_DIR / "ca.crt"
    resp = requests.request(method, _base() + path, json=body, timeout=15,
                            verify=str(ca) if ca.is_file() else True,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type,
                                     "Accept": "application/json"})
    if resp.status_code >= 300:
        try:
            message = resp.json().get("message") or resp.text
        except ValueError:
            message = resp.text
        raise K8sError(resp.status_code, str(message)[:300])
    return resp.json() if resp.content else {}


def _scale_path(deployment: str) -> str:
    return f"/apis/apps/v1/namespaces/{namespace()}/deployments/{deployment}/scale"


def get_replicas(deployment: str) -> int:
    return int(((request("GET", _scale_path(deployment)).get("spec") or {}).get("replicas")) or 0)


def change_replicas(deployment: str, delta: int, *, floor: int = 0) -> int:
    """Add ``delta`` (negative to take away) to the Deployment's replica
    count, never below ``floor``; the new count. Optimistic: the write
    carries the version it read, and a conflict is read and tried again."""
    for _ in range(3):
        scale = request("GET", _scale_path(deployment))
        current = int(((scale.get("spec") or {}).get("replicas")) or 0)
        target = max(floor, current + delta)
        body = {"apiVersion": "autoscaling/v1", "kind": "Scale",
                "metadata": {"name": deployment, "namespace": namespace(),
                             "resourceVersion": (scale.get("metadata") or {}).get("resourceVersion")},
                "spec": {"replicas": target}}
        try:
            request("PUT", _scale_path(deployment), body)
            return target
        except K8sError as exc:
            if exc.status != 409:
                raise
    raise K8sError(409, f"the replica count of {deployment} kept changing")


def get_pod(name: str) -> Dict[str, Any]:
    return request("GET", f"/api/v1/namespaces/{namespace()}/pods/{name}")


def annotate_pod(name: str, annotations: Dict[str, str]) -> None:
    request("PATCH", f"/api/v1/namespaces/{namespace()}/pods/{name}",
            {"metadata": {"annotations": annotations}}, content_type="application/merge-patch+json")


__all__ = ["K8sError", "annotate_pod", "change_replicas", "get_pod", "get_replicas", "in_cluster",
           "namespace", "request"]

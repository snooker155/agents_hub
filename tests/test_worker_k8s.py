"""
Workers in Kubernetes (common/worker_fleet.py over common/k8s_api.py): a hub
in a pod of the Helm chart adds replicas to the worker Deployment, counts the
pods still starting, and takes a drained worker's pod away by giving it the
lowest deletion cost before lowering the replica count. The worker there
stays up once drained instead of exiting (runtime/worker.py).
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from common import k8s_api, members, worker_autoscale, worker_fleet


class FakeK8s:
    """The scale subresource and pods of one namespace, with a version
    check on writes like the API server's."""

    def __init__(self, replicas=2):
        self.replicas, self.version = replicas, 1
        self.pods = {}
        self.conflicts = 0
        self.calls = []

    def __call__(self, method, url, json=None, headers=None, **kw):
        self.calls.append((method, url))
        assert headers["Authorization"] == "Bearer sa-token"
        path = url.split(":443", 1)[1]
        if path.endswith("/deployments/hub-worker/scale"):
            if method == "GET":
                return Resp(200, {"metadata": {"resourceVersion": str(self.version)},
                                  "spec": {"replicas": self.replicas}})
            if self.conflicts:
                self.conflicts -= 1
                self.version += 1
                return Resp(409, {"message": "conflict"})
            assert json["metadata"]["resourceVersion"] == str(self.version)
            self.replicas, self.version = json["spec"]["replicas"], self.version + 1
            return Resp(200, json)
        if "/pods/" in path:
            name = path.rsplit("/", 1)[1]
            pod = self.pods.setdefault(name, {"metadata": {"name": name, "annotations": {}}})
            if method == "PATCH":
                assert headers["Content-Type"] == "application/merge-patch+json"
                pod["metadata"]["annotations"].update(json["metadata"]["annotations"])
            return Resp(200, pod)
        return Resp(404, {"message": "not found"})


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = b"x"
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def k8s(tmp_path, monkeypatch):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("sa-token")
    (sa / "namespace").write_text("hub")
    monkeypatch.setattr(k8s_api, "SA_DIR", sa)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setenv("AGENTS_HUB_WORKER_DEPLOYMENT", "hub-worker")
    fake = FakeK8s()
    import requests
    monkeypatch.setattr(requests, "request", fake)
    return fake


def test_the_launcher_in_a_pod(k8s, monkeypatch):
    assert worker_fleet.launcher() == "kubernetes"
    assert worker_fleet.launcher_detail() == "hub-worker"
    monkeypatch.delenv("AGENTS_HUB_WORKER_DEPLOYMENT")
    # A pod with no Deployment to scale starts nothing, not a process in itself.
    assert worker_fleet.launcher() == ""
    with pytest.raises(worker_fleet.FleetError):
        worker_fleet.add()


def test_add_raises_the_replica_count_through_a_conflict(k8s):
    k8s.conflicts = 1
    assert worker_fleet.add(2) == ["hub-worker: 4 replicas"]
    assert k8s.replicas == 4
    assert all("/namespaces/hub/" in url for _, url in k8s.calls)


def worker(member_id, **load):
    rec = members.register("worker", member_id=member_id, capabilities={"origin": "kubernetes", "concurrency": 4})
    if load:
        members.beat(load, member_id=member_id)
    return rec


def test_pods_still_starting_are_pending(k8s):
    worker("w-1")
    k8s.replicas = 3
    assert worker_fleet.pending() == 2
    k8s.replicas = 1
    assert worker_fleet.pending() == 0


def test_a_drained_worker_pod_is_taken_away_once(k8s):
    rec = worker("w-1")
    pod = rec["host"]
    assert worker_fleet.reconcile() == []           # not asked to stop
    worker_fleet.stop("w-1")
    assert worker_fleet.reconcile() == []           # asked, still finishing its runs
    members.beat({"tracked": 0, "draining": True, "drained": True}, member_id="w-1")
    assert worker_fleet.reconcile() == [pod]
    notes = k8s.pods[pod]["metadata"]["annotations"]
    assert notes["controller.kubernetes.io/pod-deletion-cost"] == "-100000"
    assert notes["agents-hub/retiring"] == "true"
    assert k8s.replicas == 1
    # Until the pod is gone it is not taken away twice.
    assert worker_fleet.reconcile() == [] and k8s.replicas == 1


def test_the_autoscaler_takes_drained_pods_away_even_when_off(k8s):
    worker("w-1")
    worker_fleet.stop("w-1")
    members.beat({"drained": True, "draining": True}, member_id="w-1")
    worker_autoscale.save_policy({"enabled": False})
    worker_autoscale.Autoscaler().tick(now=1.0)
    assert worker_autoscale.events()[0]["action"] == "retire"
    assert k8s.replicas == 1


def test_nothing_more_starts_while_workers_come_up():
    pol = {**worker_autoscale.DEFAULTS, "enabled": True, "min_workers": 3, "max_workers": 5}
    one = [{"member_id": "a", "runs": 4, "concurrency": 4, "carriers": 0, "stoppable": True}]
    waiting = {"runs": 9, "carriers": 0, "oldest_seconds": 600}
    assert worker_autoscale.decide(pol, one, waiting, {}, pending=1) == {
        "action": "up", "count": 1, "reason": "below_min"}
    assert worker_autoscale.decide(pol, one, waiting, {}, pending=2) == {"action": None, "reason": "starting"}


def test_a_worker_in_a_deployment_stays_up_once_drained(monkeypatch):
    from runtime import worker as worker_mod
    monkeypatch.setenv("AGENTS_HUB_WORKER_EXIT_ON_DRAIN", "0")
    monkeypatch.setattr(worker_mod, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(worker_mod, "DRAIN_SECONDS", 0)
    w = worker_mod.Worker(concurrency=1)
    w.request_drain()
    done = threading.Event()
    threading.Thread(target=lambda: (w.run_forever(install_signals=False), done.set()), daemon=True).start()
    deadline = time.time() + 5
    while not w.drained and time.time() < deadline:
        time.sleep(0.02)
    assert w.drained and not done.is_set()
    w.request_drain(False)
    assert not w.drained
    # The pod's deletion (SIGTERM) still ends it.
    w.request_stop()
    assert done.wait(5)

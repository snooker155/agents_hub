"""
Object store mirror: common/blobs.py.

``LocalBlobStore`` is the default, no-op backend every single-host deployment
keeps using; ``S3BlobStore`` is exercised against an in-memory fake S3 client
(no network, no moto — just the handful of calls the module makes:
``upload_file``/``download_file``/``head_object``/``delete_object``/
``list_objects_v2``, the same names and argument shapes ``boto3.client("s3")``
uses). Covers the round trip, the "never raise" contract (a store outage must
never fail a run or a read), ``rel()``'s root guard, and the two reader
fallbacks this step wires up: a run log served by the stats route's reader
function, and a view asset missing on this host.

Run: ``python -m pytest tests/test_blobs.py -q``
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from common import blobs
from common.paths import AGENTS_HUB_ROOT

# dashboard/backend/routes/stats.py imports sibling modules (e.g. ``models``)
# assuming the backend dir itself is on sys.path, the way the running backend
# has it (dashboard/backend/main.py's own sys.path setup) — mirrors
# tests/test_identity_routes.py's same fixup.
_BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class FakeS3Client:
    """In-memory stand-in for a boto3 S3 client: just the calls S3BlobStore
    makes, with the same argument shapes, no network."""

    def __init__(self, *, fail: bool = False):
        self.objects: dict = {}
        self.fail = fail

    def upload_file(self, filename, bucket, key):
        if self.fail:
            raise RuntimeError("boom")
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        if self.fail:
            raise RuntimeError("boom")
        if key not in self.objects:
            raise KeyError(key)
        Path(filename).write_bytes(self.objects[key])

    def head_object(self, Bucket, Key):
        if self.fail:
            raise RuntimeError("boom")
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def delete_object(self, Bucket, Key):
        if self.fail:
            raise RuntimeError("boom")
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        if self.fail:
            raise RuntimeError("boom")
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


@pytest.fixture(autouse=True)
def _reset_blob_store():
    """Every test starts from the real, unconfigured singleton (no
    AGENTS_HUB_BLOB_URL is set anywhere in the test environment)."""
    blobs.reset()
    yield
    blobs.reset()


def _write(rel_path: str, text: str = "hello") -> Path:
    p = AGENTS_HUB_ROOT / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── local backend: the default ───────────────────────────────────────────────

def test_local_backend_reads_what_is_on_disk():
    store = blobs.LocalBlobStore()
    p = _write("run_logs/local_only.log", "local")
    assert store.ensure_local(blobs.rel(p)) == p
    assert store.read_text(blobs.rel(p)) == "local"
    # Nothing ever written: nothing to read.
    assert store.ensure_local("run_logs/nowhere.log") is None
    assert store.read_text("run_logs/nowhere.log", default="fallback") == "fallback"
    assert store.read_bytes("run_logs/nowhere.log") is None
    assert store.exists("run_logs/nowhere.log") is False
    assert blobs.rel(p) in store.list("run_logs")


def test_module_level_store_is_the_local_one():
    assert isinstance(blobs.store(), blobs.LocalBlobStore)


# ── failures are swallowed, never raised ─────────────────────────────────────

def test_module_functions_never_raise_even_with_a_broken_store(monkeypatch):
    class Boom(blobs.BlobStore):
        def ensure_local(self, rel_path):
            raise RuntimeError("boom")

        def read_text(self, rel_path, default=None):
            raise RuntimeError("boom")

        def read_bytes(self, rel_path):
            raise RuntimeError("boom")

        def exists(self, rel_path):
            raise RuntimeError("boom")

        def delete(self, rel_path):
            raise RuntimeError("boom")

        def list(self, prefix):
            raise RuntimeError("boom")

    monkeypatch.setattr(blobs, "store", lambda: Boom())
    assert blobs.ensure_local("x") is None
    assert blobs.read_text("x", default="dd") == "dd"
    assert blobs.read_bytes("x") is None
    assert blobs.exists("x") is False
    blobs.delete("x")  # must not raise
    assert blobs.list("x") == []


def test_a_bad_blob_url_falls_back_to_local(monkeypatch):
    monkeypatch.setattr("common.config.live_setting", lambda key, default: (
        "not-an-s3-url" if key == blobs.BLOB_URL_ENV else default))
    assert isinstance(blobs.store(), blobs.LocalBlobStore)


# ── rel() guards the root ────────────────────────────────────────────────────

def test_rel_refuses_paths_outside_the_root():
    with pytest.raises(ValueError):
        blobs.rel("/etc/passwd")


def test_rel_accepts_a_relative_path_as_already_rooted():
    assert blobs.rel("run_logs/x.log") == "run_logs/x.log"


def test_rel_round_trips_an_absolute_path_under_the_root():
    p = AGENTS_HUB_ROOT / "run_logs" / "agent_run_rel.log"
    assert blobs.rel(p) == "run_logs/agent_run_rel.log"


# ── reader fallbacks wired up in this step ───────────────────────────────────

def test_stats_route_returns_none_when_the_log_is_nowhere(monkeypatch):
    from dashboard.backend.routes import stats as stats_routes

    monkeypatch.setattr(blobs, "store", lambda: blobs.LocalBlobStore())
    assert stats_routes._read_run_log_text("no-such-run-at-all") is None



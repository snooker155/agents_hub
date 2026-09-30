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

def test_local_backend_is_a_noop_and_unconfigured():
    store = blobs.LocalBlobStore()
    assert store.configured() is False
    p = _write("run_logs/local_only.log", "local")
    # Nothing to copy anywhere on one host.
    assert store.mirror(blobs.rel(p)) is False
    assert store.ensure_local(blobs.rel(p)) == p
    assert store.read_text(blobs.rel(p)) == "local"
    # Nothing ever written locally: no remote to fetch it from either.
    assert store.ensure_local("run_logs/nowhere.log") is None
    assert store.read_text("run_logs/nowhere.log", default="fallback") == "fallback"
    assert store.read_bytes("run_logs/nowhere.log") is None
    assert store.exists("run_logs/nowhere.log") is False
    assert blobs.rel(p) in store.list("run_logs")


def test_module_level_configured_is_false_without_a_blob_url():
    assert blobs.configured() is False
    assert isinstance(blobs.store(), blobs.LocalBlobStore)


# ── S3 backend: round trip ───────────────────────────────────────────────────

def test_s3_round_trip_mirror_ensure_local_read_list_delete():
    client = FakeS3Client()
    store = blobs.S3BlobStore("s3://test-bucket/prefix", client=client)

    p = _write("run_logs/agent_run_s3case.log", "s3 body")
    rel_path = blobs.rel(p)

    assert store.configured() is True
    assert store.mirror(rel_path) is True
    assert client.objects.get(f"prefix/{rel_path}") == b"s3 body"

    # Delete the local copy; ensure_local re-downloads it from the store.
    p.unlink()
    assert not p.exists()
    fetched = store.ensure_local(rel_path)
    assert fetched == p
    assert p.read_text(encoding="utf-8") == "s3 body"

    # Re-delete locally to prove read_text/read_bytes/exists also fetch.
    p.unlink()
    assert store.read_text(rel_path) == "s3 body"
    p.unlink()
    assert store.read_bytes(rel_path) == b"s3 body"
    p.unlink()
    assert store.exists(rel_path) is True

    listed = store.list("run_logs")
    assert rel_path in listed

    store.delete(rel_path)
    assert not p.exists()
    assert f"prefix/{rel_path}" not in client.objects
    assert store.exists(rel_path) is False


def test_s3_mirror_is_noop_when_the_local_file_is_missing():
    store = blobs.S3BlobStore("s3://bucket", client=FakeS3Client())
    assert store.mirror("run_logs/never_written.log") is False


def test_s3_without_a_prefix_uses_bare_keys():
    client = FakeS3Client()
    store = blobs.S3BlobStore("s3://bucket-only", client=client)
    p = _write("run_logs/agent_run_bare.log", "x")
    rel_path = blobs.rel(p)
    store.mirror(rel_path)
    assert rel_path in client.objects


# ── failures are swallowed, never raised ─────────────────────────────────────

def test_s3_failures_never_raise():
    client = FakeS3Client(fail=True)
    store = blobs.S3BlobStore("s3://bucket/prefix", client=client)
    p = _write("run_logs/agent_run_failcase.log", "body")
    rel_path = blobs.rel(p)

    assert store.mirror(rel_path) is False
    p.unlink()
    assert store.ensure_local(rel_path) is None
    assert store.read_text(rel_path, default="d") == "d"
    assert store.read_bytes(rel_path) is None
    assert store.exists(rel_path) is False
    store.delete(rel_path)  # must not raise
    assert store.list("run_logs") == []


def test_module_functions_never_raise_even_with_a_broken_store(monkeypatch):
    class Boom(blobs.BlobStore):
        def configured(self):
            raise RuntimeError("boom")

        def mirror(self, rel_path):
            raise RuntimeError("boom")

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
    assert blobs.configured() is False
    assert blobs.mirror("x") is False
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

def test_stats_route_reads_a_run_log_mirrored_from_another_host(monkeypatch):
    """A run whose process ran on a different worker/replica: its log was
    mirrored there and never written on this host at all. The stats route's
    reader function must still serve it, fetching it from the store."""
    from managers import run_manager as rm
    from dashboard.backend.routes import stats as stats_routes

    client = FakeS3Client()
    fake_store = blobs.S3BlobStore("s3://bucket/prefix", client=client)
    monkeypatch.setattr(blobs, "store", lambda: fake_store)

    run_id = "on-another-host"
    log_rel = f"run_logs/agent_run_{run_id}.log"
    log_path = AGENTS_HUB_ROOT / log_rel
    # Simulate the other host: write, mirror, then remove the local copy so
    # this host only ever sees it through the store.
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("remote log body", encoding="utf-8")
    fake_store.mirror(log_rel)
    log_path.unlink()
    assert not log_path.exists()

    rm.upsert_run({"run_id": run_id, "agent_id": "a", "status": "completed",
                   "log_file": str(log_path)})

    text = stats_routes._read_run_log_text(run_id)
    assert text == "remote log body"


def test_stats_route_returns_none_when_the_log_is_nowhere(monkeypatch):
    from dashboard.backend.routes import stats as stats_routes

    monkeypatch.setattr(blobs, "store", lambda: blobs.LocalBlobStore())
    assert stats_routes._read_run_log_text("no-such-run-at-all") is None


def test_view_asset_missing_locally_is_fetched_from_the_store(monkeypatch, tmp_path):
    """A view asset copied in at create_view time is mirrored immediately
    (views/store.py's _mirror_view_dir). If the local copy later disappears —
    the view was created on another host, say — view_asset_path must still
    serve it by fetching it from the store."""
    from views.store import create_view, view_asset_path

    client = FakeS3Client()
    fake_store = blobs.S3BlobStore("s3://bucket/prefix", client=client)
    monkeypatch.setattr(blobs, "store", lambda: fake_store)

    src = tmp_path / "data.json"
    src.write_text('{"values": [1, 2, 3]}', encoding="utf-8")
    env = create_view(
        "table", "T", {"columns": ["a"], "rows": [[1]]},
        summary="t", asset_sources={"data.json": str(src)},
    )

    local_path = view_asset_path(env.view_id, "data.json")
    assert local_path is not None and local_path.is_file()

    # Remove only the local copy; create_view already mirrored it.
    local_path.unlink()
    assert not local_path.exists()

    fetched = view_asset_path(env.view_id, "data.json")
    assert fetched is not None
    assert fetched.read_text(encoding="utf-8").startswith("{")

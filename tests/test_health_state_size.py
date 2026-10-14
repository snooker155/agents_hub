"""The state directory size in the health snapshot (common/health.py).

Walking the whole state directory can take minutes on a slow mount, so the
snapshot must never wait for it: the size is measured in a background thread
and the snapshot reports the last result.
"""
import threading
import time

from common import health


def _reset(monkeypatch, root):
    monkeypatch.setattr(health, "AGENTS_HUB_ROOT", root)
    monkeypatch.setattr(health, "_state_bytes", None)
    monkeypatch.setattr(health, "_state_measured_at", None)
    monkeypatch.setattr(health, "_state_measuring", False)


def _wait_measured(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with health._state_lock:
            if not health._state_measuring:
                return
        time.sleep(0.01)
    raise AssertionError("state size measurement did not finish")


def test_dir_size_counts_nested_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"1" * 10)
    (tmp_path / "y.bin").write_bytes(b"2" * 5)
    assert health._dir_size(tmp_path) == 15
    assert health._dir_size(tmp_path / "missing") == 0


def test_state_size_is_measured_in_the_background(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "f.bin").write_bytes(b"x" * 7)
    release = threading.Event()
    real = health._dir_size

    def slow(path):
        release.wait(5)
        return real(path)

    monkeypatch.setattr(health, "_dir_size", slow)
    started = time.monotonic()
    assert health._state_dir_bytes() is None
    assert time.monotonic() - started < 1.0
    # A second call while the first walk runs starts no other walk.
    assert health._state_dir_bytes() is None
    assert sum(t.name == "state-size" for t in threading.enumerate()) == 1
    release.set()
    _wait_measured()
    assert health._state_dir_bytes() == 7


def test_state_size_is_reused_until_stale(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "f.bin").write_bytes(b"x" * 3)
    health._state_dir_bytes()
    _wait_measured()
    (tmp_path / "g.bin").write_bytes(b"x" * 4)
    assert health._state_dir_bytes() == 3
    monkeypatch.setattr(health, "STATE_SIZE_TTL", 0.0)
    health._state_dir_bytes()
    _wait_measured()
    assert health._state_dir_bytes() == 7

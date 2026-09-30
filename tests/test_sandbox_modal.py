"""sandbox/modal.py against a fake ``modal`` module injected into
sys.modules: the real package is not installed anywhere in this checkout, so
every test here is self-contained. Covers availability, network mapping,
timeouts, errors and that the sandbox is always terminated.
"""
from __future__ import annotations

import sys
import types

import pytest

from sandbox.base import SandboxNetwork, SandboxRequest


class FakeStream:
    def __init__(self, text=""):
        self.text = text

    def read(self):
        return self.text


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = FakeStream(stdout)
        self.stderr = FakeStream(stderr)
        self._waited = False

    def wait(self):
        self._waited = True


class FakeApp:
    @classmethod
    def lookup(cls, name, create_if_missing=False):
        return cls()


class FakeFilesystem:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def write_text(self, content, path):
        self.sandbox.written[path] = content


class FakeSandbox:
    created = []
    terminated = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.written = {}
        self.filesystem = FakeFilesystem(self)
        self.exec_result = FakeProcess(0, "ok\n", "")

    @classmethod
    def create(cls, **kwargs):
        sb = cls(**kwargs)
        cls.created.append(sb)
        return sb

    def exec(self, *args, timeout=None, workdir=None):
        self.last_exec = args
        return self.exec_result

    def terminate(self):
        FakeSandbox.terminated.append(self)


class FakeImage:
    @classmethod
    def debian_slim(cls):
        return cls()

    @classmethod
    def from_registry(cls, name):
        return cls()


def _fake_modal_module():
    mod = types.ModuleType("modal")
    mod.App = FakeApp
    mod.Sandbox = FakeSandbox
    mod.Image = FakeImage
    return mod


@pytest.fixture
def fake_modal(monkeypatch):
    FakeSandbox.created.clear()
    FakeSandbox.terminated.clear()
    original_create = FakeSandbox.__dict__["create"]
    mod = _fake_modal_module()
    monkeypatch.setitem(sys.modules, "modal", mod)
    from common.config import settings
    monkeypatch.setattr(settings, "modal_token_id", "id-123")
    monkeypatch.setattr(settings, "modal_token_secret", "secret-456")
    monkeypatch.setattr(settings, "modal_image", "")
    yield mod
    FakeSandbox.create = original_create


@pytest.fixture(autouse=True)
def _no_leftover_module(monkeypatch):
    monkeypatch.delitem(sys.modules, "modal", raising=False)
    yield
    monkeypatch.delitem(sys.modules, "modal", raising=False)


def _provider():
    from sandbox.modal import ModalProvider
    return ModalProvider()


# ── availability ─────────────────────────────────────────────────────────────

def test_unavailable_without_the_sdk():
    ok, reason = _provider().is_available()
    assert not ok and "not installed" in reason


def test_unavailable_without_tokens(fake_modal, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "modal_token_id", "")
    monkeypatch.setattr(settings, "modal_token_secret", "")
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    ok, reason = _provider().is_available()
    assert not ok and "MODAL_TOKEN_ID" in reason


def test_available_exports_credentials_into_the_environment(fake_modal, monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    ok, reason = _provider().is_available()
    assert ok and reason == ""
    import os
    assert os.environ["MODAL_TOKEN_ID"] == "id-123"
    assert os.environ["MODAL_TOKEN_SECRET"] == "secret-456"


# ── network mapping ──────────────────────────────────────────────────────────

def test_network_none_blocks_network(fake_modal):
    result = _provider().run(SandboxRequest(language="python", code="print(1)"))
    assert result.exit_code == 0
    assert FakeSandbox.created[0].kwargs["block_network"] is True


def test_network_limited_sets_the_domain_allowlist_with_wildcards(fake_modal):
    result = _provider().run(SandboxRequest(
        language="python", code="print(1)",
        network=SandboxNetwork(type="limited", hosts=["pypi.org"])))
    assert result.exit_code == 0
    allowlist = FakeSandbox.created[0].kwargs["outbound_domain_allowlist"]
    assert "pypi.org" in allowlist and "*.pypi.org" in allowlist


def test_network_limited_with_no_hosts_is_refused(fake_modal):
    result = _provider().run(SandboxRequest(
        language="python", code="print(1)", network=SandboxNetwork(type="limited", hosts=[])))
    assert result.exit_code == -1 and "no allowed hosts" in result.error
    assert not FakeSandbox.created


def test_network_unrestricted_passes_no_network_kwarg(fake_modal):
    _provider().run(SandboxRequest(
        language="python", code="print(1)", network=SandboxNetwork(type="unrestricted")))
    kwargs = FakeSandbox.created[0].kwargs
    assert "block_network" not in kwargs and "outbound_domain_allowlist" not in kwargs


# ── running, errors, cleanup ─────────────────────────────────────────────────

def test_writes_the_snippet_and_extra_files(fake_modal):
    _provider().run(SandboxRequest(
        language="python", code="print(1)", extra_files={"helper.py": "x = 1"}))
    sb = FakeSandbox.created[0]
    assert sb.written["/sandbox/main.py"] == "print(1)"
    assert sb.written["/sandbox/helper.py"] == "x = 1"


def test_non_zero_exit_is_reported(fake_modal):
    def _create(**kwargs):
        sb = FakeSandbox(**kwargs)
        sb.exec_result = FakeProcess(7, "partial\n", "bad input\n")
        FakeSandbox.created.append(sb)
        return sb
    FakeSandbox.create = staticmethod(_create)

    result = _provider().run(SandboxRequest(language="python", code="raise SystemExit(7)"))
    assert result.exit_code == 7 and result.stdout == "partial\n" and not result.error
    assert FakeSandbox.terminated  # always cleaned up


def test_run_failure_is_reported_and_still_terminates(fake_modal):
    def _create(**kwargs):
        sb = FakeSandbox(**kwargs)

        def _boom(*a, **kw):
            raise RuntimeError("connection reset")
        sb.exec = _boom
        FakeSandbox.created.append(sb)
        return sb
    FakeSandbox.create = staticmethod(_create)

    result = _provider().run(SandboxRequest(language="python", code="1"))
    assert result.exit_code == -1 and "modal run failed" in result.error
    assert FakeSandbox.terminated


def test_sandbox_creation_failure_is_reported(fake_modal):
    def _raise(**kwargs):
        raise RuntimeError("quota exceeded")
    FakeSandbox.create = staticmethod(_raise)

    result = _provider().run(SandboxRequest(language="python", code="1"))
    assert result.exit_code == -1 and "modal sandbox creation failed" in result.error
    assert not FakeSandbox.terminated  # nothing was created to terminate


def test_workspace_mount_is_refused(fake_modal, tmp_path):
    result = _provider().run(SandboxRequest(language="python", code="1", workspace=str(tmp_path)))
    assert result.exit_code == -1 and "mount_workspace" in result.error
    assert not FakeSandbox.created


def test_stdin_is_refused(fake_modal):
    result = _provider().run(SandboxRequest(language="python", code="1", stdin="hi"))
    assert result.exit_code == -1 and "stdin" in result.error
    assert not FakeSandbox.created


def test_unsupported_language(fake_modal):
    result = _provider().run(SandboxRequest(language="ruby", code="1"))
    assert result.exit_code == -1 and "unsupported language" in result.error

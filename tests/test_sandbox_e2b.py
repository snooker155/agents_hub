"""sandbox/e2b.py against a fake ``e2b`` module injected into sys.modules:
no real e2b package or API key is needed or used. Covers availability,
network mapping, timeouts, errors and that the sandbox is always killed.
"""
from __future__ import annotations

import sys
import types

import pytest

from sandbox.base import SandboxNetwork, SandboxRequest


class FakeSandboxException(Exception):
    pass


class FakeAuthenticationException(FakeSandboxException):
    pass


class FakeTimeoutException(FakeSandboxException):
    pass


class FakeCommandExitException(FakeSandboxException):
    def __init__(self, exit_code, stdout, stderr):
        super().__init__(f"exit {exit_code}")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeCommandResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeFiles:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def write(self, path, content):
        self.sandbox.written[path] = content


class FakeCommands:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def run(self, cmd, timeout=None):
        return self.sandbox.on_run(cmd, timeout)


class FakeSandbox:
    created = []
    killed = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.written = {}
        self.files = FakeFiles(self)
        self.commands = FakeCommands(self)
        self.on_run = lambda cmd, timeout: FakeCommandResult(0, "ok\n", "")

    @classmethod
    def create(cls, **kwargs):
        if kwargs.get("_raise_auth"):
            raise FakeAuthenticationException("bad key")
        sb = cls(**kwargs)
        cls.created.append(sb)
        return sb

    def kill(self):
        FakeSandbox.killed.append(self)


def _fake_e2b_module():
    mod = types.ModuleType("e2b")
    mod.Sandbox = FakeSandbox
    mod.AuthenticationException = FakeAuthenticationException
    mod.TimeoutException = FakeTimeoutException
    mod.CommandExitException = FakeCommandExitException
    mod.SandboxException = FakeSandboxException
    return mod


@pytest.fixture
def fake_e2b(monkeypatch):
    FakeSandbox.created.clear()
    FakeSandbox.killed.clear()
    # A few tests replace FakeSandbox.create (a classmethod, so a class-level
    # attribute shared across the whole module) to inject a failure; restore
    # it so that override never leaks into a later test.
    original_create = FakeSandbox.__dict__["create"]
    mod = _fake_e2b_module()
    monkeypatch.setitem(sys.modules, "e2b", mod)
    from common.config import settings
    monkeypatch.setattr(settings, "e2b_api_key", "test-key")
    monkeypatch.setattr(settings, "e2b_template", "")
    yield mod
    FakeSandbox.create = original_create


@pytest.fixture(autouse=True)
def _no_leftover_module(monkeypatch):
    monkeypatch.delitem(sys.modules, "e2b", raising=False)
    yield
    monkeypatch.delitem(sys.modules, "e2b", raising=False)


def _provider():
    from sandbox.e2b import E2BProvider
    return E2BProvider()


# ── availability ─────────────────────────────────────────────────────────────

def test_unavailable_without_the_sdk(monkeypatch):
    # The real `e2b` package (1.11.1, an older API) is installed in this
    # checkout's own environment; simulate its absence the way it would look
    # for an operator who has not added the sandbox extra at all.
    import sandbox.e2b as e2b_provider

    def _missing():
        raise ImportError("no module named e2b")
    monkeypatch.setattr(e2b_provider, "_sdk", _missing)
    ok, reason = _provider().is_available()
    assert not ok and "not installed" in reason


def test_unavailable_without_a_key(fake_e2b, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "e2b_api_key", "")
    ok, reason = _provider().is_available()
    assert not ok and "E2B_API_KEY" in reason


def test_available_with_sdk_and_key(fake_e2b):
    ok, reason = _provider().is_available()
    assert ok and reason == ""


def test_key_falls_back_to_the_secrets_store(fake_e2b, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "e2b_api_key", "")
    monkeypatch.setattr("common.secrets.get_secret", lambda ws, name, **kw: "stored-key" if name == "E2B_API_KEY" else None)
    ok, reason = _provider().is_available()
    assert ok and reason == ""


# ── network mapping ──────────────────────────────────────────────────────────

def test_network_none_disables_internet_access(fake_e2b):
    result = _provider().run(SandboxRequest(language="python", code="print(1)"))
    assert result.exit_code == 0
    assert FakeSandbox.created[0].kwargs["allow_internet_access"] is False


def test_network_limited_sets_the_domain_allowlist(fake_e2b):
    result = _provider().run(SandboxRequest(
        language="python", code="print(1)",
        network=SandboxNetwork(type="limited", hosts=["pypi.org", "github.com"])))
    assert result.exit_code == 0
    net = FakeSandbox.created[0].kwargs["network"]
    assert net["allow_out"] == ["pypi.org", "github.com"]
    assert callable(net["deny_out"])


def test_network_limited_with_no_hosts_is_refused(fake_e2b):
    result = _provider().run(SandboxRequest(
        language="python", code="print(1)", network=SandboxNetwork(type="limited", hosts=[])))
    assert result.exit_code == -1 and "no allowed hosts" in result.error
    assert not FakeSandbox.created


def test_network_unrestricted_passes_no_network_kwarg(fake_e2b):
    _provider().run(SandboxRequest(
        language="python", code="print(1)", network=SandboxNetwork(type="unrestricted")))
    kwargs = FakeSandbox.created[0].kwargs
    assert "network" not in kwargs and "allow_internet_access" not in kwargs


# ── running, errors, cleanup ─────────────────────────────────────────────────

def test_writes_the_snippet_and_extra_files(fake_e2b):
    _provider().run(SandboxRequest(
        language="python", code="print(1)", extra_files={"helper.py": "x = 1"}))
    sb = FakeSandbox.created[0]
    assert sb.written["main.py"] == "print(1)"
    assert sb.written["helper.py"] == "x = 1"


def test_non_zero_exit_is_not_an_infrastructure_error(fake_e2b):
    def on_run(cmd, timeout):
        raise FakeCommandExitException(7, "partial\n", "bad input\n")

    def _create(**kwargs):
        sb = FakeSandbox(**kwargs)
        sb.on_run = on_run
        FakeSandbox.created.append(sb)
        return sb
    FakeSandbox.create = staticmethod(_create)

    result = _provider().run(SandboxRequest(language="python", code="raise SystemExit(7)"))
    assert result.exit_code == 7 and result.stdout == "partial\n" and not result.error
    assert FakeSandbox.killed  # always cleaned up


def test_command_timeout_is_reported(fake_e2b):
    def _create(**kwargs):
        sb = FakeSandbox(**kwargs)
        sb.on_run = lambda cmd, timeout: (_ for _ in ()).throw(FakeTimeoutException("slow"))
        FakeSandbox.created.append(sb)
        return sb
    FakeSandbox.create = staticmethod(_create)

    result = _provider().run(SandboxRequest(language="python", code="while True: pass", timeout=5))
    assert result.timed_out and "timed out after 5s" in result.error
    assert FakeSandbox.killed


def test_authentication_failure_is_reported(fake_e2b):
    original_create = FakeSandbox.create

    def _raise(**kwargs):
        raise FakeAuthenticationException("bad key")
    FakeSandbox.create = staticmethod(_raise)
    try:
        result = _provider().run(SandboxRequest(language="python", code="1"))
    finally:
        FakeSandbox.create = original_create
    assert result.exit_code == -1 and "authentication failed" in result.error


def test_workspace_mount_is_refused(fake_e2b, tmp_path):
    result = _provider().run(SandboxRequest(language="python", code="1", workspace=str(tmp_path)))
    assert result.exit_code == -1 and "mount_workspace" in result.error
    assert not FakeSandbox.created


def test_stdin_is_refused(fake_e2b):
    result = _provider().run(SandboxRequest(language="python", code="1", stdin="hi"))
    assert result.exit_code == -1 and "stdin" in result.error
    assert not FakeSandbox.created


def test_unsupported_language(fake_e2b):
    result = _provider().run(SandboxRequest(language="ruby", code="1"))
    assert result.exit_code == -1 and "unsupported language" in result.error


def test_sandbox_is_always_killed_even_on_run_failure(fake_e2b):
    def _create(**kwargs):
        sb = FakeSandbox(**kwargs)

        def _boom(cmd, timeout):
            raise RuntimeError("network blip")
        sb.on_run = _boom
        FakeSandbox.created.append(sb)
        return sb
    FakeSandbox.create = staticmethod(_create)

    result = _provider().run(SandboxRequest(language="python", code="1"))
    assert result.exit_code == -1 and "e2b run failed" in result.error
    assert FakeSandbox.killed

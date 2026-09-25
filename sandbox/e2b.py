"""The E2B sandbox: a remote, short-lived VM per run (e2b.dev).

Written against the current E2B Python SDK (2.x: ``Sandbox.create`` with
``allow_internet_access`` / ``network``, ``commands.run``, ``files.write``);
every call here was checked against the official docs at
https://docs.e2b.dev (sandbox creation, commands, filesystem, network) on
2026-09-25. This checkout has ``e2b`` 1.11.1 installed (an older API without
these parameters), so ``e2b`` stays an optional dependency an operator
upgrades to use this provider: ``pip install -U "e2b>=2"``, see
docs/sandboxes.md and requirements-sandbox.txt. The import is lazy so its
absence only affects :class:`E2BProvider`.

Network mapping. ``none``: ``allow_internet_access=False`` — the SDK's own
outbound cutoff, enforced by E2B, not a hint a client could ignore.
``limited``: E2B's own domain-based outbound allowlist
(``network={"allow_out": hosts, "deny_out": lambda ctx: [ctx.all_traffic]}``),
a host allowlist E2B enforces at the network layer — the docs show this
exists, so ``limited`` is honoured for real rather than refused; it only
covers TLS on port 443 (and plain HTTP on 80 is not passed), worth knowing
but not a reason to refuse, since almost everything a limited run would
reach is HTTPS. A ``limited`` request with no hosts is refused outright
(an empty allowlist that silently behaved like ``none`` would surprise
nobody sensible; refusing forces the caller to say what they meant).
``unrestricted``: no network argument at all, E2B's own default (open
outbound).

Credentials: ``E2B_API_KEY`` (settings, or the hub secrets store's global
scope, ``common/secrets.py``) and an optional ``E2B_TEMPLATE`` naming the
sandbox template/image; E2B's own default template otherwise.

Not supported: ``mount_workspace`` (no local filesystem to mount from — a
run that needs its workspace belongs on docker or local) and ``stdin`` (the
SDK's ``commands.run`` takes a boolean "enable a pipe" flag, not literal
input data, so there is no single call that reproduces
``subprocess.run(..., input=...)``). Both come back as a clear refusal
rather than a silent no-op.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from sandbox.base import LANGS, SandboxProvider, SandboxRequest, SandboxResult, truncate_output

log = logging.getLogger(__name__)


def _settings():
    from common.config import settings
    return settings


def _api_key() -> str:
    """``E2B_API_KEY`` from settings, else the hub secrets store's global
    (workspace-less) scope, else empty."""
    s = _settings()
    key = str(getattr(s, "e2b_api_key", "") or "").strip()
    if key:
        return key
    try:
        from common.secrets import get_secret
        return str(get_secret("", "E2B_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001 - no secret configured either; reads as missing
        return ""


def _template() -> Optional[str]:
    value = str(getattr(_settings(), "e2b_template", "") or "").strip()
    return value or None


def _max_output() -> int:
    from tools.shell import _MAX_OUTPUT
    return _MAX_OUTPUT


def _sdk():
    """The ``e2b`` module, imported lazily so its absence only affects this
    provider. Tests inject a fake module into ``sys.modules['e2b']``."""
    import e2b
    return e2b


class E2BProvider(SandboxProvider):
    name = "e2b"

    def is_available(self) -> Tuple[bool, str]:
        try:
            _sdk()
        except ImportError:
            return False, 'the e2b package is not installed (pip install "e2b>=2", see requirements-sandbox.txt)'
        if not _api_key():
            return False, "E2B_API_KEY is not set (settings or the hub secrets store)"
        return True, ""

    def run(self, request: SandboxRequest) -> SandboxResult:
        language = (request.language or "").strip().lower()
        if language not in LANGS:
            return SandboxResult(exit_code=-1, provider=self.name,
                                 error=f"unsupported language {language!r} (expected python, node or bash)")
        ok, reason = self.is_available()
        if not ok:
            return SandboxResult(exit_code=-1, provider=self.name, error=reason)
        if request.workspace:
            return SandboxResult(exit_code=-1, provider=self.name, error=(
                "mount_workspace is not supported on the e2b provider: it has no local filesystem to "
                "mount from. Choose docker or local for a snippet that needs to read the workspace."))
        if request.stdin is not None:
            return SandboxResult(exit_code=-1, provider=self.name, error=(
                "stdin is not supported on the e2b provider (its commands.run takes no literal input "
                "data). Choose docker or local for a snippet that reads stdin."))

        e2b = _sdk()
        net = request.network
        timeout = max(1, int(request.timeout or 60))
        create_kwargs: Dict[str, Any] = {
            "api_key": _api_key(),
            # The sandbox itself outlives the one command it runs, so it gets
            # a little headroom beyond the command's own timeout.
            "timeout": timeout + 10,
        }
        template = _template()
        if template:
            create_kwargs["template"] = template
        if net is None or net.type == "none":
            create_kwargs["allow_internet_access"] = False
        elif net.type == "limited":
            if not net.hosts:
                return SandboxResult(exit_code=-1, provider=self.name, error=(
                    "a limited network with no allowed hosts would refuse everything; add at least "
                    "one host, or use network none"))
            create_kwargs["network"] = {
                "allow_out": list(net.hosts),
                "deny_out": lambda ctx: [ctx.all_traffic],
            }
        # unrestricted: no network argument, e2b's own default (open outbound).

        started = time.monotonic()
        try:
            sandbox = e2b.Sandbox.create(**create_kwargs)
        except e2b.AuthenticationException as exc:
            return SandboxResult(exit_code=-1, provider=self.name, error=f"e2b authentication failed: {exc}")
        except e2b.TimeoutException as exc:
            return SandboxResult(exit_code=-1, provider=self.name, timed_out=True,
                                 error=f"e2b sandbox did not start in time: {exc}")
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            return SandboxResult(exit_code=-1, provider=self.name, error=f"e2b sandbox creation failed: {exc}")

        try:
            file_name, interpreter, _ = LANGS[language]
            files = {file_name: request.code or "", **(request.extra_files or {})}
            for rel, content in files.items():
                sandbox.files.write(rel, content)
            try:
                result = sandbox.commands.run(f"{interpreter} {file_name}", timeout=timeout)
                exit_code, stdout, stderr = result.exit_code, result.stdout, result.stderr
            except e2b.CommandExitException as exc:
                # Non-zero exit: not an infrastructure failure, the snippet's own business.
                exit_code, stdout, stderr = exc.exit_code, exc.stdout, exc.stderr
            except e2b.TimeoutException as exc:
                return SandboxResult(exit_code=-1, provider=self.name, timed_out=True,
                                     duration_ms=int((time.monotonic() - started) * 1000),
                                     error=f"timed out after {timeout}s: {exc}")
            out, out_trunc = truncate_output(stdout or "", _max_output())
            err, err_trunc = truncate_output(stderr or "", _max_output())
            return SandboxResult(exit_code=int(exit_code), stdout=out, stderr=err,
                                 truncated_stdout=out_trunc, truncated_stderr=err_trunc,
                                 duration_ms=int((time.monotonic() - started) * 1000), provider=self.name)
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            return SandboxResult(exit_code=-1, provider=self.name, error=f"e2b run failed: {exc}")
        finally:
            # Always killed, success or failure: E2B bills for sandbox
            # lifetime, and a leaked one keeps running until its own timeout.
            try:
                sandbox.kill()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                log.warning("could not kill e2b sandbox", exc_info=True)


__all__ = ["E2BProvider"]

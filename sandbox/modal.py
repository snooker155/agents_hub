"""The Modal sandbox: a remote container per run (modal.com).

Written against the official ``modal`` Python SDK (not installed in this
checkout; an operator adds it, ``pip install modal``, see
docs/sandboxes.md and requirements-sandbox.txt). Every call here was
checked against https://modal.com/docs on 2026-09-25:
``modal.App.lookup(name, create_if_missing=True)``, ``modal.Sandbox.create``
(``image``, ``timeout``, ``block_network``, ``outbound_domain_allowlist``),
``sandbox.filesystem.write_text``, ``sandbox.exec`` / ``process.wait()`` /
``.returncode`` / ``.stdout``/``.stderr``, ``sandbox.terminate``.

Network mapping. ``none``: ``block_network=True`` — Modal drops all
outbound traffic. ``limited``: ``outbound_domain_allowlist`` with the
request's hosts, Modal's own domain-based allowlist (TLS on port 443 only;
plain HTTP is not passed — the same limitation E2B's domain filter has, see
sandbox/e2b.py). Modal also offers an IP/CIDR allowlist
(``outbound_cidr_allowlist``); the request's hosts are domain names, so the
domain allowlist is used directly instead of resolving them to IP addresses
once at sandbox-creation time, which would go stale the moment the target's
DNS changes (a CDN-fronted API can rotate IPs at any time). A wildcard
(``*.example.com``) is added alongside every bare host so a normalized
allowlist entry (which the hub treats as covering its own subdomains, see
environments/models.py ``NetworkPolicy``) covers them here too.
``unrestricted``: no network argument, Modal's own default (open outbound).

Credentials: ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` (settings, or the
hub secrets store's global scope). Modal's client reads them from the
process environment, so they are exported into ``os.environ`` before the
client is touched — the same pattern ``common/config.py`` uses for the model
providers' own keys.

Not supported, same as sandbox/e2b.py and for the same reasons:
``mount_workspace`` (no local filesystem to mount from) and ``stdin``.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from sandbox.base import LANGS, SandboxProvider, SandboxRequest, SandboxResult, truncate_output

log = logging.getLogger(__name__)

#: One Modal App this provider's sandboxes all belong to; looked up (and
#: created once) rather than named per run, since a run-scoped app would
#: leave one behind for every snippet ever executed.
_APP_NAME = "agents-hub-run-code"
_WORKDIR = "/sandbox"


def _settings():
    from common.config import settings
    return settings


def _secret(setting_name: str, env_name: str, secret_name: str) -> str:
    value = str(getattr(_settings(), setting_name, "") or os.environ.get(env_name, "")).strip()
    if value:
        return value
    try:
        from common.secrets import get_secret
        return str(get_secret("", secret_name) or "").strip()
    except Exception:  # noqa: BLE001 - no secret configured either; reads as missing
        return ""


def _export_credentials() -> bool:
    """True when both Modal tokens are known, after exporting them into the
    process environment (the modal client reads them from there)."""
    token_id = _secret("modal_token_id", "MODAL_TOKEN_ID", "MODAL_TOKEN_ID")
    token_secret = _secret("modal_token_secret", "MODAL_TOKEN_SECRET", "MODAL_TOKEN_SECRET")
    if not (token_id and token_secret):
        return False
    os.environ["MODAL_TOKEN_ID"] = token_id
    os.environ["MODAL_TOKEN_SECRET"] = token_secret
    return True


def _image_name() -> Optional[str]:
    value = str(getattr(_settings(), "modal_image", "") or "").strip()
    return value or None


def _max_output() -> int:
    from tools.shell import _MAX_OUTPUT
    return _MAX_OUTPUT


def _sdk():
    """The ``modal`` module, imported lazily so its absence only affects this
    provider. Tests inject a fake module into ``sys.modules['modal']``."""
    import modal
    return modal


def _allowlist(hosts: List[str]) -> List[str]:
    out: List[str] = []
    for host in hosts:
        if host not in out:
            out.append(host)
        wildcard = f"*.{host}"
        if wildcard not in out:
            out.append(wildcard)
    return out


class ModalProvider(SandboxProvider):
    name = "modal"

    def is_available(self) -> Tuple[bool, str]:
        try:
            _sdk()
        except ImportError:
            return False, "the modal package is not installed (pip install modal, see requirements-sandbox.txt)"
        if not _export_credentials():
            return False, "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET are not set (settings or the hub secrets store)"
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
                "mount_workspace is not supported on the modal provider: it has no local filesystem to "
                "mount from. Choose docker or local for a snippet that needs to read the workspace."))
        if request.stdin is not None:
            return SandboxResult(exit_code=-1, provider=self.name, error=(
                "stdin is not supported on the modal provider. Choose docker or local for a snippet "
                "that reads stdin."))

        modal = _sdk()
        net = request.network
        timeout = max(1, int(request.timeout or 60))
        sandbox_kwargs: Dict[str, Any] = {"timeout": timeout + 10}
        try:
            image_name = _image_name()
            sandbox_kwargs["image"] = (modal.Image.from_registry(image_name) if image_name
                                       else modal.Image.debian_slim())
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            return SandboxResult(exit_code=-1, provider=self.name, error=f"modal image resolution failed: {exc}")

        if net is None or net.type == "none":
            sandbox_kwargs["block_network"] = True
        elif net.type == "limited":
            if not net.hosts:
                return SandboxResult(exit_code=-1, provider=self.name, error=(
                    "a limited network with no allowed hosts would refuse everything; add at least "
                    "one host, or use network none"))
            sandbox_kwargs["outbound_domain_allowlist"] = _allowlist(net.hosts)
        # unrestricted: no network argument, modal's own default (open outbound).

        started = time.monotonic()
        sandbox = None
        try:
            app = modal.App.lookup(_APP_NAME, create_if_missing=True)
            sandbox = modal.Sandbox.create(app=app, **sandbox_kwargs)
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            return SandboxResult(exit_code=-1, provider=self.name, error=f"modal sandbox creation failed: {exc}")

        try:
            file_name, interpreter, _ = LANGS[language]
            files = {file_name: request.code or "", **(request.extra_files or {})}
            for rel, content in files.items():
                sandbox.filesystem.write_text(content, f"{_WORKDIR}/{rel}")
            process = sandbox.exec(interpreter, f"{_WORKDIR}/{file_name}", timeout=timeout,
                                   workdir=_WORKDIR)
            process.wait()
            exit_code = process.returncode
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            out, out_trunc = truncate_output(stdout or "", _max_output())
            err, err_trunc = truncate_output(stderr or "", _max_output())
            return SandboxResult(exit_code=int(exit_code), stdout=out, stderr=err,
                                 truncated_stdout=out_trunc, truncated_stderr=err_trunc,
                                 duration_ms=int((time.monotonic() - started) * 1000), provider=self.name)
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            duration = int((time.monotonic() - started) * 1000)
            timed_out = duration >= timeout * 1000
            return SandboxResult(exit_code=-1, provider=self.name, duration_ms=duration,
                                 timed_out=timed_out, error=f"modal run failed: {exc}")
        finally:
            # Always terminated, success or failure: a leaked sandbox keeps
            # billing until its own timeout expires.
            if sandbox is not None:
                try:
                    sandbox.terminate()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    log.warning("could not terminate modal sandbox", exc_info=True)


__all__ = ["ModalProvider"]

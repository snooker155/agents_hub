"""Picking a sandbox provider by name.

Four providers, one instance each (stateless beyond a docker-availability
cache and, for e2b/modal, nothing at all): ``docker``, ``local``, ``e2b``,
``modal``. :func:`get_provider` returns the instance; :func:`available`
reports whether each one can actually run something right now, and why not
when it cannot (used by the doctor check and the Environments page's
provider picker); :func:`resolve` is the precedence a run's provider comes
from.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from sandbox.base import SandboxProvider

if TYPE_CHECKING:
    from environments.models import Environment

PROVIDER_NAMES = ("docker", "local", "e2b", "modal")

_INSTANCES: Dict[str, SandboxProvider] = {}


def get_provider(name: str) -> SandboxProvider:
    """The provider instance for ``name``. Raises :class:`KeyError` for
    anything outside :data:`PROVIDER_NAMES` — callers resolve a name through
    :func:`resolve` first, which never returns anything else."""
    name = (name or "").strip().lower()
    if name not in _INSTANCES:
        if name == "docker":
            from sandbox.docker import DockerProvider
            _INSTANCES[name] = DockerProvider()
        elif name == "local":
            from sandbox.local import LocalProvider
            _INSTANCES[name] = LocalProvider()
        elif name == "e2b":
            from sandbox.e2b import E2BProvider
            _INSTANCES[name] = E2BProvider()
        elif name == "modal":
            from sandbox.modal import ModalProvider
            _INSTANCES[name] = ModalProvider()
        else:
            raise KeyError(f"no sandbox provider named {name!r} (expected one of {PROVIDER_NAMES})")
    return _INSTANCES[name]


def available() -> Dict[str, Dict[str, object]]:
    """Every provider's availability: ``{name: {"available": bool, "reason": str}}``.
    Never raises — a provider whose own check blows up counts as unavailable
    with the exception as its reason, exactly like the doctor's own checks."""
    out: Dict[str, Dict[str, object]] = {}
    for name in PROVIDER_NAMES:
        try:
            ok, reason = get_provider(name).is_available()
        except Exception as exc:  # noqa: BLE001 - a broken provider reads as unavailable
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        out[name] = {"available": ok, "reason": "" if ok else reason}
    return out


def resolve(environment: Optional["Environment"], settings=None) -> str:
    """The provider name a request should use.

    Precedence: the run's environment, when it names one explicitly (its
    ``sandbox_provider`` is anything but ``inherit``); else
    ``CODE_RUNNER_PROVIDER`` (default ``docker``); else, when that provider
    cannot run right now and the operator has opted into the historical
    local fallback (``CODE_RUNNER_FALLBACK=local``), ``local`` — the same
    rule ``run_code`` always applied when docker was unavailable, now
    expressed as a provider choice instead of a hardcoded branch.

    Availability is not otherwise considered here: a name this function
    returns may still fail ``is_available()`` (a key missing, docker down),
    and the caller surfaces that as the run's error, the same as calling any
    other unavailable provider by name.
    """
    if environment is not None:
        choice = str(getattr(environment, "sandbox_provider", "") or "inherit").strip().lower()
        if choice and choice != "inherit":
            return choice
    live_settings = settings
    if live_settings is None:
        from common.config import settings as live_settings
    chosen = str(getattr(live_settings, "code_runner_provider", "") or "docker").strip().lower() or "docker"
    fallback = str(getattr(live_settings, "code_runner_fallback", "") or "").strip().lower()
    if chosen == "docker" and fallback == "local":
        ok, _ = get_provider("docker").is_available()
        if not ok:
            return "local"
    return chosen


__all__ = ["PROVIDER_NAMES", "get_provider", "available", "resolve"]

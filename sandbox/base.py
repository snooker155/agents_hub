"""The sandbox interface every provider implements.

A sandbox runs one snippet of code to completion and reports what happened:
nothing here is provider-specific (no docker command line, no SDK object).
``SandboxRequest`` is what a caller asks for; ``SandboxResult`` is what came
back; ``SandboxProvider`` is the seam between them. ``sandbox/registry.py``
picks a provider by name; ``sandbox/docker.py``, ``local.py``, ``e2b.py`` and
``modal.py`` are the implementations.

The network policy on a request is the one part every provider must honour or
refuse, never silently narrow or widen: ``none`` gives the snippet no network
at all, ``limited`` gives it only ``hosts``, ``unrestricted`` gives it the
provider's normal outbound access. A provider that cannot enforce ``limited``
(no allowlist mechanism, or the mechanism it has cannot be reached right now)
must return an error result, not run the snippet wide open — see each
provider's module docstring for how it enforces the policy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

NetworkType = Literal["none", "limited", "unrestricted"]

#: language -> (file name inside the sandbox, interpreter command, local
#: interpreter candidates in PATH order). Shared by every provider so a
#: language is defined once: docker.py uses the file name and interpreter,
#: local.py the candidates too, e2b.py/modal.py the file name and interpreter.
LANGS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "python": ("main.py", "python", ("python3", "python")),
    "node": ("main.js", "node", ("node",)),
    "bash": ("main.sh", "bash", ("bash",)),
}

DEFAULT_IMAGES: Dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-slim",
    "bash": "bash:5",
}


@dataclass
class SandboxNetwork:
    """What a run may reach. ``hosts`` only matters when ``type`` is
    ``limited``: bare hostnames (as environments/models.py normalizes them),
    covering their subdomains the same way the egress proxy does."""
    type: NetworkType = "none"
    hosts: List[str] = field(default_factory=list)


@dataclass
class SandboxRequest:
    """One snippet to run. ``extra_files`` are written alongside the main
    file (relative path -> content) for a snippet that spans more than one
    file; empty for the ``run_code`` tool today, which only ever runs one.
    ``workspace`` is a host directory to mount read-only at ``/work``, when
    set; not every provider can honour it (a remote sandbox has no local
    filesystem to mount from), in which case the provider refuses rather than
    silently dropping it. ``environment_id`` is only used to scope things a
    provider needs an identity for (an egress token, a derived image cache
    key); providers that need nothing of the sort ignore it.
    """
    language: str
    code: str
    extra_files: Dict[str, str] = field(default_factory=dict)
    timeout: int = 60
    stdin: Optional[str] = None
    memory: Optional[str] = None
    cpus: Optional[str] = None
    pids_limit: Optional[int] = None
    network: SandboxNetwork = field(default_factory=SandboxNetwork)
    workspace: Optional[str] = None
    environment_id: Optional[str] = None
    image: Optional[str] = None


@dataclass
class SandboxResult:
    """What running a request produced. ``ok`` is the convenience a caller
    actually wants: exit 0 and no infrastructure error (a non-zero exit from
    the snippet itself is not an ``error`` — that is the snippet's own
    business, ``exit_code`` says so)."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    duration_ms: int = 0
    timed_out: bool = False
    provider: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.error


def truncate_output(text: str, limit: int) -> "tuple[str, bool]":
    """``text`` cut to ``limit`` characters, and whether it was cut. Every
    provider applies the same limit (``tools.shell._MAX_OUTPUT``) so a
    snippet's output reads the same regardless of where it ran."""
    text = text or ""
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]", True
    return text, False


class SandboxProvider(ABC):
    """One way to run a :class:`SandboxRequest`. ``name`` is the id used in
    settings, the environment's ``sandbox_provider`` field and doctor's
    report (``docker``, ``local``, ``e2b``, ``modal``)."""

    name: str = ""

    @abstractmethod
    def is_available(self) -> "tuple[bool, str]":
        """(True, "") when this provider can run something right now;
        otherwise (False, a one-sentence reason: daemon down, SDK missing,
        key missing)."""

    @abstractmethod
    def run(self, request: SandboxRequest) -> SandboxResult:
        """Run ``request`` to completion. Never raises: any failure (the
        provider unavailable, the network policy unenforceable, a timeout, a
        crashed interpreter) comes back as a result with ``error`` set."""


__all__ = ["NetworkType", "SandboxNetwork", "SandboxRequest", "SandboxResult", "SandboxProvider",
           "LANGS", "DEFAULT_IMAGES", "truncate_output"]

"""Sandbox provider abstraction: run one snippet of code to completion,
somewhere isolated, and report what happened.

``base.py`` is the interface (``SandboxRequest``, ``SandboxResult``,
``SandboxProvider``); ``registry.py`` picks a provider by name;
``docker.py``, ``local.py``, ``e2b.py`` and ``modal.py`` are the
implementations. See docs/sandboxes.md.

Nothing is imported eagerly here beyond the interface: a provider's own SDK
(``e2b``, ``modal``) is only imported when that provider is actually asked
for, so their absence never breaks anything that does not use them.
"""
from __future__ import annotations

from sandbox.base import SandboxNetwork, SandboxRequest, SandboxResult

__all__ = ["SandboxNetwork", "SandboxRequest", "SandboxResult"]

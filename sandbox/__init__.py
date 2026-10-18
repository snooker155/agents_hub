"""Sandbox provider abstraction: run one snippet of code to completion,
somewhere isolated, and report what happened.

``base.py`` is the interface (``SandboxRequest``, ``SandboxResult``,
``SandboxProvider``); ``registry.py`` picks a provider by name;
``docker.py`` and ``local.py`` are the implementations. See docs/sandboxes.md.

Nothing is imported eagerly here beyond the interface: a provider is only
imported when it is actually asked for.
"""
from __future__ import annotations

from sandbox.base import SandboxNetwork, SandboxRequest, SandboxResult

__all__ = ["SandboxNetwork", "SandboxRequest", "SandboxResult"]

"""
Which edition is installed: the core alone, or the core with the
enterprise directory ``ee/`` (single sign-on and SCIM).

The core never imports ``ee`` unconditionally. The backend mounts the
enterprise routes only when the package is there, and the features the
frontend is told about (``identity.mode_features``) stay off without it, so a tree
with ``ee/`` deleted is a complete product rather than a broken one.
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache


@lru_cache(maxsize=1)
def enterprise_available() -> bool:
    """Whether the ``ee`` package can be imported from this checkout."""
    try:
        return importlib.util.find_spec("ee") is not None
    except (ImportError, ValueError):
        return False


def edition() -> str:
    """``enterprise`` with ``ee/`` present, else ``community``."""
    return "enterprise" if enterprise_available() else "community"


__all__ = ["enterprise_available", "edition"]

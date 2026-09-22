"""The one error shape the project services raise.

A plain ``ValueError`` is for "this input makes no sense" (caught wherever the
caller already handles it); ``ServiceError`` is for "this failed, and here is
the HTTP status the route should answer with" — the services know the status,
the routes only translate it into ``HTTPException``.
"""
from __future__ import annotations


class ServiceError(Exception):
    """Raised by a project service function. ``status`` is the HTTP status the
    calling route should respond with; ``detail`` is the message."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


__all__ = ["ServiceError"]

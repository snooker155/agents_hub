"""Support bundle and SLO status (docs/runbook.md, docs/slo.md).

``GET /api/support/bundle`` is the one route this module guards with an
explicit admin check rather than leaving it to ``common.identity.require_role``
alone: a bundle is the whole config and the last N errors in one download, so
the same bar as the Settings page's secret fields applies, and outside
``multi`` mode (where there is nobody to guard against) every request is
already the operator.

``GET /api/support/slo`` is read-only and cheap (a couple of aggregate
queries, see ``common/slo.py``); no admin check beyond the ordinary request
guard, so the Health page's SLO card works for any signed-in user the way the
rest of the health snapshot does.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from common import audit, identity, support_bundle
from common.slo import evaluate as evaluate_slo

router = APIRouter(prefix="/api/support", tags=["support"])


def _principal(request: Request):
    return identity.request_principal(request)


@router.get("/slo")
async def slo(request: Request, window_seconds: float = 3600.0):
    """The two SLO objectives (run start p95, error rate) over the rolling
    window. Never raises: a section that cannot be computed comes back
    ``no_data`` rather than a 500 (common/slo.py)."""
    return evaluate_slo(window_seconds)


@router.get("/bundle")
async def bundle(request: Request, since: float = support_bundle.DEFAULT_SINCE_SECONDS,
                 error_limit: int = support_bundle.DEFAULT_ERROR_LIMIT):
    """The support bundle as a zip download. Admin only: it carries the
    effective config (secrets reduced to "is it set") and the last errors,
    which is exactly the bar ``routes/settings.py`` holds its own secret
    fields to."""
    principal = _principal(request)
    identity.require_role(principal, admin=True)
    from starlette.concurrency import run_in_threadpool

    data = await run_in_threadpool(support_bundle.build, since_seconds=since, error_limit=error_limit)
    audit.record("support.bundle_download", principal=principal, object_type="support_bundle",
                 details={"since_seconds": since})
    filename = support_bundle.default_filename()
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=data, media_type="application/zip", headers=headers)


__all__ = ["router"]

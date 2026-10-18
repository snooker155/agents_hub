"""
``GET /api/system/version``: which release this backend is and the newest
schema migration it knows (docs/deployment.md "Rolling back"). ``ah version``
reads the same thing.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/version")
async def system_version():
    """Which release this backend is (common/version.py) and the newest schema
    migration it knows, which is what decides whether an older release can
    still open its database (docs/deployment.md "Rolling back")."""
    from common import migrations, db
    from common.version import info
    return {**info(), "schema_latest": migrations.latest_version(db.dialect())}

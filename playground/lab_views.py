"""
Publishing an environment's views as the run goes.

An environment may declare ``views()`` (playground/environments/base.py): the
lab publishes a results table, a chart, its formulas and the report. This
module is the one place a scenario run turns those into rows in the views
store, owned by the scenario run (``owner {kind: "scenario", id: sim_run_id}``)
so the run page and the Views gallery both find them.

Generic on purpose: nothing here knows about the lab. Any environment with a
non empty ``views()`` gets the same treatment. A key seen for the first time
creates a view; a key seen again updates that view's spec in place
(``views.store.update_spec``), so a run ends with one results table, not one
per tick. The key to view id mapping lives in the run's checkpoint (the
runner passes it in and saves it back), which is what lets a resumed run keep
updating the views it already made.

Never fatal: a view that cannot be written is logged and skipped. A lab whose
chart spec is wrong still finishes its science.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def _summary(item: Dict[str, Any]) -> str:
    """A one line text fallback so a view is never blank on a text surface."""
    kind = str(item.get("kind") or "")
    spec = item.get("spec") or {}
    if kind == "table":
        return f"{len(spec.get('rows') or [])} row(s)"
    if kind == "document":
        return str(spec.get("title") or item.get("title") or "document")[:200]
    return str(item.get("title") or kind)[:200]


def sync_env_views(env: Any, run: Any, view_ids: Optional[Dict[str, str]] = None,
                   last_specs: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Create or update one view per key ``env.views()`` returns.

    ``view_ids`` is the mapping so far (key to view id), updated in place and
    returned. ``last_specs`` (key to the JSON of the spec last written) lets a
    tick that changed nothing skip the write; it is an in process cache only.
    """
    view_ids = view_ids if view_ids is not None else {}
    try:
        items = env.views() if hasattr(env, "views") else []
    except Exception:  # noqa: BLE001 - a broken views() must not end the run
        log.exception("environment views() failed")
        return view_ids
    if not items:
        return view_ids

    from views import store as vstore

    owner = {"kind": "scenario", "id": run.sim_run_id}
    for item in items:
        key = str(item.get("key") or "")
        kind = str(item.get("kind") or "")
        spec = item.get("spec") or {}
        if not key or not kind:
            continue
        title = str(item.get("title") or key)
        try:
            encoded = json.dumps(spec, sort_keys=True, default=str)
        except (TypeError, ValueError):
            encoded = ""
        if last_specs is not None and encoded and last_specs.get(key) == encoded \
                and key in view_ids:
            continue
        try:
            existing = view_ids.get(key)
            if existing and vstore.update_spec(existing, spec, title=title,
                                               summary=_summary(item)):
                pass
            else:
                env_view = vstore.create_view(
                    kind, title, spec, workspace=run.workspace,
                    summary=_summary(item), owner=owner,
                )
                view_ids[key] = env_view.view_id
            if last_specs is not None and encoded:
                last_specs[key] = encoded
        except Exception:  # noqa: BLE001 - never fatal, see module docstring
            log.exception("could not publish view %s for sim %s", key, run.sim_run_id)
    return view_ids


__all__ = ["sync_env_views"]

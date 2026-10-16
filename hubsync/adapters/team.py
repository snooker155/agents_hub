"""
Teams for the sync engine: a roster, a charter and the terms a team stops on.

A team row has a ``workspace`` column, and ``list_teams`` also returns the
teams with none (shared by every workspace). Those belong to no one workspace,
so only the teams that name this one are listed. The workspace is left out of
the document and set on apply: the same team lands in whichever workspace the
engine is filling. ``updated_at`` is stamped by ``save_team`` on every save,
which is why it is volatile. Members name agents by id; agents sync first.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register


class TeamAdapter(Adapter):
    kind = "team"
    order = 32
    label = "Teams"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from teams import store
        return sorted(t.team_id for t in store.list_teams(workspace) if t.workspace == workspace)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from teams import store
        team = store.get_team(record_id)
        if team is None:
            return None
        doc = team.to_dict()
        doc.pop("workspace", None)
        doc.pop("team_id", None)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from teams import store
        from teams.models import Team
        prev = store.get_team(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"team {record_id} lives in another workspace here")
        try:
            team = Team.from_dict({**doc, "team_id": record_id, "workspace": workspace})
        except (TypeError, ValueError, KeyError) as exc:
            raise AdapterError(f"unreadable team: {exc}") from exc
        store.save_team(team)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from teams import store
        store.delete_team(record_id)


register(TeamAdapter())

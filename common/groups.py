"""
Groups, and the rules that turn a group into access.

A group is an entity of its own so that both single sign-on (the ``groups``
claim of an id token, ``common/oidc.py``) and SCIM provisioning
(``dashboard/backend/routes/scim.py``) can say "this person is in these
groups" and let one piece of code decide what that means. What it means is
the ``group_mappings`` table: a rule maps a group name either to a global
role (``target = "role"``: admin or member) or to a membership role in one
workspace (``target = "workspace"``: viewer, editor or owner).

:func:`apply_mappings` recomputes what a user's groups grant them and is
called on every login and every SCIM change. It only ever touches what it
granted itself: a workspace membership an owner added by hand carries
``source = manual`` and is left alone, and a global role set by hand
(``users.role_source = manual``) is never demoted by the absence of a
group. A role that came from a group (``role_source = group``) follows the
groups, so leaving the admins group in the provider takes admin away here
on the next login.

Nothing here checks the auth mode: the tables are inert unless something
writes to them, and only ``multi`` mode ever does.
"""
from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from common import db
from common.auth import (
    GLOBAL_ROLES, ROLE_ADMIN, ROLE_MEMBER, WORKSPACE_ROLES, WS_OWNER, WS_EDITOR,
    WS_VIEWER,
)

TARGET_ROLE = "role"
TARGET_WORKSPACE = "workspace"
TARGETS = (TARGET_ROLE, TARGET_WORKSPACE)

SOURCE_MANUAL = "manual"
SOURCE_GROUP = "group"

_WS_RANK = {WS_VIEWER: 1, WS_EDITOR: 2, WS_OWNER: 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(name: str) -> str:
    """Group names compare case-insensitively and without surrounding space;
    a Keycloak path (``/staff/devs``) keeps its slashes."""
    return (name or "").strip().lower()


# ── groups ───────────────────────────────────────────────────────────────────

def _row_to_group(row) -> Dict[str, Any]:
    return {
        "id": row["group_id"], "name": row["name"],
        "display_name": row["display_name"] or row["name"],
        "source": row["source"] or SOURCE_MANUAL, "external_id": row["external_id"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_groups() -> List[Dict[str, Any]]:
    rows = db.get_conn().execute("SELECT * FROM groups ORDER BY name").fetchall()
    out = [_row_to_group(r) for r in rows]
    counts = {r["group_id"]: int(r["n"]) for r in db.get_conn().execute(
        "SELECT group_id, COUNT(*) AS n FROM group_members GROUP BY group_id").fetchall()}
    for item in out:
        item["member_count"] = counts.get(item["id"], 0)
    return out


def get_group(group_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM groups WHERE group_id = ?",
                                (str(group_id),)).fetchone()
    return _row_to_group(row) if row else None


def get_group_by_name(name: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM groups WHERE name = ?",
                                (_norm(name),)).fetchone()
    return _row_to_group(row) if row else None


def get_group_by_external_id(external_id: str) -> Optional[Dict[str, Any]]:
    if not external_id:
        return None
    row = db.get_conn().execute("SELECT * FROM groups WHERE external_id = ?",
                                (str(external_id),)).fetchone()
    return _row_to_group(row) if row else None


def ensure_group(name: str, *, display_name: str = "", source: str = SOURCE_MANUAL,
                 external_id: Optional[str] = None) -> Dict[str, Any]:
    """The group called ``name``, created if it did not exist."""
    key = _norm(name)
    if not key:
        raise ValueError("group name is required")
    existing = get_group_by_name(key)
    if existing:
        changes: Dict[str, Any] = {}
        if display_name and display_name != existing["display_name"]:
            changes["display_name"] = display_name
        if external_id and external_id != existing["external_id"]:
            changes["external_id"] = external_id
        if changes:
            return update_group(existing["id"], **changes) or existing
        return existing
    now = _now()
    group_id = _secrets.token_hex(8)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO groups (group_id, name, display_name, source, external_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, key, (display_name or name).strip(), source, external_id, now, now))
    return get_group(group_id)  # type: ignore[return-value]


def update_group(group_id: str, *, display_name: Optional[str] = None,
                 name: Optional[str] = None,
                 external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    group = get_group(group_id)
    if group is None:
        return None
    new_name = _norm(name) if name is not None else group["name"]
    if name is not None and not new_name:
        raise ValueError("group name is required")
    with db.transaction() as conn:
        if new_name != group["name"]:
            taken = conn.execute("SELECT 1 FROM groups WHERE name = ? AND group_id <> ?",
                                 (new_name, str(group_id))).fetchone()
            if taken:
                raise ValueError(f"a group called '{new_name}' already exists")
        conn.execute(
            "UPDATE groups SET name = ?, display_name = ?, external_id = ?, updated_at = ? "
            "WHERE group_id = ?",
            (new_name,
             display_name.strip() if display_name is not None else group["display_name"],
             external_id if external_id is not None else group["external_id"],
             _now(), str(group_id)))
    return get_group(group_id)


def delete_group(group_id: str) -> bool:
    """Remove a group and its memberships; what its mappings granted is
    recomputed for every former member."""
    group = get_group(group_id)
    if group is None:
        return False
    members = group_member_ids(group_id)
    with db.transaction() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ?", (str(group_id),))
        conn.execute("DELETE FROM groups WHERE group_id = ?", (str(group_id),))
    for user_id in members:
        apply_mappings(user_id)
    return True


# ── membership of groups ─────────────────────────────────────────────────────

def group_member_ids(group_id: str) -> List[str]:
    rows = db.get_conn().execute(
        "SELECT user_id FROM group_members WHERE group_id = ? ORDER BY user_id",
        (str(group_id),)).fetchall()
    return [str(r["user_id"]) for r in rows]


def groups_of_user(user_id: str) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT g.* FROM groups g JOIN group_members m ON m.group_id = g.group_id "
        "WHERE m.user_id = ? ORDER BY g.name", (str(user_id),)).fetchall()
    return [_row_to_group(r) for r in rows]


def group_names_of_user(user_id: str) -> List[str]:
    return [g["name"] for g in groups_of_user(user_id)]


def set_group_members(group_id: str, user_ids: Iterable[str]) -> None:
    """Replace a group's member list (SCIM PUT / PATCH on a Group)."""
    wanted = {str(u) for u in user_ids if u}
    before = set(group_member_ids(group_id))
    now = _now()
    with db.transaction() as conn:
        for user_id in before - wanted:
            conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                         (str(group_id), user_id))
        for user_id in wanted - before:
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, created_at) VALUES (?, ?, ?)",
                (str(group_id), user_id, now))
    for user_id in before ^ wanted:
        apply_mappings(user_id)


def add_group_member(group_id: str, user_id: str) -> None:
    if str(user_id) in group_member_ids(group_id):
        return
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO group_members (group_id, user_id, created_at) VALUES (?, ?, ?)",
            (str(group_id), str(user_id), _now()))
    apply_mappings(user_id)


def remove_group_member(group_id: str, user_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
            (str(group_id), str(user_id)))
    if cursor.rowcount:
        apply_mappings(user_id)
    return bool(cursor.rowcount)


def set_user_groups(user_id: str, names: Iterable[str], *, source: str = "oidc") -> List[str]:
    """Replace the groups a user is in with these names, creating unknown
    groups on the way (a provider is the source of truth for its groups).
    Returns the normalised names. Recomputes what the groups grant."""
    wanted: Set[str] = {_norm(n) for n in names if _norm(n)}
    ids: Dict[str, str] = {}
    for name in wanted:
        ids[name] = ensure_group(name, source=source)["id"]
    current = {g["name"]: g["id"] for g in groups_of_user(user_id)}
    now = _now()
    with db.transaction() as conn:
        for name, group_id in current.items():
            if name not in wanted:
                conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                             (group_id, str(user_id)))
        for name in wanted - set(current):
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, created_at) VALUES (?, ?, ?)",
                (ids[name], str(user_id), now))
    apply_mappings(user_id)
    return sorted(wanted)


# ── mappings ─────────────────────────────────────────────────────────────────

def _row_to_mapping(row) -> Dict[str, Any]:
    return {
        "id": row["mapping_id"], "group_name": row["group_name"], "target": row["target"],
        "role": row["role"], "workspace": row["workspace"], "created_at": row["created_at"],
    }


def list_mappings(group_name: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    if group_name:
        rows = conn.execute(
            "SELECT * FROM group_mappings WHERE group_name = ? ORDER BY target, workspace",
            (_norm(group_name),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM group_mappings ORDER BY group_name, target, workspace").fetchall()
    return [_row_to_mapping(r) for r in rows]


def get_mapping(mapping_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute("SELECT * FROM group_mappings WHERE mapping_id = ?",
                                (str(mapping_id),)).fetchone()
    return _row_to_mapping(row) if row else None


def add_mapping(group_name: str, *, target: str, role: str,
                workspace: Optional[str] = None) -> Dict[str, Any]:
    """Add a rule. Validates the role against the target; one rule per
    (group, target, workspace) is replaced rather than duplicated."""
    name = _norm(group_name)
    if not name:
        raise ValueError("group name is required")
    if target not in TARGETS:
        raise ValueError(f"unknown mapping target '{target}'")
    if target == TARGET_ROLE:
        if role not in GLOBAL_ROLES:
            raise ValueError(f"unknown global role '{role}'")
        workspace = None
    else:
        if role not in WORKSPACE_ROLES:
            raise ValueError(f"unknown workspace role '{role}'")
        workspace = (workspace or "").strip()
        if not workspace:
            raise ValueError("a workspace mapping needs a workspace")
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM group_mappings WHERE group_name = ? AND target = ? "
            "AND COALESCE(workspace, '') = ?", (name, target, workspace or ""))
        mapping_id = _secrets.token_hex(8)
        conn.execute(
            "INSERT INTO group_mappings (mapping_id, group_name, target, role, workspace, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (mapping_id, name, target, role, workspace, now))
    _reapply_for_group(name)
    return get_mapping(mapping_id)  # type: ignore[return-value]


def delete_mapping(mapping_id: str) -> bool:
    mapping = get_mapping(mapping_id)
    if mapping is None:
        return False
    with db.transaction() as conn:
        conn.execute("DELETE FROM group_mappings WHERE mapping_id = ?", (str(mapping_id),))
    _reapply_for_group(mapping["group_name"])
    return True


def _reapply_for_group(group_name: str) -> None:
    group = get_group_by_name(group_name)
    if group is None:
        return
    for user_id in group_member_ids(group["id"]):
        apply_mappings(user_id)


# ── what the groups grant ────────────────────────────────────────────────────

def grants_for(group_names: Iterable[str]) -> Dict[str, Any]:
    """What a set of group names grants: ``{"role": admin|member|None,
    "workspaces": {name: role}}``, the strongest role winning per workspace."""
    names = [_norm(n) for n in group_names if _norm(n)]
    role: Optional[str] = None
    workspaces: Dict[str, str] = {}
    if not names:
        return {"role": None, "workspaces": {}}
    rows = db.get_conn().execute(
        "SELECT * FROM group_mappings WHERE group_name IN ("
        + ",".join("?" for _ in names) + ")", tuple(names)).fetchall()
    for row in rows:
        if row["target"] == TARGET_ROLE:
            if row["role"] == ROLE_ADMIN or role is None:
                role = row["role"]
        else:
            current = workspaces.get(row["workspace"])
            if current is None or _WS_RANK.get(row["role"], 0) > _WS_RANK.get(current, 0):
                workspaces[row["workspace"]] = row["role"]
    return {"role": role, "workspaces": workspaces}


def apply_mappings(user_id: str) -> Dict[str, Any]:
    """Recompute the global role and the workspace memberships this user's
    groups grant. Manual grants are never touched. Returns the grants."""
    from common import identity
    user = identity.get_user(user_id)
    if user is None:
        return {"role": None, "workspaces": {}}
    grants = grants_for(group_names_of_user(user_id))
    granted_role = grants["role"]

    # The global role: a group grant sets it (and marks it as group-sourced);
    # no grant leaves a manual role alone and returns a group-sourced one to
    # member. The last-admin rule in update_user still applies.
    if granted_role is not None:
        if user["role"] != granted_role or user.get("role_source") != SOURCE_GROUP:
            _set_role(user, granted_role, SOURCE_GROUP)
    elif user.get("role_source") == SOURCE_GROUP and user["role"] != ROLE_MEMBER:
        _set_role(user, ROLE_MEMBER, SOURCE_GROUP)

    # Workspace memberships: rows this module granted follow the groups; rows
    # an owner granted by hand stay, and a manual row is not downgraded by a
    # weaker group grant (an upgrade is applied and keeps the manual mark).
    rows = db.get_conn().execute(
        "SELECT workspace, role, source FROM workspace_members WHERE user_id = ?",
        (str(user_id),)).fetchall()
    existing = {r["workspace"]: (r["role"], r["source"] or SOURCE_MANUAL) for r in rows}
    wanted: Dict[str, str] = dict(grants["workspaces"])
    now = _now()
    with db.transaction() as conn:
        for workspace, (role, source) in existing.items():
            if source != SOURCE_GROUP:
                continue
            if workspace not in wanted:
                conn.execute(
                    "DELETE FROM workspace_members WHERE workspace = ? AND user_id = ?",
                    (workspace, str(user_id)))
        for workspace, role in wanted.items():
            current = existing.get(workspace)
            if current is None:
                conn.execute(
                    "INSERT INTO workspace_members (workspace, user_id, role, created_at, "
                    "source) VALUES (?, ?, ?, ?, ?)",
                    (workspace, str(user_id), role, now, SOURCE_GROUP))
            elif current[1] == SOURCE_GROUP and current[0] != role:
                conn.execute(
                    "UPDATE workspace_members SET role = ? WHERE workspace = ? AND user_id = ?",
                    (role, workspace, str(user_id)))
            elif current[1] != SOURCE_GROUP and _WS_RANK.get(role, 0) > _WS_RANK.get(current[0], 0):
                conn.execute(
                    "UPDATE workspace_members SET role = ? WHERE workspace = ? AND user_id = ?",
                    (role, workspace, str(user_id)))
    return grants


def _set_role(user: Dict[str, Any], role: str, source: str) -> None:
    from common import identity
    try:
        identity.update_user(user["id"], role=role, role_source=source)
    except ValueError:
        # The last admin: the group says member, the installation says no.
        pass


__all__ = [
    "SOURCE_GROUP", "SOURCE_MANUAL", "TARGET_ROLE", "TARGET_WORKSPACE", "TARGETS",
    "add_group_member", "add_mapping", "apply_mappings", "delete_group", "delete_mapping",
    "ensure_group", "get_group", "get_group_by_external_id", "get_group_by_name",
    "get_mapping", "grants_for", "group_member_ids", "group_names_of_user",
    "groups_of_user", "list_groups", "list_mappings", "remove_group_member",
    "set_group_members", "set_user_groups", "update_group",
]

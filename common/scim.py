"""
SCIM 2.0, the pure half: filter parsing, resource mapping, paging, errors.

An identity provider (Entra ID, Okta, Keycloak) drives ``/scim/v2``
(``dashboard/backend/routes/scim.py``) to keep this hub's users and groups in
step with its own directory. This module is everything about that protocol
that is a function of its arguments: turning a SCIM filter string into an
attribute and a value, turning a user or group row into the JSON shape RFC
7644 expects, wrapping a page of results in a ``ListResponse`` envelope, and
building the error body a SCIM client knows how to read. It touches no
database and imports no web framework, the same split ``common/auth.py``
makes for the browser-facing auth layer, so the protocol details are testable
without a server and the route module stays thin: look the resource up,
call into ``common.identity`` or ``common.groups`` to act, hand the result
here to shape the response.

Two things this module deliberately does not know: what the bearer token is
(the route checks ``settings.auth_scim_token`` itself, since a wrong guess
there must answer 401, not the generic auth failure the rest of the API
gives), and how a change gets audited (the route calls ``common.audit``,
because only it knows which action name a given change earned).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── schema identifiers ───────────────────────────────────────────────────────

SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_LIST_RESPONSE = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCHEMA_PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_SERVICE_PROVIDER_CONFIG = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCHEMA_RESOURCE_TYPE = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"

#: The ceiling on a page of results, advertised in ServiceProviderConfig and
#: enforced in :func:`clamp_count`. An identity provider syncing a directory
#: of any real size pages regardless; this just bounds one round trip.
MAX_RESULTS = 200
DEFAULT_COUNT = 100


class ScimError(ValueError):
    """A request the protocol itself rejects: a bad filter, a bad PATCH path
    or op. Carries the HTTP status and ``scimType`` the route answers with,
    so the route needs only ``except ScimError as exc: return _err(exc)``.
    """

    def __init__(self, detail: str, *, status: int = 400, scim_type: str = "invalidValue"):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.scim_type = scim_type


def error_body(status: int, detail: str, scim_type: Optional[str] = None) -> Dict[str, Any]:
    """The body of a SCIM error response (RFC 7644 §3.12)."""
    body: Dict[str, Any] = {"schemas": [SCHEMA_ERROR], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return body


# ── filters ──────────────────────────────────────────────────────────────────
#
# Only ``attr eq "value"`` is supported, which is the one form every
# provisioning client actually sends for the attributes it uses to look an
# account up before creating a duplicate. Anything else, including a boolean
# combination or another operator, is a 400 with ``scimType: invalidFilter``
# rather than a silent no-match: a client that gets an empty list back when
# its filter was simply not understood will happily provision a duplicate.

_FILTER_RE = re.compile(r'^\s*([A-Za-z0-9_.]+)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$',
                        re.IGNORECASE)

#: Attribute names a Users filter may name, lower-cased, mapped to the
#: canonical name the route branches on. ``emails.value`` and the bare
#: ``emails`` both resolve to the same canonical attribute: this hub stores
#: one email per account, so filtering by "the emails collection contains
#: this value" and "the email attribute equals this value" are the same
#: question here.
USER_FILTER_ATTRS = {
    "username": "userName",
    "externalid": "externalId",
    "emails.value": "emails",
    "emails": "emails",
}

#: Attribute names a Groups filter may name.
GROUP_FILTER_ATTRS = {
    "displayname": "displayName",
    "externalid": "externalId",
}


def parse_filter(raw: Optional[str], allowed: Dict[str, str]) -> Tuple[str, str]:
    """``("", "")`` for no filter; otherwise ``(canonical_attr, value)``.

    Raises :class:`ScimError` (``scimType: invalidFilter``) for anything not
    of the form ``attr eq "value"``, or naming an attribute not in
    ``allowed``. The attribute name is matched case-insensitively, since
    providers disagree on the casing of ``userName`` in a filter string even
    though they agree on it in a resource body.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    match = _FILTER_RE.match(raw)
    if not match:
        raise ScimError(f"unsupported filter expression: {raw}",
                        status=400, scim_type="invalidFilter")
    attr_raw, value = match.group(1), match.group(2)
    canonical = allowed.get(attr_raw.lower())
    if canonical is None:
        raise ScimError(f"unsupported filter attribute: {attr_raw}",
                        status=400, scim_type="invalidFilter")
    return canonical, value.replace('\\"', '"')


# ── paging ───────────────────────────────────────────────────────────────────

def clamp_start_index(start_index: Optional[int]) -> int:
    """SCIM's ``startIndex`` is 1-based; anything less becomes 1 (RFC 7644 §3.4.2)."""
    if start_index is None:
        return 1
    return max(1, int(start_index))


def clamp_count(count: Optional[int]) -> int:
    """``count`` bounded to ``[0, MAX_RESULTS]``, defaulting to ``DEFAULT_COUNT``."""
    if count is None:
        return DEFAULT_COUNT
    return max(0, min(int(count), MAX_RESULTS))


def list_response(resources: List[Dict[str, Any]], *, total: int, start_index: int) -> Dict[str, Any]:
    """The ``ListResponse`` envelope around one page of already-built resources."""
    return {
        "schemas": [SCHEMA_LIST_RESPONSE],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


# ── resource mapping ─────────────────────────────────────────────────────────

def extract_primary_email(emails: Optional[Iterable[Dict[str, Any]]]) -> str:
    """The value a SCIM ``emails`` array resolves to: the one marked
    ``primary``, else the first, else ``""``."""
    items = [e for e in (emails or []) if isinstance(e, dict)]
    if not items:
        return ""
    primary = next((e for e in items if e.get("primary")), items[0])
    return str(primary.get("value") or "")


def user_to_resource(user: Dict[str, Any], *, groups: List[Dict[str, str]],
                     base_url: str) -> Dict[str, Any]:
    """A ``common.identity`` user row as a SCIM User resource.

    ``name`` carries only ``formatted``: the users table has one
    ``display_name`` column, not separate given/family names, so a provider
    that reads ``name.givenName`` back after setting it will not get its own
    words back verbatim (see docs/scim.md's gotchas). ``groups`` is the list
    of ``{"value", "display"}`` the caller already fetched, so this stays a
    pure function of its arguments.
    """
    display_name = user.get("display_name") or user.get("username") or ""
    email = (user.get("email") or "").strip()
    return {
        "schemas": [SCHEMA_USER],
        "id": user["id"],
        "externalId": user.get("external_id"),
        "userName": user["username"],
        "displayName": display_name,
        "name": {"formatted": display_name},
        "emails": [{"value": email, "primary": True}] if email else [],
        "active": not bool(user.get("disabled")),
        "groups": [{"value": g["value"], "display": g.get("display", "")} for g in groups],
        "meta": {
            "resourceType": "User",
            "created": user.get("created_at"),
            "lastModified": user.get("updated_at"),
            "location": f"{base_url}/Users/{user['id']}",
        },
    }


def group_to_resource(group: Dict[str, Any], *, members: List[Dict[str, str]],
                      base_url: str) -> Dict[str, Any]:
    """A ``common.groups`` group row as a SCIM Group resource."""
    return {
        "schemas": [SCHEMA_GROUP],
        "id": group["id"],
        "externalId": group.get("external_id"),
        "displayName": group.get("display_name") or group.get("name"),
        "members": [{"value": m["value"], "display": m.get("display", "")} for m in members],
        "meta": {
            "resourceType": "Group",
            "created": group.get("created_at"),
            "lastModified": group.get("updated_at"),
            "location": f"{base_url}/Groups/{group['id']}",
        },
    }


# ── PATCH ────────────────────────────────────────────────────────────────────
#
# Two shapes of "add"/"replace"/"remove" operation are in real use: the
# RFC 7644 form, which names a ``path``, and the one Entra ID sends for a
# simple attribute change, ``{"op": "replace", "value": {"active": false}}``
# with no ``path`` at all. Both are normalised into one flat dict of the
# changes requested, so the route applies them with one block of code
# regardless of which shape a given provider used.

_MEMBER_REMOVE_RE = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.IGNORECASE)


def _op_kind(op: Dict[str, Any]) -> str:
    kind = str(op.get("op") or "").strip().lower()
    if kind not in ("add", "replace", "remove"):
        raise ScimError(f"unsupported PATCH op '{op.get('op')}'",
                        status=400, scim_type="invalidValue")
    return kind


def normalize_user_patch(operations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """SCIM PATCH ``Operations`` for a User, flattened to the fields changed.

    Possible keys in the result: ``active`` (bool), ``username``,
    ``display_name``, ``given_name``, ``family_name``, ``external_id``,
    ``email``. ``given_name``/``family_name`` are returned separately rather
    than combined here, because combining them correctly needs the user's
    *current* name when a PATCH touches only one of the two, and this
    function has no database access; the route does that combining.
    """
    changes: Dict[str, Any] = {}
    for op in operations or []:
        kind = _op_kind(op)
        path = str(op.get("path") or "").strip()
        value = op.get("value")
        if not path:
            if not isinstance(value, dict):
                raise ScimError("a PATCH operation without a path needs an object value",
                                status=400, scim_type="invalidValue")
            for key, val in value.items():
                _apply_user_field(changes, key, val, remove=(kind == "remove"))
            continue
        _apply_user_field(changes, path, value, remove=(kind == "remove"))
    return changes


def _apply_user_field(changes: Dict[str, Any], path: str, value: Any, *, remove: bool) -> None:
    key = path.strip().lower()
    if key == "active":
        changes["active"] = False if remove else bool(value)
    elif key == "username":
        changes["username"] = "" if remove else str(value or "")
    elif key == "displayname":
        changes["display_name"] = "" if remove else str(value or "")
    elif key == "name.givenname":
        changes["given_name"] = "" if remove else str(value or "")
    elif key == "name.familyname":
        changes["family_name"] = "" if remove else str(value or "")
    elif key == "externalid":
        changes["external_id"] = None if remove else str(value or "")
    elif key == "emails" or key.startswith("emails"):
        if remove:
            changes["email"] = ""
        elif isinstance(value, list):
            changes["email"] = extract_primary_email(value)
        elif isinstance(value, dict):
            changes["email"] = extract_primary_email([value])
        else:
            # A provider that PATCHes a single sub-attribute, e.g.
            # ``emails[type eq "work"].value``, sends the new address as a
            # bare string rather than a list of email objects.
            changes["email"] = str(value or "")
    else:
        raise ScimError(f"unsupported PATCH path '{path}'",
                        status=400, scim_type="invalidPath")


def normalize_group_patch(operations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """SCIM PATCH ``Operations`` for a Group, flattened.

    Possible keys: ``display_name``, ``external_id``, ``members_add`` (list
    of user ids), ``members_remove`` (list), ``members_replace`` (list, only
    set by a ``replace`` on the bare ``members`` path).
    """
    changes: Dict[str, Any] = {}
    add: List[str] = []
    remove: List[str] = []
    replace: Optional[List[str]] = None
    for op in operations or []:
        kind = _op_kind(op)
        path = str(op.get("path") or "").strip()
        value = op.get("value")
        if not path:
            if not isinstance(value, dict):
                raise ScimError("a PATCH operation without a path needs an object value",
                                status=400, scim_type="invalidValue")
            for key, val in value.items():
                if key.lower() == "displayname":
                    changes["display_name"] = str(val or "")
                elif key.lower() == "externalid":
                    changes["external_id"] = str(val or "")
            continue
        key = path.strip().lower()
        if key == "displayname":
            changes["display_name"] = str(value or "")
        elif key == "externalid":
            changes["external_id"] = str(value or "")
        elif key == "members":
            ids = [str(m["value"]) for m in (value or []) if isinstance(m, dict) and m.get("value")]
            if kind == "remove":
                remove.extend(ids)
            elif kind == "add":
                add.extend(ids)
            else:
                replace = ids
        else:
            member_match = _MEMBER_REMOVE_RE.match(path)
            if member_match and kind == "remove":
                remove.append(member_match.group(1))
            else:
                raise ScimError(f"unsupported PATCH path '{path}'",
                                status=400, scim_type="invalidPath")
    if add:
        changes["members_add"] = add
    if remove:
        changes["members_remove"] = remove
    if replace is not None:
        changes["members_replace"] = replace
    return changes


__all__ = [
    "DEFAULT_COUNT", "GROUP_FILTER_ATTRS", "MAX_RESULTS", "SCHEMA_ERROR", "SCHEMA_GROUP",
    "SCHEMA_LIST_RESPONSE", "SCHEMA_PATCH_OP", "SCHEMA_RESOURCE_TYPE",
    "SCHEMA_SERVICE_PROVIDER_CONFIG", "SCHEMA_USER", "USER_FILTER_ATTRS", "ScimError",
    "clamp_count", "clamp_start_index", "error_body", "extract_primary_email",
    "group_to_resource", "list_response", "normalize_group_patch", "normalize_user_patch",
    "parse_filter", "user_to_resource",
]

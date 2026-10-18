"""One operator: every request is the local operator, nothing is refused.

This edition has no login, accounts, sessions, API keys or workspace
membership (common/auth.py, common/identity.py). These tests pin that the
names the routes still call keep answering as the operator.
"""
from types import SimpleNamespace

from common import identity
from common.auth import LOCAL_OPERATOR_ID, LOCAL_PRINCIPAL, SINGLE, WS_EDITOR, WS_OWNER, role_satisfies


def test_the_mode_is_always_single():
    assert identity.current_mode() == SINGLE
    assert not any(identity.mode_features().values())


def test_every_request_is_allowed_as_the_operator():
    allowed, principal = identity.authorize_request(SimpleNamespace(headers={}))
    assert allowed is True
    assert principal is LOCAL_PRINCIPAL
    assert principal.is_admin and principal.reaches("any-workspace")


def test_require_role_never_raises():
    identity.require_role(LOCAL_PRINCIPAL, admin=True, workspace="w", role=WS_OWNER)
    identity.require_role(None, admin=True)


def test_request_principal_defaults_to_the_operator():
    assert identity.request_principal(None) is LOCAL_PRINCIPAL
    req = SimpleNamespace(state=SimpleNamespace())
    assert identity.request_principal(req) is LOCAL_PRINCIPAL


def test_current_user_is_the_operator_unless_bound():
    assert identity.current_user_id() == LOCAL_OPERATOR_ID
    token = identity.set_current_user("delegator")
    try:
        assert identity.current_user_id() == "delegator"
    finally:
        identity.reset_current_user(token)
    assert identity.current_user_id() == LOCAL_OPERATOR_ID


def test_role_ranks_still_compare():
    assert role_satisfies(WS_OWNER, WS_EDITOR)
    assert not role_satisfies(None, WS_EDITOR)


def test_principal_dict_is_json_ready():
    data = identity.principal_dict(LOCAL_PRINCIPAL)
    assert data["id"] == LOCAL_OPERATOR_ID and data["scope"] is None

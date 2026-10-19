"""One operator: every request is the local operator, nothing is refused.

This edition has no login, accounts, sessions, API keys or workspace
membership (common/auth.py, common/identity.py). These tests pin that the
names the routes still call keep answering as the operator.
"""
from types import SimpleNamespace

from common import identity
from common.auth import LOCAL_OPERATOR_ID, LOCAL_PRINCIPAL


def test_the_operator_is_local():
    assert LOCAL_PRINCIPAL.id == LOCAL_OPERATOR_ID
    assert LOCAL_PRINCIPAL.kind == "local"


def test_request_principal_is_the_operator():
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

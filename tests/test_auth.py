"""Optional API-token authorization predicate tests."""
from common.auth import is_authorized, extract_bearer

TOKEN = "s3cret"


def test_open_when_no_token_configured():
    assert is_authorized(configured_token="", method="GET", path="/api/tasks") is True


def test_blocks_api_without_token():
    assert is_authorized(configured_token=TOKEN, method="GET", path="/api/tasks") is False


def test_allows_bearer_header():
    assert is_authorized(configured_token=TOKEN, method="GET", path="/api/tasks",
                         auth_header=f"Bearer {TOKEN}") is True


def test_allows_x_api_token_header():
    assert is_authorized(configured_token=TOKEN, method="POST", path="/api/chat/message",
                         x_api_token=TOKEN) is True


def test_allows_query_token_for_eventsource():
    assert is_authorized(configured_token=TOKEN, method="GET", path="/api/stream",
                         query_token=TOKEN) is True


def test_rejects_wrong_token():
    assert is_authorized(configured_token=TOKEN, method="GET", path="/api/tasks",
                         auth_header="Bearer nope") is False


def test_preflight_and_non_api_paths_pass():
    assert is_authorized(configured_token=TOKEN, method="OPTIONS", path="/api/tasks") is True
    assert is_authorized(configured_token=TOKEN, method="GET", path="/") is True


def test_extract_bearer():
    assert extract_bearer("Bearer abc") == "abc"
    assert extract_bearer("bearer  spaced ") == "spaced"
    assert extract_bearer("Basic xyz") is None
    assert extract_bearer(None) is None


# ---------------------------------------------------------------------------
# Middleware ordering
#
# The token guard must sit *inside* CORSMiddleware. Starlette's add_middleware
# inserts at the head of the list and the head is the outermost layer, so the
# last registration wraps every earlier one: registering the guard after CORS
# puts its 401 outside CORSMiddleware, the response reaches the browser without
# an Access-Control-Allow-Origin header, and the browser reports a CORS failure
# instead of the auth failure that actually happened.
# ---------------------------------------------------------------------------

ORIGIN = "http://localhost:5173"


def _client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_root_banner_is_cors_enabled():
    response = _client().get("/", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_unauthorized_response_still_carries_cors_headers():
    from common.config import settings

    client = _client()
    previous = settings.api_token
    settings.api_token = TOKEN
    try:
        rejected = client.get("/api/health", headers={"Origin": ORIGIN})
        assert rejected.status_code == 401
        assert rejected.headers.get("access-control-allow-origin") == ORIGIN

        accepted = client.get("/api/health", headers={"Origin": ORIGIN, "X-Api-Token": TOKEN})
        assert accepted.status_code == 200
        assert accepted.headers.get("access-control-allow-origin") == ORIGIN
    finally:
        settings.api_token = previous

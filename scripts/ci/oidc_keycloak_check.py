"""
End-to-end single sign-on against a real Keycloak (the ``oidc-keycloak`` CI job).

The unit tests in ``tests/test_oidc.py`` run against a fake provider; this
runs the same flow against the real thing, so what they cannot catch
(discovery quirks, the actual token endpoint, Keycloak's groups mapper, its
login form) fails in CI rather than in somebody's deployment.

Expects Keycloak on http://localhost:8080 with ``deploy/keycloak/realm-export.json``
imported. The hub side runs in process through FastAPI's TestClient, the
browser side is a ``requests.Session`` talking to Keycloak; the one redirect
that crosses between them (Keycloak back to the callback) is replayed by hand.

Exit status 0 when alice comes out an administrator in both groups and bob a
member with editor access to ``default``; anything else exits non-zero.
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
ISSUER = f"{KEYCLOAK}/realms/agents-hub"
REPO = Path(__file__).resolve().parents[2]

# Configure the hub before anything imports common.config.
os.environ["AGENTS_HUB_ROOT"] = tempfile.mkdtemp(prefix="agents_hub_oidc_ci_")
os.environ["AGENTS_HUB_DATABASE_URL"] = ""
os.environ.update({
    "AUTH_MODE": "multi",
    "AUTH_OIDC_ISSUER": ISSUER,
    "AUTH_OIDC_CLIENT_ID": "agents-hub",
    "AUTH_OIDC_CLIENT_SECRET": "agents-hub-secret",
    "AUTH_OIDC_GROUPS_CLAIM": "groups",
    "AUTH_OIDC_PROVIDER_NAME": "Keycloak",
    "AUTH_PUBLIC_URL": "http://testserver",
})
os.environ.pop("AGENTS_HUB_API_TOKEN", None)
for path in (str(REPO), str(REPO / "dashboard" / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class CheckFailed(Exception):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def sign_in(app, username: str, password: str) -> dict:
    """Drive one browser through the whole flow; returns /api/auth/me."""
    hub = TestClient(app, follow_redirects=False)
    browser = requests.Session()

    start = hub.get("/api/auth/oidc/start", params={"next": "/dashboard"})
    expect(start.status_code == 302, f"start answered {start.status_code}: {start.text}")
    location = start.headers["location"]
    expect(location.startswith(f"{ISSUER}/protocol/openid-connect/auth"),
           f"start redirected somewhere unexpected: {location}")

    page = browser.get(location, timeout=30)
    expect(page.status_code == 200, f"Keycloak login page answered {page.status_code}")
    match = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page.text) \
        or re.search(r'<form[^>]*action="([^"]+)"', page.text)
    expect(match is not None, "no login form on Keycloak's page")
    action = html.unescape(match.group(1))
    # Keycloak marks its cookies Secure even on plain http; a browser treats
    # localhost as a secure context and sends them anyway, requests does not.
    for cookie in browser.cookies:
        cookie.secure = False

    posted = browser.post(action, data={"username": username, "password": password,
                                        "credentialId": ""},
                          allow_redirects=False, timeout=30)
    expect(posted.status_code in (302, 303),
           f"Keycloak did not accept {username}'s password ({posted.status_code})")
    back = posted.headers["location"]
    expect(back.startswith("http://testserver/api/auth/oidc/callback"),
           f"Keycloak redirected somewhere unexpected: {back}")

    parts = urllib.parse.urlsplit(back)
    done = hub.get(f"{parts.path}?{parts.query}")
    expect(done.status_code == 302, f"callback answered {done.status_code}: {done.text}")
    landing = done.headers["location"]
    fragment = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(landing).fragment))
    expect("token" in fragment, f"callback carried no token: {landing}")
    expect(fragment.get("next") == "/dashboard", f"next was lost: {fragment}")
    expect("token=" not in urllib.parse.urlsplit(landing).query, "token leaked into the query")

    me = hub.get("/api/auth/me", headers={"Authorization": f"Bearer {fragment['token']}"})
    expect(me.status_code == 200, f"/api/auth/me answered {me.status_code}: {me.text}")
    return me.json()


def main() -> int:
    from common import groups, identity
    from dashboard.backend.main import app

    groups.add_mapping("devs", target="workspace", role="editor", workspace="default")
    groups.add_mapping("hub-admins", target="role", role="admin")

    try:
        alice = sign_in(app, "alice", "alice")
        print("alice:", {k: alice.get(k) for k in ("username", "role", "groups", "via")})
        expect(alice["username"] == "alice", f"alice's username is {alice['username']}")
        expect(alice["role"] == "admin", f"alice is {alice['role']}, not admin")
        expect(sorted(alice.get("groups") or []) == ["devs", "hub-admins"],
               f"alice's groups are {alice.get('groups')}")
        expect(alice.get("via") == "oidc", "alice's session is not an OIDC one")
        expect(alice.get("email") == "alice@example.com", f"alice's email is {alice.get('email')}")

        bob = sign_in(app, "bob", "bob")
        print("bob:", {k: bob.get(k) for k in ("username", "role", "groups", "via")})
        expect(bob["role"] == "member", f"bob is {bob['role']}, not member")
        expect(sorted(bob.get("groups") or []) == ["devs"], f"bob's groups are {bob.get('groups')}")
        role = identity.membership_role("default", bob["id"])
        expect(role == "editor", f"bob's role in default is {role}, not editor")

        again = sign_in(app, "alice", "alice")
        expect(again["id"] == alice["id"], "a second login created a second account")
    except CheckFailed as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK: single sign-on against Keycloak works end to end")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Reading the internet from an isolated workspace (common/isolation.py):
fetch_url, web_search and the browser tools enforce the workspace's reading
list in process (tools/web.py, tools/browser.py), and the browser service
holds a read only session to GET and HEAD (deploy/browser/policy.py, app.py).

Nothing leaves the machine: DNS is monkeypatched, fetches go through an httpx
MockTransport, the search provider and the browser service are stand-ins.

Run: ``python -m pytest tests/test_isolated_reads.py -q``
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from common import isolation
from tools import browser, web, web_log

BROWSER_DIR = Path(__file__).resolve().parents[1] / "deploy" / "browser"

PUBLIC = "93.184.216.34"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def workspaces(monkeypatch):
    """Workspace settings by name, read by common.isolation; "iso" is isolated
    with example.org on its list and is the workspace the tools run in."""
    table = {"iso": {"isolated": True, "isolation_allow_domains": ["example.org"]},
             "plain": {}}
    monkeypatch.setattr(isolation, "_settings", lambda ws: dict(table.get(ws or "", {})))
    monkeypatch.setenv("AGENT_WORKSPACE", "iso")
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.delenv(web.MAX_REQUESTS_ENV, raising=False)
    monkeypatch.delenv(browser.ADOPT_ENV, raising=False)
    web.reset_isolated_budget()
    yield table
    web.reset_isolated_budget()


@pytest.fixture
def logged(monkeypatch):
    """Every web log entry written, in order."""
    entries = []
    monkeypatch.setattr(web_log, "append", lambda record: entries.append(dict(record)))
    return entries


@pytest.fixture
def dns(monkeypatch):
    """Every host is public except intranet.example.org."""
    def resolve(host, port):
        addr = "10.0.0.5" if host == "intranet.example.org" else PUBLIC
        return [(2, 1, 6, "", (addr, 0))]
    monkeypatch.setattr(web.socket, "getaddrinfo", resolve)


class _Web:
    """A tiny internet: routes by URL, records every request."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get(str(request.url))
        if handler is None:
            return httpx.Response(404, text="not here")
        return handler(request)


@pytest.fixture
def net(monkeypatch):
    def install(routes):
        fake = _Web(routes)
        monkeypatch.setattr(web, "_fetch_client", lambda timeout: httpx.Client(
            transport=httpx.MockTransport(fake), follow_redirects=False, timeout=timeout))
        return fake
    return install


def _page(text, **headers):
    return lambda request: httpx.Response(200, html=f"<html><body><p>{text}</p></body></html>",
                                          headers=headers)


def _redirect(location, **headers):
    return lambda request: httpx.Response(302, headers={"location": location, **headers})


# ── fetch_url ────────────────────────────────────────────────────────────────

def test_an_allowed_host_is_read_with_get_a_fixed_agent_and_no_cookies(workspaces, logged, dns, net):
    fake = net({"https://docs.example.org/page": _page("Hello from the list")})
    out = web.fetch_url.invoke({"url": "https://docs.example.org/page"})
    assert "Hello from the list" in out and "UNTRUSTED_WEB_CONTENT" in out
    [req] = fake.requests
    assert req.method == "GET" and req.content == b""
    assert req.headers["user-agent"] == "agents-hub/1.0 (+web tool)"
    assert "cookie" not in req.headers and "authorization" not in req.headers
    assert logged[-1]["status"] == "ok" and logged[-1]["workspace"] == "iso" and logged[-1]["isolated"]


def test_a_host_off_the_list_is_refused_before_any_request(workspaces, logged, dns, net):
    fake = net({})
    out = web.fetch_url.invoke({"url": "https://evil.test/collect?d=secret"})
    assert "refused" in out and "'evil.test'" in out and "Isolation settings" in out
    assert fake.requests == []
    assert logged[-1]["status"] == "refused" and logged[-1]["workspace"] == "iso"


def test_a_redirect_off_the_list_is_refused(workspaces, logged, dns, net):
    fake = net({"https://example.org/r": _redirect("https://evil.test/x")})
    out = web.fetch_url.invoke({"url": "https://example.org/r"})
    assert "refused" in out and "evil.test" in out
    assert [str(r.url) for r in fake.requests] == ["https://example.org/r"]
    assert logged[-1]["status"] == "refused"


def test_a_listed_host_on_a_private_address_is_refused(workspaces, logged, dns, net):
    workspaces["iso"]["isolation_allow_domains"] = ["example.org"]
    fake = net({})
    out = web.fetch_url.invoke({"url": "http://intranet.example.org/admin"})
    assert "refused" in out and "10.0.0.5" in out
    assert fake.requests == []


def test_cookies_are_dropped_between_hops_and_every_hop_is_a_get(workspaces, logged, dns, net):
    fake = net({
        "https://example.org/login": _redirect("/home", **{"set-cookie": "sid=abc; Path=/"}),
        "https://example.org/home": _page("home"),
    })
    out = web.fetch_url.invoke({"url": "https://example.org/login"})
    assert "home" in out
    assert [r.method for r in fake.requests] == ["GET", "GET"]
    assert "cookie" not in fake.requests[1].headers


def test_a_url_with_credentials_is_refused(workspaces, logged, dns, net):
    fake = net({})
    out = web.fetch_url.invoke({"url": "https://user:pw@example.org/"})
    assert "refused" in out and "user name or password" in out
    assert fake.requests == []


def test_the_hubs_own_lists_do_not_apply_the_workspace_list_replaces_them(workspaces, logged, dns, net,
                                                                          monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "web_deny_domains", ("example.org",))
    monkeypatch.setattr(settings, "web_domain_policy_enabled", True)
    monkeypatch.setattr(settings, "web_allow_domains", ("evil.test",))
    net({"https://example.org/": _page("listed")})
    assert "listed" in web.fetch_url.invoke({"url": "https://example.org/"})
    assert "refused" in web.fetch_url.invoke({"url": "https://evil.test/"})


def test_an_empty_list_reads_nothing(workspaces, logged, dns, net):
    workspaces["iso"]["isolation_allow_domains"] = []
    fake = net({})
    out = web.fetch_url.invoke({"url": "https://example.org/"})
    assert "refused" in out and "reading list is empty" in out
    assert fake.requests == []


def test_the_budget_is_per_run_and_refuses_past_it(workspaces, logged, dns, net, monkeypatch):
    monkeypatch.setenv(web.MAX_REQUESTS_ENV, "2")
    monkeypatch.setenv("AGENT_RUN_ID", "run-a")
    fake = net({"https://example.org/": _page("ok")})
    assert "ok" in web.fetch_url.invoke({"url": "https://example.org/"})
    assert "ok" in web.fetch_url.invoke({"url": "https://example.org/"})
    out = web.fetch_url.invoke({"url": "https://example.org/"})
    assert "refused" in out and web.MAX_REQUESTS_ENV in out
    assert len(fake.requests) == 2
    assert logged[-1]["status"] == "refused"
    # Another run has a budget of its own.
    monkeypatch.setenv("AGENT_RUN_ID", "run-b")
    assert "ok" in web.fetch_url.invoke({"url": "https://example.org/"})


def test_isolated_fetch_check_is_reusable_with_an_explicit_workspace(workspaces, dns):
    assert web.isolated_fetch_check("https://www.example.org/a", "iso") == (True, "")
    assert web.isolated_fetch_check("https://notexample.org/", "iso")[0] is False
    assert web.isolated_fetch_check("ftp://example.org/", "iso")[0] is False
    assert web.isolated_fetch_check("https://evil.test/", "iso", resolve=False)[0] is False


def test_a_workspace_that_is_not_isolated_reads_as_before(workspaces, logged, dns, net, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "plain")
    monkeypatch.setenv(web.MAX_REQUESTS_ENV, "0")
    fake = net({"https://evil.test/": _page("anything public")})
    out = web.fetch_url.invoke({"url": "https://evil.test/"})
    assert "anything public" in out
    assert len(fake.requests) == 1
    assert "isolated" not in logged[-1]
    assert web.isolated_workspace() is None


# ── web_search ───────────────────────────────────────────────────────────────

@pytest.fixture
def search(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "web_search_provider", "brave")
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    monkeypatch.setattr(web, "_SEARCH_CACHE", {})
    monkeypatch.setattr(web, "_search_config", lambda: ("brave", "k"))
    seen = []

    def fake(query, count, key, timeout, include=None, exclude=None):
        seen.append({"query": query, "include": include, "exclude": exclude})
        return [{"title": "Listed", "url": "https://docs.example.org/a", "snippet": "on the list"},
                {"title": "Off", "url": "https://evil.test/b", "snippet": "off the list"}]
    monkeypatch.setitem(web._PROVIDERS, "brave", fake)
    return seen


def test_search_goes_to_the_provider_narrowed_to_the_list(workspaces, logged, search):
    out = web.web_search.invoke({"query": "python docs"})
    assert search[0]["query"] == "python docs" and search[0]["include"] == ["example.org"]
    assert "docs.example.org" in out and "evil.test" not in out
    assert logged[-1]["kind"] == "search" and logged[-1]["workspace"] == "iso"
    assert logged[-1]["blocked_results"] == 1


def test_search_with_an_empty_list_asks_nobody(workspaces, logged, search):
    workspaces["iso"]["isolation_allow_domains"] = []
    out = web.web_search.invoke({"query": "anything"})
    assert "refused" in out and search == []


def test_search_spends_the_same_budget(workspaces, logged, search, monkeypatch):
    monkeypatch.setenv(web.MAX_REQUESTS_ENV, "1")
    web.web_search.invoke({"query": "one"})
    out = web.web_search.invoke({"query": "two"})
    assert "refused" in out and len(search) == 1


# ── browser tools ────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._data, self.content = status, data or {}, content
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


@pytest.fixture
def service(monkeypatch, tmp_path):
    """A stand-in browser service; ``page_url`` is where navigation lands."""
    from common.config import settings
    monkeypatch.setattr(settings, "browser_url", "http://browser.test:3000")
    monkeypatch.setattr(settings, "browser_token", "tok")
    monkeypatch.setattr(browser, "_SESSIONS", {})
    monkeypatch.setattr(browser, "_screenshot_dir", lambda: tmp_path / "screenshots")
    state = {"calls": [], "page_url": "https://docs.example.org/"}

    def fake(method, path, **kw):
        state["calls"].append((method, path, kw))
        if path == "/sessions" and method == "POST":
            return _Resp(data={"session_id": "S1"})
        if path.endswith("/navigate"):
            return _Resp(data={"url": state["page_url"], "title": "T", "status": 200})
        if path.endswith("/read"):
            return _Resp(data={"url": state["page_url"], "title": "T",
                               "html": "<html><body><p>Read me</p></body></html>"})
        if path.endswith("/screenshot"):
            return _Resp(content=b"\x89PNG fake")
        if method == "DELETE":
            return _Resp(data={"ok": True})
        raise AssertionError((method, path))
    monkeypatch.setattr(browser, "_request", fake)
    return state


def test_the_session_is_read_only_with_the_list_as_its_allowlist(workspaces, logged, dns, service):
    out = browser.browser_open.invoke({"url": "https://docs.example.org/"})
    assert "Call browser_read" in out
    create = [c for c in service["calls"] if c[1] == "/sessions"][0]
    policy = create[2]["json"]["policy"]
    assert policy == {"deny_domains": [], "allow_domains": ["example.org"],
                      "allowlist_enabled": True, "read_only": True}
    assert "Read me" in browser.browser_read.invoke({})
    shot = json.loads(browser.browser_screenshot.invoke({}))
    assert shot["ok"] and shot["path"].startswith("screenshots/")
    assert browser.browser_close.invoke({}) == "Browser session closed."


def test_browser_open_off_the_list_is_refused_before_the_service(workspaces, logged, dns, service):
    out = browser.browser_open.invoke({"url": "https://evil.test/"})
    assert "refused" in out and "Isolation settings" in out
    assert service["calls"] == []


def test_a_landing_off_the_list_is_refused_and_the_session_closed(workspaces, logged, dns, service):
    service["page_url"] = "https://evil.test/landed"
    out = browser.browser_open.invoke({"url": "https://docs.example.org/"})
    assert "refused the page it landed on" in out
    assert ("DELETE", "/sessions/S1", {}) in service["calls"]


def test_browser_read_rechecks_where_the_page_is(workspaces, logged, dns, service):
    browser.browser_open.invoke({"url": "https://docs.example.org/"})
    service["page_url"] = "https://evil.test/moved"
    out = browser.browser_read.invoke({})
    assert "refused the page" in out and "Read me" not in out


def test_browser_act_is_refused_in_an_isolated_workspace(workspaces, service):
    out = browser.browser_act.invoke({"action": "click", "selector": "button"})
    assert out == browser.ISOLATED_ACT_REFUSED and service["calls"] == []


def test_a_handed_over_session_is_not_adopted(workspaces, logged, dns, service, monkeypatch):
    monkeypatch.setenv(browser.ADOPT_ENV, "H1")
    browser.browser_open.invoke({"url": "https://docs.example.org/"})
    assert not any("/sessions/H1" in c[1] for c in service["calls"])
    assert not any(c[0] == "GET" and c[1] == "/sessions" for c in service["calls"])


def test_browser_calls_spend_the_reading_budget(workspaces, logged, dns, service, monkeypatch):
    monkeypatch.setenv(web.MAX_REQUESTS_ENV, "1")
    browser.browser_open.invoke({"url": "https://docs.example.org/"})
    out = browser.browser_read.invoke({})
    assert "refused" in out and web.MAX_REQUESTS_ENV in out


def test_a_plain_workspace_keeps_its_old_session_policy(workspaces, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "plain")
    policy = browser.session_policy()
    assert "read_only" not in policy
    assert browser.browser_act.invoke({"action": "click", "selector": "a"}) != browser.ISOLATED_ACT_REFUSED


# ── the browser service: read only sessions ──────────────────────────────────

def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, BROWSER_DIR / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("browser_service_policy_ro", "policy.py")


@pytest.fixture
def service_app(monkeypatch):
    monkeypatch.syspath_prepend(str(BROWSER_DIR))
    monkeypatch.delitem(sys.modules, "policy", raising=False)
    module = _load("browser_service_app_ro", "app.py")
    monkeypatch.setattr(module.state, "sessions", {})
    yield module
    sys.modules.pop("policy", None)


RO = {"read_only": True, "allow_domains": ["example.org"],
      "internal_origins": ["http://hub.local:8000"], "internal_paths": ["/apps/"]}


def test_a_read_only_policy_always_applies_its_list_and_exempts_no_hub_page():
    pol = policy.Policy.from_dict({**RO, "allowlist_enabled": False})
    assert pol.read_only and pol.allowlist_enabled and pol.internal_origins == ()
    assert policy.check_url("https://hub.local:8000/apps/x/", pol, resolve=False)[0] is False
    assert policy.check_url("https://a.example.org/", pol, resolve=False) == (True, "")
    assert policy.Policy.from_dict({**RO, "allow_domains": []}).allowlist_enabled
    assert policy.check_url("https://example.org/",
                            policy.Policy.from_dict({"read_only": True}), resolve=False)[0] is False
    assert pol.to_dict()["read_only"] is True


@pytest.mark.parametrize("method,body,ok", [
    ("GET", False, True), ("HEAD", False, True), ("get", False, True),
    ("POST", False, False), ("PUT", True, False), ("DELETE", False, False),
    ("OPTIONS", False, False), ("GET", True, False),
])
def test_check_method_holds_a_read_only_session_to_get_and_head(method, body, ok):
    pol = policy.Policy.from_dict(RO)
    assert policy.check_method(method, body, pol)[0] is ok
    # A session that is not read only lets every method through.
    assert policy.check_method(method, body, policy.Policy())[0] is True


class _FakeRoute:
    def __init__(self):
        self.calls = []

    async def abort(self, code=None):
        self.calls.append(("abort", code))

    async def fetch(self, **kw):
        self.calls.append(("fetch", kw))
        return type("R", (), {"status": 200, "headers": {}})()

    async def fulfill(self, response=None):
        self.calls.append(("fulfill", response.status))


class _Req:
    def __init__(self, url, method="GET", body=None):
        self.url, self.method, self.post_data_buffer = url, method, body


def _ro_session(app, page=None, data=None):
    return app.Session(id="R1", policy=app.Policy.from_dict(data or RO), context=None, page=page)


@pytest.mark.parametrize("req", [
    _Req("https://example.org/form", "POST", b"a=1"),           # a form post
    _Req("https://example.org/api", "POST", b'{"x": 1}'),       # fetch or XHR with a body
    _Req("https://example.org/beacon", "POST", None),           # sendBeacon with no payload
    _Req("https://example.org/api", "PUT", b"x"),
])
def test_the_route_handler_blocks_writes_in_a_read_only_session(service_app, dns, req):
    s = _ro_session(service_app)
    route = _FakeRoute()
    asyncio.run(service_app._make_route_handler(s)(route, req))
    assert route.calls == [("abort", "blockedbyclient")]
    assert "only reads" in s.blocked[0]["reason"]


def test_the_route_handler_lets_a_listed_get_through_and_blocks_other_hosts(service_app, dns):
    s = _ro_session(service_app)
    ok_route, off_route = _FakeRoute(), _FakeRoute()
    asyncio.run(service_app._make_route_handler(s)(ok_route, _Req("https://cdn.example.org/a.js")))
    asyncio.run(service_app._make_route_handler(s)(off_route, _Req("https://tracker.test/p.gif")))
    assert ok_route.calls[-1] == ("fulfill", 200)
    assert off_route.calls == [("abort", "blockedbyclient")]


def test_a_post_still_passes_in_a_session_that_is_not_read_only(service_app, dns):
    s = service_app.Session(id="W", policy=service_app.Policy(), context=None, page=None)
    route = _FakeRoute()
    asyncio.run(service_app._make_route_handler(s)(route, _Req("https://example.org/f", "POST", b"a")))
    assert route.calls[-1] == ("fulfill", 200)


class _FakeWS:
    def __init__(self, url):
        self.url, self.closed, self.connected = url, None, False

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)

    def connect_to_server(self):
        self.connected = True


def test_websockets_are_refused_in_a_read_only_session(service_app, dns):
    s = _ro_session(service_app)
    ws = _FakeWS("wss://example.org/socket")
    asyncio.run(service_app._make_ws_handler(s)(ws))
    assert ws.closed and ws.closed[0] == 1008 and not ws.connected


class _Mouse:
    def __init__(self, log):
        self.log = log

    async def click(self, x, y):
        self.log.append("click")

    async def move(self, x, y):
        self.log.append("move")

    async def wheel(self, dx, dy):
        self.log.append("wheel")


class _Page:
    def __init__(self):
        self.url, self.log = "https://example.org/", []
        self.mouse = _Mouse(self.log)
        self.keyboard = _Mouse(self.log)

    async def title(self):
        return "T"

    async def wait_for_load_state(self, *a, **kw):
        return None


def test_action_endpoints_refuse_a_read_only_session(service_app, dns):
    from fastapi import HTTPException
    page = _Page()
    s = _ro_session(service_app, page=page)
    service_app.state.sessions[s.id] = s
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service_app.act("R1", service_app.Act(action="click", selector="button")))
    assert exc.value.status_code == 403
    for kind in ("click", "dblclick", "type", "key"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(service_app.send_input("R1", service_app.Input(kind=kind, text="x", key="Enter")))
        assert exc.value.status_code == 403
    assert page.log == []
    # Looking around still works.
    out = asyncio.run(service_app.send_input("R1", service_app.Input(kind="scroll", dy=200)))
    assert out["url"] == "https://example.org/" and page.log == ["move", "wheel"]
    assert asyncio.run(service_app.describe(s))["read_only"] is True

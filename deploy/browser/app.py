"""
Agents Hub browser service: a headless Chromium behind a small HTTP API.

Runs in its own container (see the Dockerfile next to this file and the
``browser`` profile in docker-compose.yml). The hub talks to it through
``tools/browser.py``; nothing else should be able to reach it, and every call
except ``/healthz`` must carry the shared token (``BROWSER_TOKEN``, the same
value the hub has as ``AGENTS_HUB_BROWSER_TOKEN``). The service refuses to
start without one.

Sessions
    One session is one isolated browser context (its own cookies and storage)
    with one current page. The hub opens one per agent run. A session idle for
    ``BROWSER_IDLE_TIMEOUT`` seconds is closed, and at most
    ``BROWSER_MAX_SESSIONS`` exist at once.

Policy
    Each session carries the domain policy the hub handed over when it was
    created. Every request the page makes, including subresources, XHR and each
    redirect hop, is routed through :func:`policy.check_url`: the domain policy,
    then the private-network block. See ``policy.py`` and
    docs/tools-and-capabilities.md.

Read only sessions
    A session created with ``policy.read_only`` (the hub's run gateway opens
    these for isolated workspaces) only reads: the page's requests must be
    GET or HEAD with no body, its WebSockets are refused, its allow list always
    applies, and ``/act`` and a person's clicks and typing on ``/input`` answer
    403. Navigation, reading, scrolling and screenshots work as usual.

Endpoints
    POST   /sessions                      {policy, run_id, workspace, owner, label} -> {session_id}
    GET    /sessions?run_id=&workspace=                       -> {sessions: [info]}
    GET    /sessions/{id}                                     -> info
    PATCH  /sessions/{id}                 {run_id, owner, label} -> info
    GET    /sessions/{id}/frame                               -> {url, title, width, height, image}
    POST   /sessions/{id}/input           {kind, x, y, text, key, dx, dy, url} -> {url, title, blocked}
    POST   /sessions/{id}/navigate        {url}               -> {url, title, status}
    GET    /sessions/{id}/read                                -> {url, title, html, blocked}
    POST   /sessions/{id}/act             {action, selector, text} -> {url, title, blocked}
    GET    /sessions/{id}/screenshot?full_page=0              -> image/png
    GET    /sessions/{id}/frame                               -> {url, title, width, height, image}
    WS     /sessions/{id}/stream                              frames as JSON messages
    POST   /sessions/{id}/input          {kind, x, y, ...}   -> {url, title, blocked}
    POST   /sessions/{id}/control        {on, by}            -> the session
    DELETE /sessions/{id}
    GET    /healthz                                           (no token)

Who a session belongs to
    The hub tags each session with the run it serves (``run_id``), the
    workspace whose policy it carries, whether an agent or a person drives it
    (``owner``) and a label. The registry lives here, in the one process that
    owns the browsers, so the hub's API and its agent subprocesses find the
    same session without sharing any state of their own. ``frame`` and
    ``input`` are what the dashboard uses to show a session and to drive it
    (docs/browser.md); both take the session lock, so a person's click and an
    agent's action never interleave inside one page.

``read`` returns the rendered DOM as HTML, not text: the hub turns it into text
with the same ``tools.web.html_to_text`` ``fetch_url`` uses, so both tools hand
the agent identically extracted, identically annotated content.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urljoin, urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from policy import Policy, check_method, check_url

log = logging.getLogger("browser_service")
logging.basicConfig(level=os.environ.get("BROWSER_LOG_LEVEL", "INFO"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


TOKEN = (os.environ.get("BROWSER_TOKEN") or os.environ.get("AGENTS_HUB_BROWSER_TOKEN") or "").strip()
MAX_SESSIONS = _env_int("BROWSER_MAX_SESSIONS", 8)
IDLE_TIMEOUT = _env_int("BROWSER_IDLE_TIMEOUT", 300)
NAV_TIMEOUT_MS = _env_int("BROWSER_NAV_TIMEOUT_MS", 20_000)
ACTION_TIMEOUT_MS = _env_int("BROWSER_ACTION_TIMEOUT_MS", 5_000)
MAX_HTML_CHARS = _env_int("BROWSER_MAX_HTML_CHARS", 2_000_000)
# The page size every session gets; the dashboard maps clicks through it.
VIEWPORT = {"width": 1280, "height": 800}
# Blocked requests remembered per session, reported back on the next call.
_BLOCKED_KEEP = 20
# A person who took control of a session and then went away (closed the tab)
# loses it after this long without a frame or an input, so the agent is not
# parked forever behind an empty chair.
CONTROL_IDLE = _env_int("BROWSER_CONTROL_IDLE", 120)
# The frame stream: CDP's screencast pushes a frame whenever the page paints.
# Without CDP (or when it fails) the stream falls back to a screenshot this
# often, sent only when the picture changed; either way a keepalive goes out
# after this many quiet seconds so the connection is known to be alive.
STREAM_FALLBACK_MS = _env_int("BROWSER_STREAM_FALLBACK_MS", 500)
STREAM_KEEPALIVE = _env_int("BROWSER_STREAM_KEEPALIVE", 15)


# ── Sessions ──────────────────────────────────────────────────────────────────

@dataclass
class Session:
    id: str
    policy: Policy
    context: Any
    page: Any
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    blocked: List[Dict[str, str]] = field(default_factory=list)
    run_id: str = ""
    workspace: str = ""
    owner: str = "agent"
    label: str = ""
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    # Who took control from the dashboard, and when they last did anything.
    controlled_by: str = ""
    controlled_at: float = 0.0

    def touch(self) -> None:
        self.last_used = time.monotonic()
        self.last_used_at = time.time()

    def person_touch(self) -> None:
        """A frame or an input from the dashboard: the person is still there."""
        self.controlled_at = time.monotonic()

    def controller(self) -> str:
        """Who holds control now, or "" once the holder has been idle for
        :data:`CONTROL_IDLE` seconds (the hold is dropped on the spot)."""
        if self.controlled_by and time.monotonic() - self.controlled_at > CONTROL_IDLE:
            log.info("session %s: %s released control by inactivity", self.id, self.controlled_by)
            self.controlled_by = ""
        return self.controlled_by

    def set_control(self, by: str) -> None:
        self.controlled_by = (by or "").strip()[:200]
        self.controlled_at = time.monotonic()

    def note_blocked(self, url: str, reason: str) -> None:
        self.blocked.append({"url": url[:500], "reason": reason})
        del self.blocked[:-_BLOCKED_KEEP]

    def take_blocked(self) -> List[Dict[str, str]]:
        out, self.blocked = self.blocked, []
        return out


class _State:
    playwright: Any = None
    browser: Any = None
    sessions: Dict[str, Session] = {}
    reaper: Optional[asyncio.Task] = None


state = _State()


async def _checked(url: str, policy: Policy) -> tuple:
    """``check_url`` off the event loop: it resolves DNS, which blocks."""
    return await asyncio.to_thread(check_url, url, policy)


def _make_route_handler(session: Session):
    """The request filter every session installs on its browser context.

    Each request is checked, then performed with ``route.fetch`` with
    redirects switched off, so a 3xx comes back to this handler instead of
    being followed inside the browser. Its ``Location`` is checked like any
    other URL before the response is handed to the page; the browser then
    requests the target, which comes through this handler again. That is the
    same per-hop re-validation ``fetch_url`` does.
    """
    async def handler(route, request) -> None:
        url = request.url
        if session.policy.read_only:
            ok, reason = check_method(getattr(request, "method", ""), _has_body(request), session.policy)
            if not ok:
                session.note_blocked(url, reason)
                await route.abort("blockedbyclient")
                return
        ok, reason = await _checked(url, session.policy)
        if not ok:
            session.note_blocked(url, reason)
            await route.abort("blockedbyclient")
            return
        try:
            response = await route.fetch(max_redirects=0, timeout=NAV_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - a failed upstream fetch aborts just that request for the page
            log.debug("fetch failed for %s: %s", url, exc)
            await route.abort("failed")
            return
        if 300 <= response.status < 400:
            location = response.headers.get("location")
            if location:
                target = urljoin(url, location)
                ok, reason = await _checked(target, session.policy)
                if not ok:
                    session.note_blocked(target, f"redirect from {url}: {reason}")
                    await route.abort("blockedbyclient")
                    return
        await route.fulfill(response=response)

    return handler


def _has_body(request: Any) -> bool:
    """Whether a Playwright request carries a body (a fetch or XHR payload,
    a form post, a beacon). Unreadable counts as a body: a read only session
    refuses what it cannot see into."""
    try:
        data = request.post_data_buffer
    except Exception:  # noqa: BLE001 - see the docstring
        return True
    return bool(data)


def _make_ws_handler(session: Session):
    async def handler(ws) -> None:
        if session.policy.read_only:
            # An open socket carries whatever the page writes into it.
            session.note_blocked(ws.url, "this session only reads: WebSockets are blocked")
            await ws.close(code=1008, reason="blocked by policy")
            return
        ok, reason = await _checked(ws.url, session.policy)
        if not ok:
            session.note_blocked(ws.url, reason)
            await ws.close(code=1008, reason="blocked by policy")
            return
        ws.connect_to_server()

    return handler


async def _new_session(policy: Policy) -> Session:
    context = await state.browser.new_context(
        accept_downloads=False,
        # A service worker's own fetches bypass context routing; blocking
        # workers keeps every request on the checked path.
        service_workers="block",
        viewport=dict(VIEWPORT),
        ignore_https_errors=False,
    )
    context.set_default_timeout(ACTION_TIMEOUT_MS)
    context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    page = await context.new_page()
    session = Session(id=secrets.token_urlsafe(16), policy=policy, context=context, page=page)
    await context.route("**/*", _make_route_handler(session))
    # WebSocket routing arrived in Playwright 1.48; on an older image the
    # page's WebSockets are not checked, which the docs call out.
    if hasattr(context, "route_web_socket"):
        await context.route_web_socket("**/*", _make_ws_handler(session))

    def _on_page(new_page) -> None:
        # A link with target=_blank or window.open: follow it, so the agent
        # keeps reading what it just opened. Routing is per context, so the new
        # page is filtered exactly like the first.
        session.page = new_page

    context.on("page", _on_page)
    return session


async def _close_session(session: Session) -> None:
    state.sessions.pop(session.id, None)
    try:
        await session.context.close()
    except Exception:  # noqa: BLE001 - the context may already be dead, the session is dropped either way
        log.debug("session context close failed", exc_info=True)


async def _reap_idle() -> int:
    now = time.monotonic()
    stale = [s for s in list(state.sessions.values()) if now - s.last_used > IDLE_TIMEOUT]
    for s in stale:
        if not s.lock.locked():
            await _close_session(s)
    return len(stale)


async def _reaper_loop() -> None:
    while True:
        await asyncio.sleep(max(5, min(60, IDLE_TIMEOUT // 4 or 5)))
        try:
            closed = await _reap_idle()
            if closed:
                log.info("closed %d idle browser session(s)", closed)
        except Exception:
            log.exception("idle session reaper failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not TOKEN:
        raise RuntimeError("BROWSER_TOKEN is not set; the browser service refuses to run without one")
    from playwright.async_api import async_playwright
    state.playwright = await async_playwright().start()
    state.browser = await state.playwright.chromium.launch(headless=True)
    state.reaper = asyncio.create_task(_reaper_loop())
    try:
        yield
    finally:
        if state.reaper:
            state.reaper.cancel()
        for s in list(state.sessions.values()):
            await _close_session(s)
        await state.browser.close()
        await state.playwright.stop()


app = FastAPI(title="Agents Hub browser service", lifespan=lifespan)


# ── Auth ──────────────────────────────────────────────────────────────────────

def token_ok(header_value: Optional[str], token: str) -> bool:
    """Constant-time check of an ``Authorization: Bearer <token>`` header."""
    if not token or not header_value:
        return False
    scheme, _, value = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), token.encode())


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not token_ok(authorization, TOKEN):
        raise HTTPException(status_code=401, detail="missing or wrong token")


def _session(session_id: str) -> Session:
    s = state.sessions.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="no such session (closed or expired)")
    s.touch()
    return s


async def _page_info(s: Session) -> Dict[str, Any]:
    try:
        title = await s.page.title()
    except Exception:  # noqa: BLE001 - the title is cosmetic, an empty one is shown
        log.debug("page title unavailable", exc_info=True)
        title = ""
    return {"url": s.page.url, "title": title, "blocked": s.take_blocked()}


async def _enforce_current_url(s: Session) -> Optional[str]:
    """Re-check where the page ended up. Routing already refused anything
    blocked; this is the second, independent check on the final address."""
    url = s.page.url or ""
    if urlparse(url).scheme not in ("http", "https"):
        return None
    ok, reason = await _checked(url, s.policy)
    if ok:
        return None
    s.note_blocked(url, reason)
    try:
        await s.page.goto("about:blank")
    except Exception:  # noqa: BLE001 - blanking the page is best effort, the refusal is returned anyway
        log.debug("blanking the page failed", exc_info=True)
    return reason


# ── API ───────────────────────────────────────────────────────────────────────

Owner = Literal["agent", "user"]


class CreateSession(BaseModel):
    policy: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    workspace: str = ""
    owner: Owner = "agent"
    label: str = ""


class PatchSession(BaseModel):
    run_id: Optional[str] = None
    owner: Optional[Owner] = None
    label: Optional[str] = None


class Input(BaseModel):
    kind: Literal["click", "dblclick", "mousemove", "type", "key", "scroll",
                  "navigate", "back", "forward", "reload"]
    x: float = 0
    y: float = 0
    text: str = ""
    key: str = ""
    dx: float = 0
    dy: float = 0
    url: str = ""


class Navigate(BaseModel):
    url: str


class Act(BaseModel):
    action: Literal["click", "fill", "press", "scroll", "select"]
    selector: str = ""
    text: str = ""


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "sessions": len(state.sessions), "max_sessions": MAX_SESSIONS}


@app.post("/sessions", dependencies=[Depends(require_token)])
async def create_session(body: CreateSession) -> Dict[str, Any]:
    if len(state.sessions) >= MAX_SESSIONS:
        await _reap_idle()
    if len(state.sessions) >= MAX_SESSIONS:
        raise HTTPException(status_code=429, detail=f"session limit reached ({MAX_SESSIONS})")
    s = await _new_session(Policy.from_dict(body.policy))
    s.run_id, s.workspace, s.owner, s.label = (
        body.run_id.strip(), body.workspace.strip(), body.owner, body.label.strip()[:200])
    state.sessions[s.id] = s
    return {"session_id": s.id, "idle_timeout": IDLE_TIMEOUT}


async def describe(s: Session) -> Dict[str, Any]:
    """One session as the listing shows it. Reading the title does not need
    the lock: it is a snapshot, and a page mid-action just answers late."""
    try:
        title = await s.page.title()
    except Exception:  # noqa: BLE001 - the title is cosmetic, an empty one is shown
        log.debug("page title unavailable", exc_info=True)
        title = ""
    return {
        "session_id": s.id, "run_id": s.run_id, "workspace": s.workspace,
        "owner": s.owner, "label": s.label, "url": getattr(s.page, "url", "") or "",
        "title": title, "created_at": s.created_at, "last_used_at": s.last_used_at,
        "controlled_by": s.controller(),
        "read_only": s.policy.read_only,
    }


#: What a person may still do on a read only session's live view: move
#: around and look. Clicks and typing are writes (a form, a button).
_READ_ONLY_INPUTS = frozenset({"navigate", "back", "forward", "reload", "scroll", "mousemove"})


def _refuse_if_read_only(s: Session, what: str) -> None:
    if s.policy.read_only:
        raise HTTPException(status_code=403,
                            detail=f"this browser session only reads: {what} is not allowed")


def _agent_may_drive(s: Session) -> None:
    """The agent's navigate and act wait while a person holds control: 423
    (Locked), which the hub's tools turn into a wait and a retry. Reading and
    screenshots stay open, so the agent can see what the person is doing."""
    holder = s.controller()
    if holder:
        raise HTTPException(
            status_code=423,
            detail=f"{holder} has taken control of this browser; the page is theirs until they release it")


class Control(BaseModel):
    on: bool
    by: str = ""


@app.post("/sessions/{session_id}/control", dependencies=[Depends(require_token)])
async def control(session_id: str, body: Control) -> Dict[str, Any]:
    """Take or release control of a session for a person on the dashboard.
    While held, the agent's driving calls answer 423 and its tools wait."""
    s = _session(session_id)
    if body.on:
        s.set_control(body.by or "a person")
    else:
        s.controlled_by = ""
    return await describe(s)


@app.get("/sessions", dependencies=[Depends(require_token)])
async def list_sessions(run_id: str = "", workspace: str = "") -> Dict[str, Any]:
    """Every open session, newest first, optionally narrowed to one run or one
    workspace. Listing does not touch a session: only use keeps it alive."""
    out = []
    for s in sorted(state.sessions.values(), key=lambda x: x.created_at, reverse=True):
        if run_id and s.run_id != run_id:
            continue
        if workspace and s.workspace != workspace:
            continue
        out.append(await describe(s))
    return {"sessions": out}


@app.get("/sessions/{session_id}", dependencies=[Depends(require_token)])
async def get_session(session_id: str) -> Dict[str, Any]:
    return await describe(_session(session_id))


@app.patch("/sessions/{session_id}", dependencies=[Depends(require_token)])
async def patch_session(session_id: str, body: PatchSession) -> Dict[str, Any]:
    """Retag a session, for a hand-off: the page stays as it is, only who
    drives it and which run it belongs to change. The policy never changes."""
    s = _session(session_id)
    if body.run_id is not None:
        s.run_id = body.run_id.strip()
    if body.owner is not None:
        s.owner = body.owner
    if body.label is not None:
        s.label = body.label.strip()[:200]
    return await describe(s)


async def _navigate(s: Session, url: str) -> Dict[str, Any]:
    """Go to ``url`` in ``s``, the caller holding the lock. Shared by the
    agent's ``/navigate`` and a person's address bar, so both pass the same
    check before the page moves and the same check on where it landed."""
    ok, reason = await _checked(url, s.policy)
    if not ok:
        raise HTTPException(status_code=403, detail=f"refused {url}: {reason}")
    status: Optional[int] = None
    try:
        resp = await s.page.goto(url, wait_until="domcontentloaded")
        status = resp.status if resp is not None else None
    except Exception as exc:  # noqa: BLE001 - any navigation failure is returned to the caller as an HTTP error
        # A page script that navigates somewhere blocked interrupts goto;
        # the refusal is recorded by the route handler a moment later.
        await asyncio.sleep(0.3)
        blocked = s.take_blocked()
        if blocked:
            raise HTTPException(status_code=403, detail=f"refused {blocked[-1]['url']}: {blocked[-1]['reason']}")
        raise HTTPException(status_code=502, detail=f"navigation failed: {type(exc).__name__}: {exc}")
    refused = await _enforce_current_url(s)
    if refused:
        raise HTTPException(status_code=403, detail=f"refused final URL: {refused}")
    return {**await _page_info(s), "status": status}


@app.post("/sessions/{session_id}/navigate", dependencies=[Depends(require_token)])
async def navigate(session_id: str, body: Navigate) -> Dict[str, Any]:
    s = _session(session_id)
    _agent_may_drive(s)
    async with s.lock:
        return await _navigate(s, body.url)


@app.get("/sessions/{session_id}/read", dependencies=[Depends(require_token)])
async def read(session_id: str) -> Dict[str, Any]:
    s = _session(session_id)
    async with s.lock:
        html = await s.page.content()
        truncated = len(html) > MAX_HTML_CHARS
        return {**await _page_info(s), "html": html[:MAX_HTML_CHARS], "truncated": truncated}


async def perform(page: Any, action: str, selector: str, text: str) -> None:
    """Run one action on ``page``. Selectors are Playwright selectors: CSS by
    default, ``text=Sign in`` for visible text."""
    loc = page.locator(selector).first if selector else None
    if action == "click":
        if loc is None:
            raise ValueError("click needs a selector")
        await loc.click()
    elif action == "fill":
        if loc is None:
            raise ValueError("fill needs a selector")
        await loc.fill(text)
    elif action == "press":
        if not text:
            raise ValueError("press needs a key in text, e.g. Enter")
        if loc is not None:
            await loc.press(text)
        else:
            await page.keyboard.press(text)
    elif action == "select":
        if loc is None:
            raise ValueError("select needs a selector")
        try:
            await loc.select_option(label=text)
        except Exception:  # noqa: BLE001 - a label miss falls back to selecting by value
            log.debug("select by label failed, trying value", exc_info=True)
            await loc.select_option(value=text)
    elif action == "scroll":
        if loc is not None:
            await loc.scroll_into_view_if_needed()
            return
        where = (text or "down").strip().lower()
        if where == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif where == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            try:
                dy = int(where)
            except ValueError:
                dy = -800 if where == "up" else 800
            await page.mouse.wheel(0, dy)
    else:
        raise ValueError(f"unknown action {action!r}")


@app.post("/sessions/{session_id}/act", dependencies=[Depends(require_token)])
async def act(session_id: str, body: Act) -> Dict[str, Any]:
    s = _session(session_id)
    _refuse_if_read_only(s, body.action)
    _agent_may_drive(s)
    async with s.lock:
        try:
            await perform(s.page, body.action, body.selector.strip(), body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - any action failure is returned to the caller as an HTTP error
            raise HTTPException(status_code=422, detail=f"{body.action} failed: {type(exc).__name__}: {exc}")
        try:
            await s.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - a page that never settles is not an error, the frame shows the state
            log.debug("wait for load state failed", exc_info=True)
        refused = await _enforce_current_url(s)
        if refused:
            raise HTTPException(status_code=403, detail=f"refused the page the action led to: {refused}")
        return await _page_info(s)


@app.get("/sessions/{session_id}/frame", dependencies=[Depends(require_token)])
async def frame(session_id: str) -> Dict[str, Any]:
    """The viewport as a JPEG data URL, for the dashboard's live view.

    Always the full 1280x800 viewport: the UI scales the image and maps a
    click back through the size returned here, which keeps the coordinates of
    ``/input`` in viewport pixels whatever the screen. Taken under the lock,
    so a frame requested during an agent's action waits for it to finish and
    never shows a half-applied step. Each frame touches the session, which is
    what keeps a session someone is watching from being reaped.
    """
    s = _session(session_id)
    s.person_touch()
    async with s.lock:
        jpeg = await s.page.screenshot(type="jpeg", quality=60)
    return await _frame_message(s, jpeg)


async def _frame_message(s: Session, jpeg: bytes, *, seq: int = 0,
                         image_b64: Optional[str] = None) -> Dict[str, Any]:
    """One frame as the dashboard reads it, from JPEG bytes or from the
    base64 CDP already hands over."""
    try:
        title = await s.page.title()
    except Exception:  # noqa: BLE001 - the title is cosmetic, an empty one is shown
        log.debug("page title unavailable", exc_info=True)
        title = ""
    size = getattr(s.page, "viewport_size", None) or VIEWPORT
    return {
        "type": "frame", "seq": seq,
        "url": s.page.url, "title": title,
        "width": int(size.get("width") or VIEWPORT["width"]),
        "height": int(size.get("height") or VIEWPORT["height"]),
        "image": "data:image/jpeg;base64," + (image_b64 or base64.b64encode(jpeg).decode("ascii")),
        "controlled_by": s.controller(),
    }


# ── The frame stream ─────────────────────────────────────────────────────────

async def _screencast(s: Session, ws: WebSocket, seq: int) -> int:
    """Push frames through CDP's screencast until the page changes or the
    client goes away. Returns the last sequence number. Raises when CDP is not
    available for this page, which the caller answers with screenshots."""
    page = s.page
    cdp = await s.context.new_cdp_session(page)
    latest: asyncio.Queue = asyncio.Queue(maxsize=1)
    loop = asyncio.get_running_loop()

    def on_frame(params: Dict[str, Any]) -> None:
        # Acknowledge at once, or Chromium stops sending; keep only the
        # newest frame, a slow client must not build up a backlog.
        loop.create_task(cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}))
        if latest.full():
            try:
                latest.get_nowait()
            except asyncio.QueueEmpty:
                pass
        latest.put_nowait(params)

    cdp.on("Page.screencastFrame", on_frame)
    await cdp.send("Page.startScreencast", {
        "format": "jpeg", "quality": 60,
        "maxWidth": VIEWPORT["width"], "maxHeight": VIEWPORT["height"], "everyNthFrame": 1,
    })
    try:
        while s.page is page:
            try:
                params = await asyncio.wait_for(latest.get(), timeout=STREAM_KEEPALIVE)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "keepalive", "seq": seq, "url": page.url,
                                    "controlled_by": s.controller()})
                continue
            seq += 1
            s.touch()
            s.person_touch()
            await ws.send_json(await _frame_message(s, b"", seq=seq, image_b64=str(params.get("data") or "")))
        return seq
    finally:
        for call in ({"method": "Page.stopScreencast"},):
            try:
                await cdp.send(call["method"])
            except Exception:  # noqa: BLE001 - stopping a screencast on a closed page is expected
                log.debug("stop screencast failed", exc_info=True)
        try:
            await cdp.detach()
        except Exception:  # noqa: BLE001 - detaching from a closed page is expected
            log.debug("cdp detach failed", exc_info=True)


async def _screenshot_stream(s: Session, ws: WebSocket, seq: int) -> int:
    """The fallback: a screenshot every STREAM_FALLBACK_MS, sent when the
    picture changed, plus a keepalive when it has not for a while."""
    last: bytes = b""
    quiet = 0.0
    while True:
        async with s.lock:
            jpeg = await s.page.screenshot(type="jpeg", quality=60)
        if jpeg != last:
            last = jpeg
            seq += 1
            quiet = 0.0
            s.touch()
            s.person_touch()
            await ws.send_json(await _frame_message(s, jpeg, seq=seq))
        else:
            quiet += STREAM_FALLBACK_MS / 1000.0
            if quiet >= STREAM_KEEPALIVE:
                quiet = 0.0
                await ws.send_json({"type": "keepalive", "seq": seq, "url": s.page.url,
                                    "controlled_by": s.controller()})
        await asyncio.sleep(STREAM_FALLBACK_MS / 1000.0)


async def _stream_frames(s: Session, ws: WebSocket) -> None:
    seq = 0
    while True:
        try:
            seq = await _screencast(s, ws, seq)
            continue  # the page changed (a new tab): attach to the new one
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 - no CDP here (a test fake, an older Playwright)
            log.debug("screencast unavailable for %s (%s); sending screenshots", s.id, exc)
        await _screenshot_stream(s, ws, seq)
        return


@app.websocket("/sessions/{session_id}/stream")
async def stream(ws: WebSocket, session_id: str, token: str = "") -> None:
    """Frames as they happen, one JSON message each (the ``/frame`` shape plus
    ``type`` and ``seq``), with a keepalive on quiet pages. The token rides
    the Authorization header or, for a client that cannot set one, ``?token=``.
    A frame is a sign the person is watching: it keeps the session alive and,
    while they hold control, keeps that hold."""
    authorized = token_ok(ws.headers.get("authorization"), TOKEN) or (
        bool(token) and bool(TOKEN) and hmac.compare_digest(token.strip().encode(), TOKEN.encode()))
    if not authorized:
        await ws.close(code=1008, reason="missing or wrong token")
        return
    s = state.sessions.get(session_id)
    if s is None:
        await ws.close(code=1008, reason="no such session (closed or expired)")
        return
    await ws.accept()
    s.touch()

    async def _watch_client() -> None:
        # The only thing a client sends is its departure.
        while True:
            await ws.receive()

    sender = asyncio.create_task(_stream_frames(s, ws))
    watcher = asyncio.create_task(_watch_client())
    try:
        done, _pending = await asyncio.wait({sender, watcher}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                log.warning("frame stream for %s ended: %s", session_id, exc)
    finally:
        for task in (sender, watcher):
            task.cancel()
        for task in (sender, watcher):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the stream tasks were cancelled or already failed, both fine here
                log.debug("stream task ended", exc_info=True)


async def perform_input(page: Any, body: Input) -> None:
    """Replay one input a person made on the live view. Everything but
    ``navigate``, which goes through :func:`_navigate` and its policy check.
    Coordinates are viewport pixels."""
    kind = body.kind
    if kind == "click":
        await page.mouse.click(body.x, body.y)
    elif kind == "dblclick":
        await page.mouse.dblclick(body.x, body.y)
    elif kind == "mousemove":
        await page.mouse.move(body.x, body.y)
    elif kind == "type":
        if body.text:
            await page.keyboard.type(body.text)
    elif kind == "key":
        if not body.key:
            raise ValueError("key needs a key, e.g. Enter")
        await page.keyboard.press(body.key)
    elif kind == "scroll":
        await page.mouse.move(body.x, body.y)
        await page.mouse.wheel(body.dx, body.dy)
    elif kind == "back":
        await page.go_back(wait_until="domcontentloaded")
    elif kind == "forward":
        await page.go_forward(wait_until="domcontentloaded")
    elif kind == "reload":
        await page.reload(wait_until="domcontentloaded")
    else:
        raise ValueError(f"unknown input {kind!r}")


@app.post("/sessions/{session_id}/input", dependencies=[Depends(require_token)])
async def send_input(session_id: str, body: Input) -> Dict[str, Any]:
    """A click, keystroke, scroll or navigation from the dashboard. The page
    may move as a result (a link, a form), so the landing is re-checked
    exactly as after an agent's ``/act``."""
    s = _session(session_id)
    if body.kind not in _READ_ONLY_INPUTS:
        _refuse_if_read_only(s, body.kind)
    s.person_touch()
    async with s.lock:
        if body.kind == "navigate":
            return await _navigate(s, body.url.strip())
        try:
            await perform_input(s.page, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - input that cannot apply is not worth failing the request, the frame shows the result
            # Back with no history, a reload interrupted by a blocked
            # redirect: not worth failing the request, the frame shows it.
            log.debug("input %s failed: %s", body.kind, exc)
        try:
            await s.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - a page that never settles is not an error, the frame shows the state
            log.debug("wait for load state failed", exc_info=True)
        refused = await _enforce_current_url(s)
        if refused:
            raise HTTPException(status_code=403, detail=f"refused the page the input led to: {refused}")
        return await _page_info(s)


@app.get("/sessions/{session_id}/screenshot", dependencies=[Depends(require_token)])
async def screenshot(session_id: str, full_page: bool = False) -> Response:
    s = _session(session_id)
    async with s.lock:
        png = await s.page.screenshot(full_page=full_page, type="png")
    return Response(content=png, media_type="image/png")


@app.delete("/sessions/{session_id}", dependencies=[Depends(require_token)])
async def close(session_id: str) -> Dict[str, Any]:
    s = state.sessions.get(session_id)
    if s is not None:
        await _close_session(s)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("BROWSER_HOST", "0.0.0.0"),
                port=_env_int("BROWSER_PORT", 3000))

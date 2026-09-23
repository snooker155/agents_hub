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

Endpoints
    POST   /sessions                      {policy}            -> {session_id}
    POST   /sessions/{id}/navigate        {url}               -> {url, title, status}
    GET    /sessions/{id}/read                                -> {url, title, html, blocked}
    POST   /sessions/{id}/act             {action, selector, text} -> {url, title, blocked}
    GET    /sessions/{id}/screenshot?full_page=0              -> image/png
    DELETE /sessions/{id}
    GET    /healthz                                           (no token)

``read`` returns the rendered DOM as HTML, not text: the hub turns it into text
with the same ``tools.web.html_to_text`` ``fetch_url`` uses, so both tools hand
the agent identically extracted, identically annotated content.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urljoin, urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from policy import Policy, check_url

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
# Blocked requests remembered per session, reported back on the next call.
_BLOCKED_KEEP = 20


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

    def touch(self) -> None:
        self.last_used = time.monotonic()

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
        ok, reason = await _checked(url, session.policy)
        if not ok:
            session.note_blocked(url, reason)
            await route.abort("blockedbyclient")
            return
        try:
            response = await route.fetch(max_redirects=0, timeout=NAV_TIMEOUT_MS)
        except Exception as exc:
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


def _make_ws_handler(session: Session):
    async def handler(ws) -> None:
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
        viewport={"width": 1280, "height": 800},
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
    except Exception:
        pass


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
    except Exception:
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
    except Exception:
        pass
    return reason


# ── API ───────────────────────────────────────────────────────────────────────

class CreateSession(BaseModel):
    policy: Dict[str, Any] = Field(default_factory=dict)


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
    state.sessions[s.id] = s
    return {"session_id": s.id, "idle_timeout": IDLE_TIMEOUT}


@app.post("/sessions/{session_id}/navigate", dependencies=[Depends(require_token)])
async def navigate(session_id: str, body: Navigate) -> Dict[str, Any]:
    s = _session(session_id)
    async with s.lock:
        ok, reason = await _checked(body.url, s.policy)
        if not ok:
            raise HTTPException(status_code=403, detail=f"refused {body.url}: {reason}")
        status: Optional[int] = None
        try:
            resp = await s.page.goto(body.url, wait_until="domcontentloaded")
            status = resp.status if resp is not None else None
        except Exception as exc:
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
        except Exception:
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
    async with s.lock:
        try:
            await perform(s.page, body.action, body.selector.strip(), body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"{body.action} failed: {type(exc).__name__}: {exc}")
        try:
            await s.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except Exception:
            pass
        refused = await _enforce_current_url(s)
        if refused:
            raise HTTPException(status_code=403, detail=f"refused the page the action led to: {refused}")
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

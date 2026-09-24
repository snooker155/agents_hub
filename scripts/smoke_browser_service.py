#!/usr/bin/env python
"""
Live smoke check of the browser service (deploy/browser, docs/browser.md).

Drives a running service through what the dashboard and an agent do with one
session, with a real Chromium behind it: open a session, navigate, receive a
frame over the WebSocket screencast, have a person take control, see the
agent's navigate refused with 423 while the hold lasts, watch the hold lapse
after ``BROWSER_CONTROL_IDLE`` seconds without a frame or an input, release,
and close. Every step prints a line; the first failure exits non-zero with the
service's answer.

Start the service with a short idle window so the expiry step is quick::

    cd deploy/browser && BROWSER_PORT=3001 BROWSER_TOKEN=smoke BROWSER_CONTROL_IDLE=3 \\
        PYTHONPATH=$(git rev-parse --show-toplevel) python app.py
    python scripts/smoke_browser_service.py --url http://127.0.0.1:3001 --token smoke \\
        --control-idle 3

The page it opens is ``https://example.com`` by default (``--page``): the
service refuses private networks, so a local test page would be blocked,
which is the policy working rather than the check.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx


def step(name: str, detail: str = "") -> None:
    print(f"[ok]   {name}" + (f": {detail}" if detail else ""), flush=True)


def fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}", flush=True)
    sys.exit(1)


async def first_frame(ws_url: str, token: str, timeout: float) -> dict:
    """Open the screencast and return the first real frame (not a keepalive)."""
    import websockets
    async with websockets.connect(ws_url, additional_headers={"Authorization": f"Bearer {token}"},
                                  max_size=16 * 1024 * 1024, open_timeout=10) as ws:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            msg = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
            if msg.get("image"):
                return msg
    fail("stream", f"no frame within {timeout:.0f}s")
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("AGENTS_HUB_BROWSER_URL") or "http://127.0.0.1:3000")
    ap.add_argument("--token", default=os.environ.get("AGENTS_HUB_BROWSER_TOKEN")
                    or os.environ.get("BROWSER_TOKEN") or "")
    ap.add_argument("--page", default="https://example.com/")
    ap.add_argument("--control-idle", type=float, default=float(os.environ.get("BROWSER_CONTROL_IDLE") or 120),
                    help="the service's BROWSER_CONTROL_IDLE, so the expiry step knows how long to wait")
    args = ap.parse_args()
    if not args.token:
        fail("setup", "a token is required (--token or AGENTS_HUB_BROWSER_TOKEN)")
    base = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"}
    http = httpx.Client(base_url=base, headers=headers, timeout=60.0)
    started = time.time()

    health = http.get("/healthz")
    if health.status_code != 200:
        fail("health", f"HTTP {health.status_code}: {health.text[:200]}")
    step("health", health.text)

    created = http.post("/sessions", json={"policy": {}, "run_id": "smoke", "workspace": "smoke",
                                           "owner": "agent", "label": "smoke check"})
    if created.status_code != 200:
        fail("open", f"HTTP {created.status_code}: {created.text[:200]}")
    sid = created.json()["session_id"]
    step("open", f"session {sid}")
    session_id = sid

    try:
        nav = http.post(f"/sessions/{sid}/navigate", json={"url": args.page})
        if nav.status_code != 200:
            fail("navigate", f"HTTP {nav.status_code}: {nav.text[:200]}")
        step("navigate", f"{nav.json().get('url')} ({nav.json().get('title')!r})")

        read = http.get(f"/sessions/{sid}/read")
        if read.status_code != 200 or not read.json().get("html"):
            fail("read", f"HTTP {read.status_code}: {read.text[:200]}")
        step("read", f"{len(read.json()['html'])} chars of HTML")

        ws_base = "ws" + base[len("http"):]
        frame = asyncio.run(first_frame(f"{ws_base}/sessions/{sid}/stream", args.token, timeout=20))
        step("stream", f"frame {frame.get('width')}x{frame.get('height')}, "
                       f"{len(frame.get('image') or '')} bytes of base64 JPEG, type={frame.get('type')}")

        taken = http.post(f"/sessions/{sid}/control", json={"on": True, "by": "smoke person"})
        if taken.status_code != 200 or taken.json().get("controlled_by") != "smoke person":
            fail("control", f"HTTP {taken.status_code}: {taken.text[:200]}")
        step("control", "taken by smoke person")

        locked = http.post(f"/sessions/{sid}/navigate", json={"url": args.page})
        if locked.status_code != 423:
            fail("agent locked out", f"expected 423, got HTTP {locked.status_code}: {locked.text[:200]}")
        step("agent locked out", f"423: {locked.json().get('detail')}")

        still = http.get(f"/sessions/{sid}/read")
        if still.status_code != 200:
            fail("agent reads while locked", f"HTTP {still.status_code}: {still.text[:200]}")
        step("agent reads while locked", "read stays open")

        typed = http.post(f"/sessions/{sid}/input", json={"kind": "mousemove", "x": 10, "y": 10})
        if typed.status_code != 200:
            fail("person input", f"HTTP {typed.status_code}: {typed.text[:200]}")
        step("person input", "mousemove accepted and refreshed the hold")

        wait = args.control_idle + 1.5
        print(f"       waiting {wait:.1f}s for the hold to lapse", flush=True)
        time.sleep(wait)
        info = http.get(f"/sessions/{sid}")
        if info.status_code != 200 or info.json().get("controlled_by"):
            fail("hold expiry", f"still held: HTTP {info.status_code}: {info.text[:200]}")
        freed = http.post(f"/sessions/{sid}/navigate", json={"url": args.page})
        if freed.status_code != 200:
            fail("hold expiry", f"agent still locked out: HTTP {freed.status_code}: {freed.text[:200]}")
        step("hold expiry", f"released after {args.control_idle:.0f}s idle, agent navigates again")

        http.post(f"/sessions/{sid}/control", json={"on": True, "by": "smoke person"})
        released = http.post(f"/sessions/{sid}/control", json={"on": False})
        if released.status_code != 200 or released.json().get("controlled_by"):
            fail("release", f"HTTP {released.status_code}: {released.text[:200]}")
        step("release", "explicit release clears the hold")
    finally:
        closed = http.delete(f"/sessions/{session_id}")
        if closed.status_code not in (200, 204):
            fail("close", f"HTTP {closed.status_code}: {closed.text[:200]}")
        step("close", f"session {session_id} closed")
    print(f"all steps passed in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()

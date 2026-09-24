"""
Request throttles: a sliding window per key, and the limits the hub applies
with it (docs/api-keys.md "Rate limits", docs/nodes.md, docs/hub-as-provider.md).

Three callers share one implementation:

* the token-in-path external route (``POST /api/external/{token}/run``),
  throttled per client address so a leaked or guessed token cannot flood a
  node and token guessing is slow;
* the guard in ``dashboard/backend/main.py``, which limits ``/api`` and
  ``/v1`` requests per minute per principal (a personal key, a person, the
  shared token);
* ``POST /v1/chat/completions``, which refuses a caller that already used its
  tokens for the day (counted from ``serving_usage``).

Requests per minute live in memory, in this process: with several API
replicas each keeps its own window, so the effective limit is the setting
times the number of replicas. That is deliberate. A shared counter would put
a database or Redis round trip on every request, and the point of the limit
is to stop a runaway client, not to meter it exactly. Tokens per day come
from the database and so hold across replicas, cached for
:data:`TOKENS_CACHE_SECONDS` to keep ``/v1`` cheap.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, Optional, Tuple

#: How long a tokens-used-today figure and a key's own limits are reused.
TOKENS_CACHE_SECONDS = 30.0
KEY_LIMITS_CACHE_SECONDS = 30.0
#: Past this many tracked keys a check sweeps out the idle ones, so a flood of
#: distinct addresses cannot grow the table without bound.
MAX_KEYS = 10_000


class SlidingWindow:
    """At most ``limit`` hits per ``window_seconds`` per key.

    Every allowed hit is remembered with its time; a check forgets the hits
    older than the window and refuses when ``limit`` remain. A refused hit is
    not remembered, so a client that keeps retrying does not push its own
    recovery further out. Thread safe; ``clock`` is injectable for tests.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = MAX_KEYS) -> None:
        self.clock = clock
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> Tuple[bool, int]:
        """``(allowed, retry_after)``: seconds until a hit would be allowed
        again, 0 when allowed. A limit of 0 or less never refuses."""
        if not limit or limit <= 0:
            return True, 0
        now = self.clock()
        horizon = now - window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._sweep(horizon)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= horizon:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, math.ceil(hits[0] + window_seconds - now))
            hits.append(now)
            return True, 0

    def _sweep(self, horizon: float) -> None:
        for k in [k for k, h in self._hits.items() if not h or h[-1] <= horizon]:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


#: The process-wide windows. Separate instances so an external caller's
#: address and a principal id can never collide.
external_window = SlidingWindow()
request_window = SlidingWindow()


def reset() -> None:
    """Forget every window and cache (tests)."""
    external_window.reset()
    request_window.reset()
    with _cache_lock:
        _tokens_cache.clear()
        _key_limits_cache.clear()


# ── limits ───────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_tokens_cache: Dict[Tuple[Optional[str], Optional[str]], Tuple[float, int]] = {}
_key_limits_cache: Dict[str, Tuple[float, Dict[str, Optional[int]]]] = {}


def _setting(name: str) -> int:
    from common.config import settings
    try:
        return max(0, int(getattr(settings, name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _key_id(principal: Any) -> Optional[str]:
    if principal is not None and getattr(principal, "via", "") == "api_key":
        return getattr(principal, "credential_id", "") or None
    return None


def key_limits(key_id: str) -> Dict[str, Optional[int]]:
    """A personal key's own ``rate_limit_per_minute`` and ``tokens_per_day``
    (None where the key sets nothing), cached briefly."""
    now = time.monotonic()
    with _cache_lock:
        hit = _key_limits_cache.get(key_id)
        if hit and now - hit[0] < KEY_LIMITS_CACHE_SECONDS:
            return hit[1]
    from common import api_keys
    record = api_keys.get_key(key_id) or {}
    limits = {"rate_limit_per_minute": record.get("rate_limit_per_minute"),
              "tokens_per_day": record.get("tokens_per_day")}
    with _cache_lock:
        _key_limits_cache[key_id] = (now, limits)
    return limits


def _limit_for(principal: Any, key_field: str, setting: str) -> int:
    key_id = _key_id(principal)
    if key_id:
        own = key_limits(key_id).get(key_field)
        if own is not None:
            return max(0, int(own))
    return _setting(setting)


def principal_key(principal: Any) -> Optional[str]:
    """What a principal's request count is kept under: the key id for a
    personal key, else the principal id (a person, the shared token, the
    local operator). None for no principal and for the hub's own service
    credential, which is never limited."""
    if principal is None or getattr(principal, "kind", "") == "service":
        return None
    key_id = _key_id(principal)
    if key_id:
        return f"key:{key_id}"
    pid = getattr(principal, "id", None)
    return f"principal:{pid}" if pid else None


def check_request(principal: Any) -> Tuple[bool, int]:
    """Requests per minute for one ``/api`` or ``/v1`` request."""
    key = principal_key(principal)
    if key is None:
        return True, 0
    limit = _limit_for(principal, "rate_limit_per_minute", "rate_limit_per_minute")
    return request_window.check(key, limit, 60.0)


def check_external(client_ip: Optional[str]) -> Tuple[bool, int]:
    """Requests per minute for the external node route, per client address."""
    return external_window.check(f"ip:{client_ip or 'unknown'}",
                                 _setting("external_rate_per_minute"), 60.0)


def seconds_until_utc_midnight(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((tomorrow - now).total_seconds()))


def check_tokens_per_day(principal: Any) -> Tuple[bool, int]:
    """Whether a ``/v1`` caller may still spend tokens today.

    Refused once what it used since 00:00 UTC reaches the cap, with
    ``retry_after`` the seconds until the next midnight. A personal key is
    counted on its own usage; anyone else on their user id.
    """
    if principal is None or getattr(principal, "kind", "") == "service":
        return True, 0
    cap = _limit_for(principal, "tokens_per_day", "rate_limit_tokens_per_day")
    if cap <= 0:
        return True, 0
    key_id = _key_id(principal)
    user_id = None if key_id else getattr(principal, "id", None)
    cache_key = (user_id, key_id)
    now = time.monotonic()
    with _cache_lock:
        hit = _tokens_cache.get(cache_key)
    if hit and now - hit[0] < TOKENS_CACHE_SECONDS:
        used = hit[1]
    else:
        from common import serving
        used = serving.tokens_today(user_id=user_id, key_id=key_id)
        with _cache_lock:
            _tokens_cache[cache_key] = (now, used)
    if used >= cap:
        return False, seconds_until_utc_midnight()
    return True, 0


__all__ = [
    "SlidingWindow", "check_external", "check_request", "check_tokens_per_day",
    "external_window", "key_limits", "principal_key", "request_window", "reset",
    "seconds_until_utc_midnight",
]

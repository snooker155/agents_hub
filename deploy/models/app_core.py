"""Settings, environment, download jobs, call usage counts and the Hugging Face file fetcher."""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_settings
import httpx
from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

log = logging.getLogger("models_service")


_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_REVISION_RE = re.compile(r"^[\w.\-/]{1,100}$")
_QUANT_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(IQ\d_[A-Z0-9]+(?:_[A-Z0-9]+)?|Q\d_K(?:_[SML])?|Q\d_\d|"
                       r"Q\d_K|BF16|F16|F32)(?![A-Za-z0-9])")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Jobs ─────────────────────────────────────────────────────────────────────

def _jobs_file() -> Path:
    """Where the job list lives between restarts: beside the models, hidden."""
    return app_settings.MODELS_DIR / ".jobs.json"


#: A job's progress is written to disk at most this often; the terminal
#: states and a status change are written at once.
_JOBS_WRITE_INTERVAL = 0.5


class Jobs:
    """Downloads and their progress; the same job shape the hub's own
    registry uses (providers/local_models.py), so the UI reads both alike.

    The list is kept in a JSON file next to the models, so a restart of the
    service does not lose what was downloading. A job that was running when
    the process died comes back as ``error`` with ``resumable`` set: its
    ``.part`` file is still there, and asking for the same download again
    continues from where it stopped (see :func:`run_download`).
    """

    def __init__(self, keep: int = app_settings.JOBS_KEPT) -> None:
        self.keep = keep
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._last_write = 0.0

    def create(self, kind: str, name: str, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "name": name, "status": "queued",
               "completed": 0, "total": 0, "percent": 0.0, "message": "", "error": None,
               "resumable": False, "meta": dict(meta or {}),
               "started_at": _now(), "finished_at": None}
        with self._lock:
            self._jobs[job["id"]] = job
            while len(self._jobs) > self.keep:
                victim = next((k for k, j in self._jobs.items()
                               if j["status"] in ("done", "error")), None)
                if victim is None:
                    break
                self._jobs.pop(victim)
            self._write(force=True)
        return dict(job)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            status_changed = "status" in fields and fields["status"] != job.get("status")
            job.update(fields)
            self._write(force=status_changed or job.get("status") in ("done", "error"))

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in reversed(self._jobs.values())]

    def running_for(self, dest_name: str) -> Optional[Dict[str, Any]]:
        """The queued or running download of ``dest_name``, if any."""
        with self._lock:
            for j in self._jobs.values():
                if (j["kind"] in ("hf_download", "hf_package") and j["status"] in ("queued", "running")
                        and (j.get("meta") or {}).get("dest") == dest_name):
                    return dict(j)
        return None

    # The lock is held by every caller of the two below.

    def _write(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < _JOBS_WRITE_INTERVAL:
            return
        self._last_write = now
        path = _jobs_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(list(self._jobs.values())), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # a full disk must not fail the job it records
            log.warning("could not write %s: %s", path, exc)

    def load(self) -> int:
        """Read the list back after a start. Whatever was still queued or
        running belongs to a process that is gone: it is closed as an error,
        and marked resumable when its partial file survived."""
        path = _jobs_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError) as exc:
            log.warning("could not read %s: %s", path, exc)
            return 0
        interrupted = 0
        with self._lock:
            self._jobs.clear()
            for j in raw if isinstance(raw, list) else []:
                if not isinstance(j, dict) or not j.get("id"):
                    continue
                j.setdefault("resumable", False)
                j.setdefault("meta", {})
                if j.get("status") in ("queued", "running"):
                    dest = (j.get("meta") or {}).get("dest") or ""
                    part = app_settings.MODELS_DIR / f"{dest}.part" if dest else None
                    staged = _staging_dir(dest) if dest and j.get("kind") == "hf_package" else None
                    j["status"] = "error"
                    j["finished_at"] = _now()
                    if staged is not None and staged.is_dir():
                        j["resumable"] = True
                        j["error"] = "interrupted by a restart; download it again to resume"
                    elif part is not None and part.is_file():
                        j["resumable"] = True
                        j["error"] = (f"interrupted by a restart at {part.stat().st_size} bytes; "
                                      "download it again to resume")
                    else:
                        j["error"] = "interrupted by a restart"
                    interrupted += 1
                self._jobs[str(j["id"])] = j
            if interrupted:
                self._write(force=True)
        return interrupted


jobs = Jobs()


# ── Usage ────────────────────────────────────────────────────────────────────

def _usage_file() -> Path:
    """Where the call counts live between restarts: beside the jobs."""
    return app_settings.MODELS_DIR / ".usage.json"


#: The header the hub names the caller with: ``hub`` (its agents and its own
#: work), ``endpoint`` (a call to the hub's /v1 from outside), ``voice`` (the
#: assistant's speech). A call without it is counted as ``direct``.
SOURCE_HEADER = "x-hub-source"
_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")

#: The gateway path a call came in on, and the kind it is counted under.
_KIND_OF_PATH = {"/v1/chat/completions": "chat", "/v1/completions": "chat",
                 "/v1/embeddings": "embeddings", "/v1/audio/speech": "speech",
                 "/v1/audio/transcriptions": "transcription",
                 "/v1/images/generations": "image", "/v1/images/edits": "image"}
#: Kinds whose answers carry no token counts: audio and pictures.
_UNCOUNTED_KINDS = ("speech", "image")

#: The counts are written at most this often; the shutdown writes the rest.
_USAGE_WRITE_INTERVAL = 2.0
#: How much of an answer's end is kept to find its token counts in.
_USAGE_TAIL_BYTES = 32 * 1024
_USAGE_RECENT = 30

_USAGE_KEY_RE = re.compile(rb'"(usage|timings)"\s*:\s*\{')
_JSON = json.JSONDecoder()
#: A stream chunk that carries the first written word: text, reasoning or a
#: tool call (an opening chunk with the role alone does not count).
_FIRST_OUTPUT_RE = re.compile(rb'"(?:content|reasoning_content|reasoning)"\s*:\s*"[^"]|"tool_calls"\s*:\s*\[')


def call_source(value: Optional[str]) -> str:
    v = str(value or "").strip().lower()
    return v if _SOURCE_RE.match(v) else "direct"


def _usage_objects(tail: bytes) -> Dict[str, Dict[str, Any]]:
    """The last ``usage`` and ``timings`` objects in ``tail``, nested ones
    whole (``prompt_tokens_details`` sits inside ``usage``)."""
    found: Dict[str, Dict[str, Any]] = {}
    # Latin-1 keeps byte offsets as character offsets; the two objects hold
    # numbers only, so no text inside them is misread.
    text = tail.decode("latin-1")
    for m in _USAGE_KEY_RE.finditer(tail):
        try:
            obj, _end = _JSON.raw_decode(text, m.end() - 1)
        except ValueError:
            continue
        if isinstance(obj, dict):
            found[m.group(1).decode()] = obj
    return found


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def gen_timing(tail: bytes) -> Tuple[int, float]:
    """Tokens written and the milliseconds it took, from llama-server's
    ``timings`` (``predicted_n``, ``predicted_ms``); (0, 0) without them.
    What the speed estimates are calibrated with."""
    timings = _usage_objects(tail).get("timings") or {}
    return int(_num(timings.get("predicted_n"))), _num(timings.get("predicted_ms"))


def call_stats(tail: bytes) -> Dict[str, Any]:
    """What one answer says about its prompt, read off its end (plain JSON or
    an event stream).

    ``prompt_tokens`` is the whole prompt and ``cached_tokens`` the part the
    engine took from its prompt cache instead of computing it: OpenAI's
    ``usage.prompt_tokens_details.cached_tokens`` (llama-server and mlx-lm
    both send it), else llama-server's ``timings`` (``cache_n``, and
    ``prompt_n`` the tokens it did compute). ``prefill_ms`` is how long that
    computing took, which only llama-server reports (None otherwise)."""
    found = _usage_objects(tail)
    usage, timings = found.get("usage") or {}, found.get("timings") or {}
    prompt = int(_num(usage.get("prompt_tokens")))
    completion = int(_num(usage.get("completion_tokens")))
    details = usage.get("prompt_tokens_details")
    cached = int(_num(details.get("cached_tokens"))) if isinstance(details, dict) else 0
    if timings:
        cached = cached or int(_num(timings.get("cache_n")))
        prompt = prompt or int(_num(timings.get("prompt_n"))) + int(_num(timings.get("cache_n")))
        completion = completion or int(_num(timings.get("predicted_n")))
    prefill_ms = _num(timings.get("prompt_ms")) if "prompt_ms" in timings else None
    computed = int(_num(timings.get("prompt_n"))) if "prompt_n" in timings else max(0, prompt - cached)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "cached_tokens": min(cached, prompt) if prompt else cached,
            "computed_tokens": computed, "prefill_ms": prefill_ms}


def tokens_in(tail: bytes) -> Tuple[int, int]:
    """Prompt and completion tokens from the end of an answer, plain JSON or
    an event stream: the last ``usage`` object (OpenAI's shape), else the
    last ``timings`` (llama-server's own, on every final chunk). (0, 0) when
    the answer names neither."""
    stats = call_stats(tail)
    return stats["prompt_tokens"], stats["completion_tokens"]


#: Width of one bucket of the cache's history, and how many are kept (a day).
SERIES_BUCKET_SECONDS = 300
SERIES_BUCKETS = 288
_SERIES_FIELDS = ("requests", "prompt_tokens", "cached_tokens", "computed_tokens", "prefill_ms",
                  "ttft_ms", "ttft_n")


class Usage:
    """Every call through the gateway, counted per model, caller and kind,
    with the last few kept whole. The hub's agents, its /v1 endpoint and the
    assistant's voice all come through here, so this is the one place the
    runtime's whole load is seen; the hub labels its calls with
    :data:`SOURCE_HEADER`.

    Each row also says how the prompt cache did for its calls: how many
    prompt tokens came from the cache (``cached_tokens``) and how many were
    computed (``computed_tokens``), how long computing took where the engine
    says (``prefill_ms`` over ``prefill_tokens``), and how long a streamed
    answer took to its first word (``ttft_ms`` over ``ttft_n`` calls). The
    same figures for all models together go into ``series``, one bucket per
    :data:`SERIES_BUCKET_SECONDS`, for the last day."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_write = 0.0
        self._dirty = False
        self._reset()

    def _reset(self) -> None:
        self.since = _now()
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.recent: List[Dict[str, Any]] = []
        self.series: List[Dict[str, Any]] = []

    def record(self, *, model: str, source: str, kind: str, ok: bool, code: int,
               duration_ms: int, prompt_tokens: int = 0, completion_tokens: int = 0,
               gen_tokens: int = 0, gen_ms: float = 0.0, cached_tokens: int = 0,
               computed_tokens: Optional[int] = None, prefill_ms: Optional[float] = None,
               ttft_ms: Optional[float] = None) -> None:
        key = f"{model}\x1f{source}\x1f{kind}"
        computed = max(0, prompt_tokens - cached_tokens) if computed_tokens is None else computed_tokens
        with self._lock:
            row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = {"model": model, "source": source, "kind": kind, "requests": 0,
                                        "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                        "duration_ms": 0, "gen_tokens": 0, "gen_ms": 0.0}
            row["requests"] += 1
            row["errors"] += 0 if ok else 1
            row["prompt_tokens"] += prompt_tokens
            row["completion_tokens"] += completion_tokens
            row["duration_ms"] += duration_ms
            row["gen_tokens"] = row.get("gen_tokens", 0) + gen_tokens
            row["gen_ms"] = round(row.get("gen_ms", 0.0) + gen_ms, 1)
            row["cached_tokens"] = row.get("cached_tokens", 0) + cached_tokens
            row["computed_tokens"] = row.get("computed_tokens", 0) + (computed if prompt_tokens else 0)
            row["hits"] = row.get("hits", 0) + (1 if cached_tokens > 0 else 0)
            row["prompt_calls"] = row.get("prompt_calls", 0) + (1 if prompt_tokens else 0)
            if prefill_ms is not None:
                row["prefill_ms"] = round(row.get("prefill_ms", 0.0) + prefill_ms, 1)
                row["prefill_tokens"] = row.get("prefill_tokens", 0) + computed
            if ttft_ms is not None:
                row["ttft_ms"] = round(row.get("ttft_ms", 0.0) + ttft_ms, 1)
                row["ttft_n"] = row.get("ttft_n", 0) + 1
            self._bucket({"requests": 1, "prompt_tokens": prompt_tokens, "cached_tokens": cached_tokens,
                          "computed_tokens": computed if prompt_tokens else 0,
                          "prefill_ms": prefill_ms or 0.0, "ttft_ms": ttft_ms or 0.0,
                          "ttft_n": 1 if ttft_ms is not None else 0})
            self.recent.append({"at": _now(), "model": model, "source": source, "kind": kind,
                                "status": "ok" if ok else "error", "code": code,
                                "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                "cached_tokens": cached_tokens, "duration_ms": duration_ms,
                                "ttft_ms": None if ttft_ms is None else round(ttft_ms)})
            del self.recent[:-_USAGE_RECENT]
            self._dirty = True
            self._write()

    def _bucket(self, add: Dict[str, Any]) -> None:
        start = int(time.time()) // SERIES_BUCKET_SECONDS * SERIES_BUCKET_SECONDS
        last = self.series[-1] if self.series else None
        if last is None or last.get("t") != start:
            last = {"t": start, **{f: 0 for f in _SERIES_FIELDS}}
            self.series.append(last)
            oldest = start - SERIES_BUCKET_SECONDS * (SERIES_BUCKETS - 1)
            self.series[:] = [b for b in self.series if b.get("t", 0) >= oldest]
        for f in _SERIES_FIELDS:
            last[f] = round(last.get(f, 0) + add.get(f, 0), 1)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            rows = sorted((dict(r) for r in self.rows.values()),
                          key=lambda r: (-r["requests"], r["model"], r["source"]))
            recent = [dict(r) for r in reversed(self.recent)]
            series = [dict(b) for b in self.series]
            since = self.since
        totals = {k: sum(r.get(k, 0) for r in rows)
                  for k in ("requests", "errors", "prompt_tokens", "completion_tokens", "duration_ms",
                            "cached_tokens", "computed_tokens", "hits", "prompt_calls", "prefill_ms",
                            "prefill_tokens", "ttft_ms", "ttft_n")}
        return {"since": since, "totals": totals, "rows": rows, "recent": recent, "series": series,
                "bucket_seconds": SERIES_BUCKET_SECONDS}

    def clear(self) -> None:
        with self._lock:
            self._reset()
            self._dirty = True
            self._write(force=True)

    # The lock is held by every caller of the two below, except load at start.

    def _write(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not self._dirty or (not force and now - self._last_write < _USAGE_WRITE_INTERVAL):
            return
        self._last_write = now
        path = _usage_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"since": self.since, "rows": list(self.rows.values()),
                                       "recent": self.recent, "series": self.series}), encoding="utf-8")
            tmp.replace(path)
            self._dirty = False
        except OSError as exc:  # a full disk must not fail the call it counts
            log.warning("could not write %s: %s", path, exc)

    def flush(self) -> None:
        with self._lock:
            self._write(force=True)

    def load(self) -> None:
        path = _usage_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError) as exc:
            log.warning("could not read %s: %s", path, exc)
            return
        if not isinstance(raw, dict):
            return
        with self._lock:
            self._reset()
            self.since = str(raw.get("since") or self.since)
            for r in raw.get("rows") or []:
                if isinstance(r, dict) and r.get("model"):
                    self.rows[f"{r['model']}\x1f{r.get('source')}\x1f{r.get('kind')}"] = r
            self.recent = [r for r in raw.get("recent") or [] if isinstance(r, dict)][-_USAGE_RECENT:]
            self.series = [b for b in raw.get("series") or []
                           if isinstance(b, dict) and isinstance(b.get("t"), int)][-SERIES_BUCKETS:]


usage = Usage()


def metered(request: Request, path: str, model: str, response: Response, started: float,
            *, streamed: bool = False) -> Response:
    """``response`` counted in :data:`usage` once it is over: a streamed
    answer when its last byte has gone out, with the token counts read off
    its end; any other answer (an error the gateway gave itself) at once.
    For an answer the caller asked to be ``streamed``, the time to its first
    written word is counted too."""
    source = call_source(request.headers.get(SOURCE_HEADER))
    kind = _KIND_OF_PATH.get(path, "chat")
    first: List[float] = []

    def done(code: int, tail: bytes) -> None:
        counted = kind not in _UNCOUNTED_KINDS and code < 400
        stats = call_stats(tail) if counted else {}
        gen_n, gen_ms = gen_timing(tail) if counted else (0, 0.0)
        usage.record(model=model or "(none)", source=source, kind=kind, ok=code < 400, code=code,
                     duration_ms=int((time.monotonic() - started) * 1000),
                     prompt_tokens=stats.get("prompt_tokens", 0),
                     completion_tokens=stats.get("completion_tokens", 0),
                     cached_tokens=stats.get("cached_tokens", 0),
                     computed_tokens=stats.get("computed_tokens"),
                     prefill_ms=stats.get("prefill_ms"),
                     ttft_ms=first[0] if first and counted else None,
                     gen_tokens=gen_n, gen_ms=gen_ms)

    if not isinstance(response, StreamingResponse):
        done(response.status_code, bytes(getattr(response, "body", b"") or b"")[-_USAGE_TAIL_BYTES:])
        return response
    inner = response.body_iterator
    code = response.status_code
    timed = streamed and kind == "chat"

    async def counted() -> Any:
        tail = b""
        try:
            async for piece in inner:
                if kind not in _UNCOUNTED_KINDS:
                    raw = piece if isinstance(piece, bytes) else str(piece).encode()
                    tail = (tail + raw)[-_USAGE_TAIL_BYTES:]
                    if timed and not first and _FIRST_OUTPUT_RE.search(raw):
                        first.append((time.monotonic() - started) * 1000)
                yield piece
        finally:
            done(code, tail)

    response.body_iterator = counted()
    return response


def http_client(**kwargs: Any) -> httpx.Client:
    """The client downloads and Hub lookups use; a test swaps it for one on a
    MockTransport."""
    return httpx.Client(follow_redirects=True, **kwargs)


def _hf_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {app_settings.HF_TOKEN}"} if app_settings.HF_TOKEN else {}


def _part_meta_path(part: Path) -> Path:
    return part.with_name(part.name + ".json")


def _read_part_meta(part: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_part_meta_path(part).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_part_meta(part: Path, meta: Dict[str, Any]) -> None:
    try:
        _part_meta_path(part).write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        pass


def _discard_part(part: Path) -> None:
    for p in (part, _part_meta_path(part)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _content_range_total(value: str) -> int:
    # "bytes 1000-4999/5000" -> 5000; "*" when the server does not know.
    tail = (value or "").rsplit("/", 1)[-1].strip()
    return int(tail) if tail.isdigit() else 0


class _FetchError(Exception):
    """A file that did not arrive whole; ``kept`` is how many bytes of its
    ``.part`` stay on disk for a resume (0: nothing to resume)."""

    def __init__(self, message: str, kept: int = 0) -> None:
        super().__init__(message)
        self.kept = kept


def _fetch(job_id: str, url: str, dest: Path, *, offset: int = 0, grand_total: int = 0) -> int:
    """Stream ``url`` into ``dest``: written to ``<dest>.part`` and renamed only
    when complete, so a half file never shows up as a model. Returns the
    file's size; raises :class:`_FetchError`.

    A ``.part`` left by an earlier attempt is continued, not restarted: the
    request carries ``Range`` from its size and ``If-Range`` with the ETag
    the first answer gave (kept in ``<dest>.part.json``), so a file that
    changed on the Hub in the meantime comes back whole (200) instead of
    being glued to the old bytes. A failure short of a 4xx keeps the part
    for the next attempt.

    ``offset`` and ``grand_total`` place this file inside a download of
    several (a speech model), so the job's progress covers all of them.
    """
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.is_file() else 0
    meta = _read_part_meta(part) if existing else {}
    jobs.update(job_id, status="running",
                message=(f"resuming {dest.name} from {existing} bytes" if existing
                         else f"downloading {dest.name}"))
    keep_part = False

    def progress(done: int, total: int) -> Dict[str, Any]:
        whole = grand_total or total
        at = offset + done
        return {"completed": at, "total": whole,
                "percent": round(100.0 * min(at, whole) / whole, 1) if whole else 0.0}

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = _hf_headers()
        if existing:
            headers["Range"] = f"bytes={existing}-"
            if meta.get("etag"):
                headers["If-Range"] = str(meta["etag"])
        timeout = httpx.Timeout(30.0, read=300.0)
        with http_client(timeout=timeout) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416 and existing:
                    # Past the end: the part is the whole file, or the file
                    # shrank. Either way a fresh whole download settles it.
                    _discard_part(part)
                    return _fetch(job_id, url, dest, offset=offset, grand_total=grand_total)
                if resp.status_code >= 400:
                    hint = " (a gated repo needs HF_TOKEN)" if resp.status_code in (401, 403) else ""
                    raise RuntimeError(f"Hugging Face answered HTTP {resp.status_code}{hint}")
                resumed = resp.status_code == 206 and existing > 0
                length = int(resp.headers.get("content-length") or 0)
                if resumed:
                    total = _content_range_total(resp.headers.get("content-range", "")) or (existing + length)
                    done = existing
                    mode = "ab"
                else:
                    # 200: the server ignored the range or the file changed.
                    total = length
                    done = 0
                    mode = "wb"
                    existing = 0
                etag = resp.headers.get("etag") or meta.get("etag") or ""
                _write_part_meta(part, {"url": url, "etag": etag, "total": total})
                jobs.update(job_id, **progress(done, total),
                            message=(f"resumed {dest.name} at {existing} bytes" if resumed
                                     else f"downloading {dest.name}"))
                last = 0.0
                keep_part = True
                with open(part, mode) as fh:
                    # Chunks as they arrive, not batched up to a size: what
                    # reached the disk before a drop is what a resume keeps.
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.5:
                            last = now
                            jobs.update(job_id, **progress(done, total))
        if total and done != total:
            raise RuntimeError(f"download ended at {done} of {total} bytes")
        part.replace(dest)
        _discard_part(part)
        return done
    except _FetchError:
        raise
    except Exception as exc:  # noqa: BLE001 - the caller records the failure on the job
        kept = part.stat().st_size if keep_part and part.is_file() else 0
        if not kept:
            _discard_part(part)
        raise _FetchError(f"{type(exc).__name__}: {exc}"[:400], kept) from None


def run_download(job_id: str, url: str, dest: Path) -> None:
    """One GGUF file into ``dest`` (see :func:`_fetch`)."""
    try:
        done = _fetch(job_id, url, dest)
    except _FetchError as exc:
        error = str(exc)
        if exc.kept:
            error += f"; {exc.kept} bytes kept, download it again to resume"
        jobs.update(job_id, status="error", error=error, resumable=bool(exc.kept), finished_at=_now())
        return
    jobs.update(job_id, status="done", completed=done, total=done, percent=100.0,
                resumable=False, message=f"saved {dest.name}", finished_at=_now())


def _staging_dir(name: str) -> Path:
    """Where a speech model's files gather until all of them are here: hidden,
    so the model list never shows a model with a file missing."""
    return app_settings.MODELS_DIR / f".{name}.download"


#: The record a downloaded speech model keeps of where it came from.
MODEL_MARKER = ".hub-model.json"


def run_package(job_id: str, repo: str, revision: str, package: Dict[str, Any]) -> None:
    """Every file of a speech model (see :func:`speech_packages`) into a
    staging directory, then the directory renamed to the model's name. Files
    finished by an earlier attempt are kept, so asking again resumes."""
    name = package["name"]
    staging = _staging_dir(name)
    staging.mkdir(parents=True, exist_ok=True)
    sizes = dict(package.get("sizes") or {})
    grand = sum(int(v or 0) for v in sizes.values())
    offset = 0
    save_as = dict(package.get("save_as") or {})
    for src in package["files"]:
        target = staging / (save_as.get(src) or src.rsplit("/", 1)[-1])
        size = int(sizes.get(src) or 0)
        if target.is_file() and (not size or target.stat().st_size == size):
            offset += target.stat().st_size
            continue
        # "@owner/repo/file": a file another repo holds, at its main.
        url = (f"{app_settings.HF_BASE}/{src[1:].rsplit('/', 1)[0]}/resolve/main/{src.rsplit('/', 1)[1]}"
               if src.startswith("@") else f"{app_settings.HF_BASE}/{repo}/resolve/{revision}/{src}")
        try:
            offset += _fetch(job_id, url, target, offset=offset, grand_total=grand)
        except _FetchError as exc:
            jobs.update(job_id, status="error", resumable=True, finished_at=_now(),
                        error=f"{exc}; finished files are kept, download it again to resume")
            return
    try:
        (staging / MODEL_MARKER).write_text(json.dumps({
            "repo": repo, "revision": revision, "engine": package["engine"],
            "kind": package["kind"], "files": package["files"], "downloaded_at": _now()}),
            encoding="utf-8")
        staging.replace(app_settings.MODELS_DIR / name)
    except OSError as exc:
        jobs.update(job_id, status="error", resumable=True, finished_at=_now(),
                    error=f"could not move {name} into place: {exc}")
        return
    jobs.update(job_id, status="done", completed=offset, total=offset, percent=100.0,
                resumable=False, message=f"saved {name}", finished_at=_now())


# ── Auth ─────────────────────────────────────────────────────────────────────

def token_from_file(path: str) -> str:
    """The token in ``path``, written there first when the file is missing.
    Readable by others on purpose: in compose the backend reads it from the
    same volume under another user id, and nothing else mounts that volume."""
    import secrets
    f = Path(path)
    try:
        value = f.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(f)
    log.info("wrote a new token to %s", f)
    return value


def token_ok(header_value: Optional[str], token: str) -> bool:
    """Constant-time check of an ``Authorization: Bearer <token>`` header."""
    if not token or not header_value:
        return False
    scheme, _, value = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), token.encode())


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    if not token_ok(authorization, app_settings.TOKEN):
        raise HTTPException(status_code=401, detail="missing or wrong token")


auth = [Depends(require_token)]

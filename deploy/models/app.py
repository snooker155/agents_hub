"""
Agents Hub model runtime: GGUF models served by llama.cpp's llama-server and
speech models served by speech_worker.py, behind a small token-protected API
and one OpenAI-compatible gateway.

Runs in its own container (the Dockerfile next to this file, the ``models``
profile in docker-compose.yml) or straight on the host
(``python deploy/models/app.py`` with llama-server on PATH, which is the only
way to use a Mac's GPU). The hub talks to it through
``providers/local_models.py`` and registers the gateway as the provider
``hub-local``. Every call except ``/healthz`` must carry the shared token
(``MODELS_TOKEN``, the hub's ``AGENTS_HUB_MODELS_TOKEN``). Without one the
service reads ``MODELS_TOKEN_FILE``, writing a fresh token there first when
the file does not exist (how compose shares it with the backend), and refuses
to start when neither is set. On the host the hub starts this service itself
(providers/model_runtime_host.py). See docs/local-models.md.

Engines
    llama.cpp's ``llama-server`` for chat models: ``LLAMA_SERVER_BIN``, else
    the build this service installed (``POST /engines/llama/install`` fetches
    llama.cpp's release binaries for this platform into
    ``MODELS_DIR/.engines``), else ``llama-server`` on PATH. The docker image
    has it built in. Speech engines are Python packages, see speech_worker.py.

Models
    Files in ``MODELS_DIR`` ending in ``.gguf``. A directory holding
    ``config.json`` and ``*.safetensors`` is listed too, marked as not
    loadable: llama-server reads GGUF only. A directory holding a speech
    model (a faster-whisper, Piper, Kokoro, Kitten or Supertonic model, see
    speech_worker.py) is
    listed with its engine and purpose: ``transcription`` or ``speech``.

Loading
    One subprocess per loaded model, on its own port from
    ``MODELS_BASE_PORT`` up, bound to ``MODELS_LLAMA_HOST`` (loopback by
    default: only this service talks to it): llama-server for a GGUF,
    speech_worker.py for a speech model. Chat models and speech models are
    two pools: at most ``MODELS_MAX_LOADED`` chat models and
    ``MODELS_MAX_SPEECH_LOADED`` speech models run at once, and loading one
    more evicts the least recently used of its own pool, so a voice call
    never unloads the chat model. A speech model is also loaded on its first
    request through the gateway.

Endpoints
    GET    /healthz                                  (no token)
    GET    /models
    DELETE /models/{file}
    GET    /models/{file}/structure
    POST   /download        {repo, file, revision}   -> {job_id}
    POST   /download        {repo, files, name, revision} (a speech model) -> {job_id}
    GET    /jobs, /jobs/{id}
    GET    /hf/files?repo=&revision=                 (GGUF files with a fit estimate each, speech packages)
    GET    /hf/search?q=&purpose=&license=&sort=     (GGUF models on the Hub, with what they need here)
    GET    /hardware                                 (what fit and speed estimates assume)
    GET    /engines, POST /engines/{engine}/install
    GET    /ollama/models, POST /ollama/import {name}  (Ollama's GGUF files, linked in)
    GET    /lmstudio/models, POST /lmstudio/import {name}  (LM Studio's GGUF files and MLX folders)
    POST   /load            {file, context_length, gpu_layers, threads}
    POST   /unload          {file}
    GET    /memory
    GET    /usage, DELETE /usage                       (calls through the gateway, per model and caller)
    GET    /cache, PUT /cache/settings, POST /cache/apply, /cache/save, /cache/warmup {model},
    DELETE /cache?model=                              (the prompt cache, see "Prompt cache" below)
    GET    /v1/models, POST /v1/chat/completions, /v1/completions, /v1/embeddings
    POST   /v1/audio/speech, /v1/audio/transcriptions
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

log = logging.getLogger("models_service")
logging.basicConfig(level=os.environ.get("MODELS_LOG_LEVEL", "INFO"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


TOKEN = (os.environ.get("MODELS_TOKEN") or os.environ.get("AGENTS_HUB_MODELS_TOKEN") or "").strip()
TOKEN_FILE = (os.environ.get("MODELS_TOKEN_FILE") or "").strip()
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or ("/models" if in_docker() else "./models"))
PORT = _env_int("MODELS_PORT", 8200)
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN") or "llama-server"
#: Whether LLAMA_SERVER_BIN was set on purpose, which an installed build
#: does not override.
_LLAMA_BIN_PINNED = bool(os.environ.get("LLAMA_SERVER_BIN"))
BASE_PORT = _env_int("MODELS_BASE_PORT", 8300)
MAX_LOADED = max(1, _env_int("MODELS_MAX_LOADED", 2))
LLAMA_HOST = os.environ.get("MODELS_LLAMA_HOST") or "127.0.0.1"
HF_TOKEN = (os.environ.get("HF_TOKEN") or "").strip()
HF_BASE = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
LOAD_TIMEOUT_SECONDS = float(_env_int("MODELS_LOAD_TIMEOUT", 120))
JOBS_KEPT = 50
#: Three: a voice converter (OpenVoice) needs the model that reads for it
#: running beside it, and transcription stays loaded next to the pair.
MAX_SPEECH_LOADED = max(1, _env_int("MODELS_MAX_SPEECH_LOADED", 3))
#: The interpreter speech workers run under: this one unless the engines live
#: in another environment.
SPEECH_PYTHON = os.environ.get("MODELS_SPEECH_PYTHON") or sys.executable
SPEECH_WORKER = Path(__file__).resolve().with_name("speech_worker.py")
VOICE_ENHANCE = Path(__file__).resolve().with_name("voice_enhance.py")

_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_REVISION_RE = re.compile(r"^[\w.\-/]{1,100}$")
_QUANT_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(IQ\d_[A-Z0-9]+(?:_[A-Z0-9]+)?|Q\d_K(?:_[SML])?|Q\d_\d|"
                       r"Q\d_K|BF16|F16|F32)(?![A-Za-z0-9])")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Jobs ─────────────────────────────────────────────────────────────────────

def _jobs_file() -> Path:
    """Where the job list lives between restarts: beside the models, hidden."""
    return MODELS_DIR / ".jobs.json"


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

    def __init__(self, keep: int = JOBS_KEPT) -> None:
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
                    part = MODELS_DIR / f"{dest}.part" if dest else None
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
    return MODELS_DIR / ".usage.json"


#: The header the hub names the caller with: ``hub`` (its agents and its own
#: work), ``endpoint`` (a call to the hub's /v1 from outside), ``voice`` (the
#: assistant's speech). A call without it is counted as ``direct``.
SOURCE_HEADER = "x-hub-source"
_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")

#: The gateway path a call came in on, and the kind it is counted under.
_KIND_OF_PATH = {"/v1/chat/completions": "chat", "/v1/completions": "chat",
                 "/v1/embeddings": "embeddings", "/v1/audio/speech": "speech",
                 "/v1/audio/transcriptions": "transcription"}

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
        counted = kind != "speech" and code < 400
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
                if kind != "speech":
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
    return {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}


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
    return MODELS_DIR / f".{name}.download"


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
        url = (f"{HF_BASE}/{src[1:].rsplit('/', 1)[0]}/resolve/main/{src.rsplit('/', 1)[1]}"
               if src.startswith("@") else f"{HF_BASE}/{repo}/resolve/{revision}/{src}")
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
        staging.replace(MODELS_DIR / name)
    except OSError as exc:
        jobs.update(job_id, status="error", resumable=True, finished_at=_now(),
                    error=f"could not move {name} into place: {exc}")
        return
    jobs.update(job_id, status="done", completed=offset, total=offset, percent=100.0,
                resumable=False, message=f"saved {name}", finished_at=_now())


# ── llama.cpp ────────────────────────────────────────────────────────────────

#: Where installed engines live: beside the models, hidden from the list.
def _engines_dir() -> Path:
    return MODELS_DIR / ".engines"


def _llama_record() -> Dict[str, Any]:
    try:
        data = json.loads((_engines_dir() / "llama.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def llama_bin() -> str:
    """The llama-server to start: a pinned ``LLAMA_SERVER_BIN``, else the
    build installed here, else ``llama-server`` found on PATH at start."""
    if not _LLAMA_BIN_PINNED:
        installed = str(_llama_record().get("bin") or "")
        if installed and Path(installed).is_file():
            return installed
    return LLAMA_SERVER_BIN


def llama_installed() -> bool:
    found = llama_bin()
    return Path(found).is_file() or shutil.which(found) is not None


#: (system, machine) -> the suffix of llama.cpp's release archive. Metal is
#: in the macOS build; the Linux ones are CPU builds.
_LLAMA_ASSETS = {
    ("Darwin", "arm64"): "macos-arm64", ("Darwin", "x86_64"): "macos-x64",
    ("Linux", "x86_64"): "ubuntu-x64", ("Linux", "aarch64"): "ubuntu-arm64",
    ("Linux", "arm64"): "ubuntu-arm64",
}
LLAMA_RELEASES = os.environ.get("MODELS_LLAMA_RELEASES") or \
    "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"


def llama_asset_suffix() -> Optional[str]:
    return _LLAMA_ASSETS.get((platform.system(), platform.machine()))


def find_llama_release(client: httpx.Client) -> Tuple[str, str, int]:
    """The newest llama.cpp build with an archive for this platform: its tag,
    the archive's URL and size."""
    suffix = llama_asset_suffix()
    if suffix is None:
        raise RuntimeError(f"llama.cpp publishes no build for {platform.system()} {platform.machine()}; "
                           "install llama-server yourself and set LLAMA_SERVER_BIN")
    resp = client.get(LLAMA_RELEASES, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub answered HTTP {resp.status_code} for llama.cpp's releases")
    for release in resp.json() or []:
        tag = str(release.get("tag_name") or "")
        want = f"llama-{tag}-bin-{suffix}.tar.gz"
        for asset in release.get("assets") or []:
            if asset.get("name") == want:
                return tag, str(asset["browser_download_url"]), int(asset.get("size") or 0)
    raise RuntimeError(f"no recent llama.cpp release has a {suffix} build")


def run_llama_install(job_id: str) -> None:
    """Download llama.cpp's release archive for this platform, unpack it
    under ``MODELS_DIR/.engines/llama.cpp/<tag>`` and record its
    llama-server, which every later load uses."""
    import tarfile
    jobs.update(job_id, status="running", message="looking for the latest llama.cpp build")
    try:
        with http_client(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            tag, url, size = find_llama_release(client)
            target = _engines_dir() / "llama.cpp" / tag
            archive = _engines_dir() / f"llama-{tag}.tar.gz"
            archive.parent.mkdir(parents=True, exist_ok=True)
            jobs.update(job_id, total=size, message=f"downloading llama.cpp {tag}")
            done, last = 0, 0.0
            with client.stream("GET", url) as resp, open(archive, "wb") as fh:
                if resp.status_code >= 400:
                    raise RuntimeError(f"GitHub answered HTTP {resp.status_code} for {url}")
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 0.5:
                        last = time.monotonic()
                        jobs.update(job_id, completed=done,
                                    percent=round(100.0 * done / size, 1) if size else 0.0)
        jobs.update(job_id, completed=done, message=f"unpacking llama.cpp {tag}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter="data")
        archive.unlink(missing_ok=True)
        found = sorted(target.rglob("llama-server"))
        if not found:
            raise RuntimeError("the archive has no llama-server")
        binary = found[0]
        binary.chmod(0o755)
        check = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30,
                               env={**os.environ, "LD_LIBRARY_PATH": str(binary.parent)})
        if check.returncode != 0:
            raise RuntimeError(f"llama-server {tag} does not run here: {(check.stderr or check.stdout)[-300:]}")
        (_engines_dir() / "llama.json").write_text(json.dumps(
            {"tag": tag, "bin": str(binary), "installed_at": _now()}), encoding="utf-8")
        for old in (_engines_dir() / "llama.cpp").iterdir():
            if old != target and old.is_dir() and not any(m.engine == "llama" for m in state.loaded.values()):
                shutil.rmtree(old, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - the job records why
        jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=_now())
        return
    jobs.update(job_id, status="done", percent=100.0, message=f"llama.cpp {tag} installed", finished_at=_now())


# ── Import from Ollama ───────────────────────────────────────────────────────

#: Where Ollama keeps its models on this machine (``OLLAMA_MODELS`` is
#: Ollama's own setting). In docker it is only there when mounted, for
#: example ``~/.ollama/models:/ollama:ro`` with ``MODELS_OLLAMA_DIR=/ollama``.
OLLAMA_DIR = Path(os.environ.get("MODELS_OLLAMA_DIR") or os.environ.get("OLLAMA_MODELS")
                  or Path.home() / ".ollama" / "models").expanduser()
_OLLAMA_REGISTRY = "registry.ollama.ai"
#: Model families that make embeddings, not text.
_EMBEDDING_FAMILIES = ("bert", "nomic-bert", "jina-bert", "xlm-roberta", "embeddinggemma")


def _ollama_blob(digest: str) -> Path:
    return OLLAMA_DIR / "blobs" / str(digest).replace(":", "-")


def ollama_dest_name(name: str) -> str:
    """The runtime file an Ollama model is imported as: ``gpt-oss:20b``
    becomes ``gpt-oss-20b.gguf``, ``llama3:latest`` becomes ``llama3.gguf``,
    so the model id the hub sees reads like Ollama's."""
    base, _, tag = name.partition(":")
    stem = base.rsplit("/", 1)[-1] if base.count("/") >= 2 else base.replace("/", "-")
    if tag and tag != "latest":
        stem = f"{stem}-{tag}"
    stem = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or "ollama-model"
    return f"{stem}.gguf"


def _ollama_name_of(manifest: Path) -> str:
    """``gpt-oss:20b`` for manifests/registry.ollama.ai/library/gpt-oss/20b;
    other namespaces and registries keep their prefix, as Ollama shows them."""
    parts = manifest.relative_to(OLLAMA_DIR / "manifests").parts
    if len(parts) < 4:
        return ""
    registry, namespace, model, tag = parts[0], parts[-3], parts[-2], parts[-1]
    if registry == _OLLAMA_REGISTRY:
        return f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
    return f"{registry}/{namespace}/{model}:{tag}"


def ollama_models() -> List[Dict[str, Any]]:
    """Every model Ollama has on disk whose weights are one GGUF file the
    runtime can use: its Ollama name, family, size, quantisation, and the
    file it is (or would be) imported as."""
    return [entry for entry, _blob in _ollama_entries()]


def _ollama_entries() -> List[Tuple[Dict[str, Any], Path]]:
    root = OLLAMA_DIR / "manifests"
    out: List[Tuple[Dict[str, Any], Path]] = []
    if not root.is_dir():
        return out
    for manifest in sorted(p for p in root.rglob("*") if p.is_file()):
        name = _ollama_name_of(manifest)
        if not name:
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        layers = data.get("layers") or []
        weights = [layer for layer in layers if str(layer.get("mediaType", "")).endswith(".model")]
        if len(weights) != 1:
            continue
        blob = _ollama_blob(weights[0].get("digest", ""))
        try:
            with open(blob, "rb") as fh:
                if fh.read(4) != b"GGUF":
                    continue
        except OSError:
            continue
        config: Dict[str, Any] = {}
        try:
            config = json.loads(_ollama_blob((data.get("config") or {}).get("digest", "")).read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            pass
        family = str(config.get("model_family") or "")
        dest = ollama_dest_name(name)
        entry = {"name": name, "file": dest, "size_bytes": int(weights[0].get("size") or blob.stat().st_size),
                 "family": family or None, "parameter_size": config.get("model_type"),
                 "quantization": config.get("file_type"), "imported": (MODELS_DIR / dest).exists(),
                 "embedding": family in _EMBEDDING_FAMILIES, "note": None}
        problem = llama_cpp_problem(blob)
        entry["compatible"] = problem is None
        if problem:
            entry["note"] = problem
        elif any(str(layer.get("mediaType", "")).endswith(".projector") for layer in layers):
            entry["note"] = "imported as text only: its vision part is left out"
        elif entry["embedding"]:
            entry["note"] = "an embedding model: it makes vectors, not answers"
        out.append((entry, blob))
    return out


#: Architectures only Ollama's own engine reads; llama.cpp names the same
#: models differently (gpt-oss) or does not have them.
_OLLAMA_ONLY_ARCHS = {"gptoss": "gpt-oss", "mllama": None, "qwen25vl": "qwen2vl"}
#: Tensor name prefixes of a vision encoder. Ollama's own engine keeps one
#: inside the model file; llama.cpp wants it as a separate projector.
_VISION_PREFIXES = ("v.", "mm.", "vision_tower.", "vision.")
_compat_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def llama_cpp_problem(blob: Path) -> Optional[str]:
    """Why llama.cpp cannot load this Ollama file, or None when it can, read
    from the GGUF header (cached by file and time). Ollama runs some models
    on an engine of its own, with files only it reads."""
    try:
        mtime = blob.stat().st_mtime
    except OSError:
        return None
    key = str(blob)
    cached = _compat_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    problem: Optional[str] = None
    module = _structure_module()
    if module is not None:
        try:
            parsed = module.parse_gguf(blob)
            arch = str((parsed.get("metadata") or {}).get("general.architecture") or "")
            vision = sum(1 for t in parsed.get("tensors") or []
                         if str(t.get("name", "")).startswith(_VISION_PREFIXES))
            if arch in _OLLAMA_ONLY_ARCHS:
                problem = (f"made for Ollama's own engine (architecture {arch}), which llama.cpp cannot "
                           "load; download it from Hugging Face instead")
            elif vision:
                problem = ("its vision part is inside the file, which only Ollama's own engine reads; "
                           "llama.cpp cannot load it, download it from Hugging Face instead")
        except Exception:  # noqa: BLE001 - a header we cannot read is left to the load to judge
            log.debug("could not read %s", blob, exc_info=True)
    _compat_cache[key] = (mtime, problem)
    return problem


def run_ollama_copy(job_id: str, src: Path, dest: Path) -> None:
    """Copy an Ollama blob into the models directory, for when a hard link
    is not possible (another disk, a read-only mount)."""
    part = dest.with_name(dest.name + ".part")
    total = src.stat().st_size
    jobs.update(job_id, status="running", total=total, message=f"copying {dest.name}")
    done, last = 0, 0.0
    try:
        with open(src, "rb") as fin, open(part, "wb") as fout:
            while True:
                chunk = fin.read(8 * 1024 * 1024)
                if not chunk:
                    break
                fout.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 0.5:
                    last = time.monotonic()
                    jobs.update(job_id, completed=done, percent=round(100.0 * done / total, 1) if total else 0.0)
        part.replace(dest)
    except Exception as exc:  # noqa: BLE001 - the job records why
        part.unlink(missing_ok=True)
        jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:400], finished_at=_now())
        return
    jobs.update(job_id, status="done", completed=done, percent=100.0, message=f"saved {dest.name}",
                finished_at=_now())


# ── Import from LM Studio ────────────────────────────────────────────────────

def lmstudio_dir() -> Path:
    """LM Studio's model folder on this machine: ``MODELS_LMSTUDIO_DIR``, else
    the downloads folder its settings name, else its defaults."""
    if os.environ.get("MODELS_LMSTUDIO_DIR"):
        return Path(os.environ["MODELS_LMSTUDIO_DIR"]).expanduser()
    home = Path.home()
    try:
        folder = json.loads((home / ".lmstudio" / "settings.json").read_text(encoding="utf-8")).get(
            "downloadsFolder")
        if folder and Path(folder).expanduser().is_dir():
            return Path(folder).expanduser()
    except (OSError, ValueError, AttributeError):
        pass
    for candidate in (home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models"):
        if candidate.is_dir():
            return candidate
    return home / ".lmstudio" / "models"


def _lmstudio_entries() -> List[Tuple[Dict[str, Any], Path]]:
    """LM Studio's models the runtime can take over: GGUF files (a vision
    projector, ``mmproj*``, is not a model) and MLX model folders."""
    root = lmstudio_dir()
    out: List[Tuple[Dict[str, Any], Path]] = []
    if not root.is_dir():
        return out
    mlx_ok = mlx_platform()
    for repo in sorted(p for p in root.glob("*/*") if p.is_dir()):
        publisher = repo.parent.name
        config = mlx_config(repo)
        if config is not None:
            quant = config.get("quantization") if isinstance(config.get("quantization"), dict) else {}
            dest = re.sub(r"[^\w.-]+", "-", repo.name).strip("-.")
            entry = {"name": f"{publisher}/{repo.name}", "file": dest, "format": "mlx",
                     "size_bytes": sum(f.stat().st_size for f in repo.rglob("*") if f.is_file()),
                     "family": config.get("model_type"),
                     "quantization": f"{quant['bits']}-bit" if quant.get("bits") else None,
                     "parameter_size": None, "imported": (MODELS_DIR / dest).exists(),
                     "compatible": mlx_ok, "embedding": False,
                     "note": None if mlx_ok else "an MLX model: MLX runs on Apple silicon only"}
            out.append((entry, repo))
            continue
        for gguf in sorted(repo.glob("*.gguf")):
            if gguf.name.lower().startswith("mmproj"):
                continue
            entry = {"name": f"{publisher}/{repo.name}/{gguf.name}", "file": gguf.name, "format": "gguf",
                     "size_bytes": gguf.stat().st_size, "family": None,
                     "quantization": quantization_of(gguf.name), "parameter_size": None,
                     "imported": (MODELS_DIR / gguf.name).exists(), "compatible": True,
                     "embedding": False, "note": None}
            out.append((entry, gguf))
    return out


def _link_tree(src: Path, dest: Path) -> None:
    """``dest`` as a directory of hard links to every file under ``src``;
    raises OSError when a link cannot be made (another disk)."""
    for f in src.rglob("*"):
        if f.is_file() and not f.name.startswith("."):
            target = dest / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(f, target)


def run_tree_copy(job_id: str, src: Path, dest: Path) -> None:
    """Copy a model folder in, for when linking is not possible; it appears
    under its name only once whole."""
    staging = MODELS_DIR / f".{dest.name}.import"
    files = [f for f in src.rglob("*") if f.is_file() and not f.name.startswith(".")]
    total = sum(f.stat().st_size for f in files)
    jobs.update(job_id, status="running", total=total, message=f"copying {dest.name}")
    done, last = 0, 0.0
    try:
        shutil.rmtree(staging, ignore_errors=True)
        for f in files:
            target = staging / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "rb") as fin, open(target, "wb") as fout:
                while chunk := fin.read(8 * 1024 * 1024):
                    fout.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 0.5:
                        last = time.monotonic()
                        jobs.update(job_id, completed=done, percent=round(100.0 * done / total, 1) if total else 0.0)
        staging.replace(dest)
    except Exception as exc:  # noqa: BLE001 - the job records why
        shutil.rmtree(staging, ignore_errors=True)
        jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:400], finished_at=_now())
        return
    jobs.update(job_id, status="done", completed=done, percent=100.0, message=f"saved {dest.name}",
                finished_at=_now())


# ── MLX ──────────────────────────────────────────────────────────────────────

#: Architectures (``config.json``) of the models mlx-lm serves for chat.
_CHAT_ARCH_SUFFIXES = ("ForCausalLM", "ForConditionalGeneration")


def mlx_config(path: Path) -> Optional[Dict[str, Any]]:
    """The ``config.json`` of a directory mlx-lm can serve as a chat model
    (an MLX quantised model, as LM Studio keeps them, or plain Hugging Face
    safetensors), or None."""
    if not path.is_dir() or not any(path.glob("*.safetensors")):
        return None
    try:
        config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    archs = config.get("architectures") if isinstance(config, dict) else None
    if not isinstance(archs, list) or not any(str(a).endswith(_CHAT_ARCH_SUFFIXES) for a in archs):
        return None
    return config


def mlx_context(config: Dict[str, Any]) -> int:
    text = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}
    for c in (config, text):
        for key in ("max_position_embeddings", "max_sequence_length", "seq_length"):
            if isinstance(c.get(key), int) and c[key] > 0:
                return int(c[key])
    return 0


def build_mlx_command(path: Path, port: int, cache: Tuple[str, ...] = ()) -> List[str]:
    return [SPEECH_PYTHON, "-m", "mlx_lm.server", "--model", str(path), "--port", str(port),
            "--host", LLAMA_HOST, "--log-level", "WARNING", *cache]


def split_harmony(text: str) -> Tuple[str, str]:
    """gpt-oss's harmony output, which mlx-lm passes through as text, split
    into (reasoning, answer): the ``analysis`` channel is reasoning, the
    ``final`` channel the answer. Text without channel markers is all answer.
    A message still being written counts, so a stream can be split as it
    grows."""
    if "<|channel|>" not in text:
        return "", text
    reasoning: List[str] = []
    answer: List[str] = []
    head, _, rest = text.partition("<|channel|>")
    if head.strip() and not head.startswith("<|start|>"):
        answer.append(head)
    for chunk in ("<|channel|>" + rest).split("<|channel|>")[1:]:
        header, sep, body = chunk.partition("<|message|>")
        if not sep:
            continue  # the header is still arriving
        for end in ("<|end|>", "<|return|>", "<|call|>", "<|start|>"):
            body = body.split(end, 1)[0]
        channel = header.split()[0] if header.split() else ""
        (reasoning if channel == "analysis" else answer if channel == "final" else []).append(body)
    return "".join(reasoning), "".join(answer)


class HarmonyStream:
    """:func:`split_harmony` over a stream: each piece of text in, the new
    reasoning and answer text out. A marker cut in half at the end of a piece
    waits for the rest."""

    def __init__(self) -> None:
        self.raw = ""
        self.sent = (0, 0)

    def feed(self, text: str) -> Tuple[str, str]:
        self.raw += text
        safe = self.raw
        cut = safe.rfind("<|")
        if cut != -1 and "|>" not in safe[cut:]:
            safe = safe[:cut]
        reasoning, answer = split_harmony(safe)
        out = (reasoning[self.sent[0]:], answer[self.sent[1]:])
        self.sent = (len(reasoning), len(answer))
        return out


def mlx_message(msg: Dict[str, Any], harmony: Optional[HarmonyStream], *, whole: bool) -> None:
    """One message or delta from mlx-lm in the shape llama-server and the
    hub use: reasoning under ``reasoning_content``, gpt-oss's channels split."""
    if not isinstance(msg, dict):
        return
    if msg.get("reasoning") and not msg.get("reasoning_content"):
        msg["reasoning_content"] = msg.pop("reasoning")
    if harmony is not None and isinstance(msg.get("content"), str):
        if whole:
            reasoning, answer = split_harmony(msg["content"])
        else:
            reasoning, answer = harmony.feed(msg["content"])
        msg["content"] = answer if (answer or whole) else None
        if not answer and not whole:
            msg.pop("content", None)
        if reasoning:
            msg["reasoning_content"] = (msg.get("reasoning_content") or "") + reasoning


# ── Speech models ────────────────────────────────────────────────────────────

#: Engine -> purpose, the same words the hub's special models use.
ENGINE_KIND = {"whisper": "transcription", "piper": "speech", "kokoro": "speech", "kitten": "speech",
               "supertonic": "speech", "chatterbox": "speech", "chatterbox_mlx": "speech", "openvoice": "speech",
               "mlx": "chat", "deepfilternet": "cleanup", "resemble_enhance": "cleanup"}
#: Every engine the Models page lists, chat first.
ALL_ENGINES = ("llama", "mlx", "whisper", "piper", "kokoro", "kitten", "supertonic", "chatterbox", "chatterbox_mlx",
               "openvoice", "deepfilternet", "resemble_enhance")
#: Engines that run only on Apple silicon: listed nowhere else.
APPLE_ENGINES = ("mlx", "chatterbox_mlx", "deepfilternet")
SPEECH_KINDS = ("speech", "transcription")
#: Engine -> the modules that have to import for it to run, comma separated.
#: Kitten runs on the worker's own code over onnxruntime and phonemizer,
#: OpenVoice's converter on the worker's openvoice_vc.py over torch.
ENGINE_MODULES = {"whisper": "faster_whisper", "piper": "piper", "kokoro": "kokoro_onnx",
                  "kitten": "onnxruntime,phonemizer,espeakng_loader", "supertonic": "supertonic",
                  "chatterbox": "chatterbox,torch,librosa,perth,pkg_resources", "openvoice": "torch,numpy,av",
                  "chatterbox_mlx": "mlx_audio,mlx,av",
                  "mlx": "mlx_lm",
                  "deepfilternet": "mlx_audio,mlx,av",
                  "resemble_enhance": "resemble_enhance,torch,torchaudio,scipy,librosa,soundfile,omegaconf,rich,av"}
#: The torch version the voice cloning engines share: the one Chatterbox
#: is built against (newer on Python 3.14, which 2.6 has no wheels for).
_TORCH = ['torch==2.6.0; python_version < "3.14"', 'torch>=2.9; python_version >= "3.14"']
_TORCHAUDIO = ['torchaudio==2.6.0; python_version < "3.14"', 'torchaudio>=2.9; python_version >= "3.14"']
#: Engine -> what ``pip install`` puts in place (POST /engines/{engine}/install,
#: and requirements-speech.txt for the image). The voice cloning engines get
#: an environment of their own, see :data:`TORCH_ENGINES`.
ENGINE_PACKAGES = {
    "whisper": ["faster-whisper>=1.1", "av>=12"],
    "piper": ["piper-tts>=1.3", "av>=12"],
    "kokoro": ["kokoro-onnx>=0.4", "av>=12"],
    "kitten": ["onnxruntime>=1.18", "phonemizer-fork>=3.3", "espeakng-loader>=0.2", "av>=12"],
    "supertonic": ["supertonic>=1.3", "av>=12"],
    # chatterbox-tts's own requirements, with exact pins where it has them,
    # minus gradio (its demo) and the Chinese and Japanese text helpers it
    # does without; the package itself goes in with --no-deps
    # (ENGINE_NO_DEPS).
    "chatterbox": [*_TORCH, *_TORCHAUDIO, "numpy>=1.24", "librosa==0.11.0", "s3tokenizer",
                   "transformers==5.2.0", "diffusers==0.29.0", "resemble-perth>=1.0.0", "conformer==0.3.2",
                   "safetensors==0.5.3", "pyloudnorm", "omegaconf", "av>=12",
                   # The watermarker imports pkg_resources, which setuptools 81 drops.
                   "setuptools<81"],
    "openvoice": [*_TORCH, "numpy>=1.24", "av>=12"],
    # mlx-audio's port of Chatterbox, the version it was checked with.
    "chatterbox_mlx": ["mlx-audio>=0.5.8,<0.6", "av>=12"],
    "mlx": ["mlx-lm>=0.32"],
    # Voice cleanup (voice_enhance.py). DeepFilterNet shares Chatterbox
    # MLX's environment and port collection.
    "deepfilternet": ["mlx-audio>=0.5.8,<0.6", "av>=12"],
    # resemble-enhance pins a training stack (deepspeed, torch 2.1, gradio)
    # inference never touches: what inference imports goes in here, the
    # package itself with --no-deps, the rest is stood in for at run time.
    "resemble_enhance": [*_TORCH, *_TORCHAUDIO, "numpy>=1.24", "scipy>=1.11", "librosa>=0.10", "soundfile>=0.12",
                         "omegaconf>=2.3", "rich>=13", "tqdm>=4.66", "av>=12"],
}
#: Packages installed without their dependencies, after ENGINE_PACKAGES.
ENGINE_NO_DEPS = {"chatterbox": ["chatterbox-tts==0.1.7"], "resemble_enhance": ["resemble-enhance==0.0.1"]}
#: Engines that run in an environment of their own (:func:`engine_python`),
#: by its name: torch and Chatterbox's exact pins, or mlx-audio's newer
#: transformers, stay away from the light ONNX engines and from each other,
#: and removing that directory removes them.
ENGINE_VENVS = {"chatterbox": "torch", "openvoice": "torch", "chatterbox_mlx": "mlx-audio",
                "deepfilternet": "mlx-audio", "resemble_enhance": "torch"}
#: Environment name -> the variable that points at an interpreter of one's
#: own instead.
VENV_PYTHON_ENV = {"torch": "MODELS_TORCH_PYTHON", "mlx-audio": "MODELS_MLX_AUDIO_PYTHON"}
TORCH_ENGINES = tuple(e for e, v in ENGINE_VENVS.items() if v == "torch")
#: What a speech worker itself needs, for an environment made for it.
WORKER_PACKAGES = ["fastapi>=0.110,<1", "uvicorn>=0.29,<1", "python-multipart>=0.0.9", "numpy>=1.24", "av>=12"]
#: Engine -> the format column of the model list.
ENGINE_FORMAT = {"whisper": "ctranslate2", "piper": "onnx", "kokoro": "onnx", "kitten": "onnx",
                 "supertonic": "onnx", "chatterbox": "torch", "chatterbox_mlx": "mlx", "openvoice": "torch",
                 "mlx": "mlx", "deepfilternet": "mlx", "resemble_enhance": "torch"}
#: The files of a faster-whisper model directory worth fetching.
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt", "vocabulary.json",
                 "preprocessor_config.json")
_PIPER_LANG_RE = re.compile(r"^([a-z]{2,3}_[A-Z]{2})-")

_worker_module: Any = None


def _worker() -> Any:
    """speech_worker.py, loaded by path: it sits next to this file in the
    image and in the repository, and its top level imports nothing heavy."""
    global _worker_module
    if _worker_module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hub_speech_worker", SPEECH_WORKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        _worker_module = module
    return _worker_module


def speech_engine_of(path: Path) -> Optional[str]:
    return _worker().detect_engine(path) if path.is_dir() else None


def engines() -> Dict[str, bool]:
    """Which engines run here: llama.cpp (a binary), MLX and the speech
    engines (packages that import under ``SPEECH_PYTHON``). MLX is only
    listed on Apple silicon, the one place it runs."""
    found = {"llama": llama_installed(), **speech_engines()}
    if not mlx_platform():
        for e in APPLE_ENGINES:
            found.pop(e, None)
    return found


def mlx_platform() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def venv_dir(name: str) -> Path:
    """An engine environment (:data:`ENGINE_VENVS`), beside the models (a
    volume in the image, so an install outlives the container)."""
    return _engines_dir() / name


def _pinned_python(name: str) -> str:
    return (os.environ.get(VENV_PYTHON_ENV.get(name, "")) or "").strip()


def engine_python(engine: str) -> str:
    """The interpreter ``engine`` runs under: its environment's, or the one
    its ``VENV_PYTHON_ENV`` variable names; ``SPEECH_PYTHON`` for an engine
    with no environment of its own."""
    name = ENGINE_VENVS.get(engine)
    if name is None:
        return SPEECH_PYTHON
    return _pinned_python(name) or str(venv_dir(name) / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def speech_engines() -> Dict[str, bool]:
    found: Dict[str, bool] = {}
    groups: Dict[str, Dict[str, str]] = {}
    for e, mods in ENGINE_MODULES.items():
        groups.setdefault(engine_python(e), {})[e] = mods
    for python, mods in groups.items():
        found.update(_modules_found(python, mods))
    return {e: found.get(e, False) for e in ENGINE_MODULES}


def _modules_found(python: str, engine_modules: Dict[str, str]) -> Dict[str, bool]:
    """Which of ``engine_modules`` import under ``python``."""
    import importlib
    import importlib.util
    if python == sys.executable:
        importlib.invalidate_caches()
        return {e: all(importlib.util.find_spec(m) is not None for m in mods.split(","))
                for e, mods in engine_modules.items()}
    if not Path(python).is_file() and os.sep in python:
        return {e: False for e in engine_modules}  # an environment not made yet
    code = ("import importlib.util, json; print(json.dumps({e: all(importlib.util.find_spec(m) is not None "
            f"for m in ms.split(',')) for e, ms in {engine_modules!r}.items()}}))")
    try:
        out = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=20).stdout
        found = json.loads(out.strip().splitlines()[-1])
        return {e: bool(found.get(e)) for e in engine_modules}
    except Exception:  # noqa: BLE001 - a missing or broken interpreter has no engines
        log.warning("could not ask %s for its speech engines", python, exc_info=True)
        return {e: False for e in engine_modules}


def speech_voices(path: Path, engine: str) -> List[str]:
    """The voice names a speech model knows, read from its files without
    loading it: Piper's speaker map, the arrays in Kokoro's voices file,
    Kitten's names in its config (else its voices file), Supertonic's
    style files."""
    try:
        if engine == "piper":
            for cfg in sorted(path.glob("*.onnx.json")):
                data = json.loads(cfg.read_text(encoding="utf-8"))
                return sorted((data.get("speaker_id_map") or {}).keys())
        if engine == "kokoro":
            for bin_ in sorted(path.glob("voices*.bin")):
                with zipfile.ZipFile(bin_) as z:
                    return sorted(n[:-4] for n in z.namelist() if n.endswith(".npy"))
        if engine == "kitten":
            aliases = json.loads((path / "config.json").read_text(encoding="utf-8")).get("voice_aliases")
            if aliases:
                return list(aliases)
            for npz in sorted(path.glob("*.npz")):
                with zipfile.ZipFile(npz) as z:
                    return sorted(n[:-4] for n in z.namelist() if n.endswith(".npy"))
        if engine == "supertonic":
            return sorted(p.stem for p in (path / "voice_styles").glob("*.json"))
        if engine in _worker().CLONING_ENGINES:
            own = ["default"] if ((engine == "chatterbox" and (path / "conds.pt").is_file())
                                  or (engine == "chatterbox_mlx" and (path / "conds.safetensors").is_file())) else []
            return own + _worker().recorded_voices(voices_dir())
    except (OSError, ValueError, zipfile.BadZipFile):
        log.debug("could not read the voices of %s", path, exc_info=True)
    return []


def package_name(engine: str, stem: str) -> str:
    """A model directory's name: the stem, made file-safe, with the engine in
    front when the stem does not say it (``ru_RU-irina-medium`` becomes
    ``piper-ru_RU-irina-medium``), so the hub can tell what it is from the id
    alone."""
    slug = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or engine
    family = engine.split("_", 1)[0]  # chatterbox_mlx is a chatterbox
    return slug if family in slug.lower() else f"{engine}-{slug}"


#: mlx-audio's Chatterbox loads its speech tokenizer from this repo; its
#: files and their sizes, for the download's progress.
S3_TOKENIZER_REPO = "mlx-community/S3TokenizerV2"
S3_TOKENIZER_FILES = {"model.safetensors": 494868984, "config.json": 126}


def speech_packages(repo: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The speech models a Hugging Face tree holds, each with the files to
    fetch: a faster-whisper directory, a Piper voice (``x.onnx`` with
    ``x.onnx.json``, or a repackaged one: ``model.onnx`` with ``config.json``
    in a repo or folder named piper, the config saved as ``model.onnx.json``
    through ``save_as``), a Kokoro model (``kokoro*.onnx`` with
    ``voices*.bin``), a Kitten model (``config.json``, ``*.onnx`` and
    ``*.npz`` in a repo named kitten) or a Supertonic one (``onnx/`` and
    ``voice_styles/`` at the repo's top, kept as folders through
    ``save_as``)."""
    sizes: Dict[str, int] = {}
    by_dir: Dict[str, List[str]] = {}
    for item in items:
        p = str(item.get("path") or "")
        if item.get("type") != "file" or not p:
            continue
        sizes[p] = int((item.get("lfs") or {}).get("size") or item.get("size") or 0)
        d, _, base = p.rpartition("/")
        by_dir.setdefault(d, []).append(base)
    repo_name = repo.rsplit("/", 1)[-1]
    out: List[Dict[str, Any]] = []

    def add(engine: str, stem: str, files: List[str], language: Optional[str] = None,
            save_as: Optional[Dict[str, str]] = None) -> None:
        pkg = {"name": package_name(engine, stem), "engine": engine, "kind": ENGINE_KIND[engine],
               "files": files, "sizes": {f: sizes.get(f, 0) for f in files},
               "size_bytes": sum(sizes.get(f, 0) for f in files), "language": language}
        if save_as:
            pkg["save_as"] = save_as
        out.append(pkg)

    styles = sorted(f"voice_styles/{n}" for n in by_dir.get("voice_styles", []) if n.lower().endswith(".json"))
    if (styles and "supertonic" in repo.lower()
            and all(f"onnx/{f}" in sizes for f in _worker().SUPERTONIC_FILES)):
        files = [f"onnx/{f}" for f in _worker().SUPERTONIC_FILES] + styles
        files += ["config.json"] if "config.json" in sizes else []
        add("supertonic", repo_name, files, save_as={f: f for f in files})

    top = set(by_dir.get("", []))
    if "chatterbox" in repo.lower() and all(f in top for f in _worker().CHATTERBOX_FILES):
        extra = [f for f in ("conds.pt", "Cangjie5_TC.json") if f in top]
        add("chatterbox", "chatterbox-multilingual", [*_worker().CHATTERBOX_FILES, *extra])
    if (repo.lower().startswith("mlx-community/chatterbox") and "turbo" not in repo.lower()
            and {"model.safetensors", "tokenizer.json", "config.json"} <= top):
        files = ["model.safetensors", "tokenizer.json", "config.json"]
        files += [f for f in ("model.safetensors.index.json", "conds.safetensors", "Cangjie5_TC.json") if f in top]
        # The speech tokenizer the port fetches when it loads, kept beside
        # the weights (the engine reads it there, so nothing is fetched).
        extra = {f"@{S3_TOKENIZER_REPO}/{f}": f"s3tokenizer/{f}" for f in S3_TOKENIZER_FILES}
        for src, size in zip(extra, S3_TOKENIZER_FILES.values()):
            sizes[src] = size
        add("chatterbox_mlx", f"{repo_name}-mlx", files + list(extra), save_as=extra)
    if "openvoice" in repo.lower():
        for d, names in sorted(by_dir.items()):
            if {"config.json", "checkpoint.pth"} <= set(names) and (not d or d.rsplit("/", 1)[-1] == "converter"):
                where = f"{d}/" if d else ""
                add("openvoice", f"{repo_name}-converter" if d else repo_name,
                    [where + "config.json", where + "checkpoint.pth"])

    for d, names in sorted(by_dir.items()):
        where = f"{d}/" if d else ""
        lower = {n.lower(): n for n in names}
        hint = f"{repo}/{d}".lower()
        if ("model.bin" in lower and "config.json" in lower and "whisper" in hint
                and {"tokenizer.json", "vocabulary.txt", "vocabulary.json"} & lower.keys()):
            add("whisper", d.rsplit("/", 1)[-1] if d else repo_name,
                [where + lower[f] for f in WHISPER_FILES if f in lower])
        onnx = sorted(n for n in names if n.lower().endswith(".onnx"))
        paired = {n for n in onnx if f"{n}.json" in names}
        for n in sorted(paired):
            m = _PIPER_LANG_RE.match(n)
            add("piper", n[:-5], [where + n, where + n + ".json"], m.group(1) if m else None)
        if (not paired and "piper" in hint and "config.json" in lower and len(onnx) == 1
                and "model.bin" not in lower):
            # speaches-ai and others repackage Piper voices this way.
            stem = d.rsplit("/", 1)[-1] if d else repo_name
            m = _PIPER_LANG_RE.match(stem.removeprefix("piper-") + ".onnx")
            cfg = where + lower["config.json"]
            add("piper", stem.removeprefix("piper-"), [where + onnx[0], cfg], m.group(1) if m else None,
                save_as={cfg: onnx[0] + ".json"})
        voices = sorted(n for n in names if n.lower().startswith("voices") and n.lower().endswith(".bin"))
        if voices and "kokoro" in hint:
            for n in onnx:
                if n not in paired:
                    add("kokoro", n[:-5], [where + n, where + voices[0]])
        npz = sorted(n for n in names if n.lower().endswith(".npz"))
        if npz and onnx and "config.json" in names and "kitten" in hint and not paired:
            # Named after the repo: the int8 and fp32 repos hold a file of
            # the same name.
            stem = d.rsplit("/", 1)[-1] if d else repo_name
            for n in onnx:
                add("kitten", stem if len(onnx) == 1 else n[:-5], [where + n, where + npz[0], where + "config.json"],
                    "en_US")
    out.sort(key=lambda p: (p["kind"], p["engine"], p["name"]))
    return out


# ── Loaded models ────────────────────────────────────────────────────────────

@dataclass
class Loaded:
    name: str
    file: str
    port: int
    proc: Any
    context_length: int
    #: llama for a GGUF; whisper, piper, kokoro, kitten or supertonic for a speech model.
    engine: str = "llama"
    #: chat, speech or transcription.
    kind: str = "chat"
    #: gpt-oss on MLX: its harmony channels are split in the gateway.
    harmony: bool = False
    loaded_at: str = field(default_factory=_now)
    last_used: float = field(default_factory=time.monotonic)
    #: What it was started with, so a reload (``/cache/apply``) starts it alike.
    gpu_layers: int = -1
    threads: Optional[int] = None
    #: The prompt cache flags it was started with (:func:`cache_flags`).
    cache_flags: Tuple[str, ...] = ()
    #: llama-server's slot count and KV type, read at load.
    slots: int = 0
    kv_type: str = "f16"
    #: KV bytes per token measured by a slot save (None until one).
    bytes_per_token: Optional[float] = None
    #: What came back from disk at load, and how the warm-up went.
    restored: Dict[str, Any] = field(default_factory=dict)
    warmup: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_used = time.monotonic()


class _State:
    loaded: Dict[str, Loaded] = {}
    lock: Optional[asyncio.Lock] = None


state = _State()


def _lock() -> asyncio.Lock:
    if state.lock is None:
        state.lock = asyncio.Lock()
    return state.lock


#: llama.cpp's split naming: <stem>-00001-of-00003.gguf. The first part is the
#: file to load; llama-server finds the rest by this pattern.
_SPLIT_RE = re.compile(r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<n>\d{5})\.gguf$", re.IGNORECASE)


def model_name(file: str) -> str:
    """The name a file is served under: without ``.gguf``, and for a split
    model without the part suffix, so every part names the same model."""
    m = _SPLIT_RE.match(file)
    if m:
        return m.group("stem")
    return file[:-5] if file.lower().endswith(".gguf") else file


def split_parts(path: Path) -> List[Path]:
    """Every part of a split GGUF, first part first, or ``[path]`` for a
    plain file. Parts that are missing on disk are simply not listed."""
    m = _SPLIT_RE.match(path.name)
    if not m:
        return [path]
    stem, n = m.group("stem"), int(m.group("n"))
    out = []
    for i in range(1, n + 1):
        candidate = path.with_name(f"{stem}-{i:05d}-of-{n:05d}.gguf")
        if candidate.is_file():
            out.append(candidate)
    return out or [path]


def _first_part(path: Path) -> Path:
    m = _SPLIT_RE.match(path.name)
    if not m or int(m.group("idx")) == 1:
        return path
    first = path.with_name(f"{m.group('stem')}-00001-of-{m.group('n')}.gguf")
    return first if first.is_file() else path


def _safe_file(file: str) -> str:
    """A plain file name inside MODELS_DIR, never a path."""
    f = (file or "").strip()
    if not f or "/" in f or "\\" in f or f.startswith(".") or "\x00" in f:
        raise HTTPException(status_code=400, detail=f"not a model file name: {file!r}")
    return f


def _model_path(file: str) -> Path:
    """The file a request names, or the same name with ``.gguf`` appended:
    the hub addresses a loaded model by its served name, which is the file
    name without the extension (see ``model_name``)."""
    f = _safe_file(file)
    path = MODELS_DIR / f
    if not path.exists() and not f.lower().endswith(".gguf"):
        if (MODELS_DIR / f"{f}.gguf").is_file():
            path = MODELS_DIR / f"{f}.gguf"
        else:
            # A split model is named by its stem; its file is the first part.
            firsts = sorted(MODELS_DIR.glob(f"{f}-00001-of-*.gguf"))
            if firsts:
                path = firsts[0]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no model file {f!r} in {MODELS_DIR}")
    return _first_part(path)


def _find_loaded(name_or_file: str) -> Optional[Loaded]:
    key = model_name(name_or_file)
    return state.loaded.get(key)


def _marker(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads((path / MODEL_MARKER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_models() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not MODELS_DIR.is_dir():
        return out
    # Asked once per listing, and only when a speech model is there.
    installed_engines: Optional[Dict[str, bool]] = None
    for p in sorted(MODELS_DIR.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file() and p.name.lower().endswith(".gguf"):
            m = _SPLIT_RE.match(p.name)
            if m and int(m.group("idx")) != 1:
                continue  # listed under its first part
            parts = split_parts(p)
            loaded = state.loaded.get(model_name(p.name))
            entry = {"name": model_name(p.name), "file": p.name, "format": "gguf",
                     "engine": "llama", "kind": "chat", "loadable": True, "size_bytes": sum(x.stat().st_size for x in parts),
                     "loaded": loaded is not None,
                     "port": loaded.port if loaded else None,
                     "context_length": loaded.context_length if loaded else None,
                     "loaded_at": loaded.loaded_at if loaded else None}
            if m:
                entry["parts"] = int(m.group("n"))
                entry["parts_found"] = len(parts)
                if len(parts) != int(m.group("n")):
                    entry["loadable"] = False
                    entry["note"] = (f"split model: {len(parts)} of {m.group('n')} parts are here; "
                                     "download the rest before loading it")
            out.append(entry)
        elif p.is_dir() and (engine := speech_engine_of(p)):
            installed = installed_engines.get(engine, False) if installed_engines is not None else None
            if installed is None:
                installed_engines = engines()
                installed = installed_engines.get(engine, False)
            loaded = state.loaded.get(p.name)
            entry = {"name": p.name, "file": p.name, "format": ENGINE_FORMAT[engine],
                     "engine": engine, "kind": ENGINE_KIND[engine], "loadable": installed,
                     "size_bytes": sum(f.stat().st_size for f in p.rglob("*") if f.is_file()),
                     "loaded": loaded is not None, "port": loaded.port if loaded else None,
                     "context_length": None, "loaded_at": loaded.loaded_at if loaded else None,
                     "voices": speech_voices(p, engine), "source": _marker(p).get("repo")}
            if not installed:
                entry["note"] = (f"the {engine} engine is not installed in this runtime; install it "
                                 "on the Models page or with pip install -r requirements-speech.txt")
            out.append(entry)
        elif p.is_dir() and (cfg := mlx_config(p)) is not None:
            if installed_engines is None:
                installed_engines = engines()
            loaded = state.loaded.get(p.name)
            entry = {"name": p.name, "file": p.name,
                     "format": "mlx" if cfg.get("quantization") else "safetensors",
                     "engine": "mlx", "kind": "chat", "loadable": bool(installed_engines.get("mlx")),
                     "size_bytes": sum(f.stat().st_size for f in p.rglob("*") if f.is_file()),
                     "loaded": loaded is not None, "port": loaded.port if loaded else None,
                     "context_length": loaded.context_length if loaded else None,
                     "loaded_at": loaded.loaded_at if loaded else None,
                     "architecture": cfg.get("model_type"), "source": _marker(p).get("source")}
            if not mlx_platform():
                entry["note"] = "an MLX model: MLX runs on Apple silicon only"
            elif not entry["loadable"]:
                entry["note"] = "the MLX engine is not installed in this runtime; install it on the Models page"
            out.append(entry)
        elif p.is_dir() and (p / "config.json").is_file() and any(p.glob("*.safetensors")):
            size = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
            out.append({"name": p.name, "file": p.name, "format": "safetensors",
                        "engine": None, "kind": "chat", "loadable": False, "size_bytes": size, "loaded": False, "port": None,
                        "context_length": None, "loaded_at": None,
                        "note": "llama-server loads GGUF only; convert this directory with "
                                "llama.cpp's convert_hf_to_gguf.py to serve it."})
    return out


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((LLAMA_HOST, port))
            return True
        except OSError:
            return False


def _next_port() -> int:
    used = {m.port for m in state.loaded.values()}
    for port in range(BASE_PORT, BASE_PORT + 200):
        if port not in used and _port_free(port):
            return port
    raise HTTPException(status_code=503, detail=f"no free port from {BASE_PORT}")


def _log_path(name: str) -> Path:
    return MODELS_DIR / ".logs" / f"{name}.log"


def _log_tail(name: str, chars: int = 800) -> str:
    try:
        return _log_path(name).read_text(errors="replace")[-chars:]
    except OSError:
        return ""


# ── Prompt cache ─────────────────────────────────────────────────────────────
# Both chat engines keep the prompts they computed and compute only what is
# new in the next call: llama-server per slot, plus ``--cache-ram`` MiB of
# host memory for prompts that left their slot; mlx-lm in an LRU of prompts,
# with the system part of each kept as an entry of its own. An agent sends
# the same long system prompt and tool list on every call, so a hit skips
# most of the prefill. Three things here keep the hits coming:
#
# * the settings (``.cache/settings.json``), turned into each server's flags
#   when it starts; a change reaches a running model when it is loaded again
#   (``POST /cache/apply`` does that for every model that differs);
# * llama-server's slots, saved to ``.cache/slots/<model>`` before the model
#   stops (an unload, an eviction, ``POST /cache/save`` before the hub stops
#   the runtime, the runtime's own shutdown) and restored when it is loaded
#   again with the same file, context length, KV type and llama.cpp build;
# * the prompt heads the gateway saw (system messages, tools, template
#   options; :func:`prompt_head`), kept per model in ``.cache/heads`` and sent
#   once each, one token long, after a load: the warm-up. An agent's first
#   call after a load then finds its prefix computed, on either engine.

CACHE_KV_TYPES = ("f16", "q8_0", "q4_0")
#: Bytes per KV element of each type (a q8_0 or q4_0 block of 32 carries a scale).
_KV_TYPE_BYTES = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}
CACHE_DEFAULTS: Dict[str, Any] = {
    #: Prompt caching at all (llama-server --cache-prompt, mlx-lm's prompt cache).
    "enabled": True,
    #: Memory for computed prompts beyond the slots, per llama-server
    #: (--cache-ram); mlx-lm's whole prompt cache (--prompt-cache-bytes).
    "ram_mib": 8192,
    #: Parallel slots of llama-server (-np); 0 lets it decide.
    "slots": 0,
    #: Smallest run of tokens llama-server moves within a slot to reuse it
    #: (--cache-reuse); 0 reuses the common prefix only.
    "reuse_tokens": 256,
    #: KV cache precision of llama-server (-ctk/-ctv).
    "kv_type": "f16",
    #: Slots saved to disk before a model stops and restored at its next load.
    "disk": True,
    #: Disk the saved slots of every model may take together.
    "disk_mib": 16384,
    #: Prompt heads replayed after a load.
    "warmup": True,
    #: Prompt heads kept per model for it.
    "warmup_prompts": 8,
}
CACHE_LIMITS: Dict[str, Tuple[int, int]] = {
    "ram_mib": (0, 1 << 20), "slots": (0, 64), "reuse_tokens": (0, 1 << 16),
    "disk_mib": (0, 1 << 22), "warmup_prompts": (0, 64)}
#: A prompt head shorter than this (its JSON) is not worth a warm-up call.
HEAD_MIN_CHARS = 1500
#: Request fields that change how a chat template renders the head.
_HEAD_FIELDS = ("tools", "chat_template_kwargs", "reasoning_effort")
_MIB = 1024 * 1024
#: The longest a single slot save or restore may take.
SLOT_IO_TIMEOUT = 120.0


def _cache_dir() -> Path:
    return MODELS_DIR / ".cache"


def memory_limit() -> Optional[int]:
    """The memory this process may use when a cgroup limits it (a container
    with ``mem_limit``), else None."""
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(f).read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < (1 << 50):
            return int(raw)
    return None


_defaults: Optional[Dict[str, Any]] = None


def cache_defaults() -> Dict[str, Any]:
    """:data:`CACHE_DEFAULTS` fitted to this machine: the RAM for prompts
    beyond the slots is an eighth of the memory there is (a container's
    limit first), between 512 MiB and llama-server's own default of 8 GiB,
    so a small container is not pushed out of memory by its cache."""
    global _defaults
    if _defaults is None:
        total = memory_limit()
        if total is None:
            try:
                total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            except (AttributeError, ValueError, OSError):  # no reading leaves the default
                total = None
        out = dict(CACHE_DEFAULTS)
        if total:
            out["ram_mib"] = int(min(CACHE_DEFAULTS["ram_mib"], max(512, total // 8 // _MIB)))
        _defaults = out
    return dict(_defaults)


def check_cache_settings(changes: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``base`` (the defaults when None) with ``changes`` applied, each
    checked; a ValueError names the first that is wrong."""
    out = dict(cache_defaults() if base is None else base)
    for key, value in (changes or {}).items():
        if key not in CACHE_DEFAULTS:
            raise ValueError(f"unknown cache setting {key!r}")
        if isinstance(CACHE_DEFAULTS[key], bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key == "kv_type":
            if value not in CACHE_KV_TYPES:
                raise ValueError(f"kv_type must be one of {', '.join(CACHE_KV_TYPES)}")
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be a whole number")
            lo, hi = CACHE_LIMITS[key]
            if not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = value
    return out


_cache_settings: Optional[Dict[str, Any]] = None


def cache_settings() -> Dict[str, Any]:
    """The prompt cache settings: the saved ones over the defaults."""
    global _cache_settings
    if _cache_settings is None:
        path = _cache_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _cache_settings = check_cache_settings(
                {k: v for k, v in raw.items() if k in CACHE_DEFAULTS} if isinstance(raw, dict) else {})
        except FileNotFoundError:
            _cache_settings = cache_defaults()
        except (OSError, ValueError) as exc:
            log.warning("could not read %s, using the defaults: %s", path, exc)
            _cache_settings = cache_defaults()
    return dict(_cache_settings)


def save_cache_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    global _cache_settings
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "settings.json.tmp"
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(d / "settings.json")
    _cache_settings = dict(settings)
    return dict(settings)


def _slot_dir(name: str) -> Path:
    return _cache_dir() / "slots" / name


def cache_flags(engine: str, name: str, cache: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    """The prompt cache flags a chat server of ``engine`` starts with."""
    c = cache or cache_settings()
    if engine == "mlx":
        if not c["enabled"]:
            return ("--prompt-cache-size", "0")
        if not c["ram_mib"]:
            return ("--prompt-cache-size", "1")
        # Room for every warmed head and the conversations that grow from them.
        return ("--prompt-cache-size", str(max(10, c["warmup_prompts"] * 2)),
                "--prompt-cache-bytes", str(c["ram_mib"] * _MIB))
    if engine != "llama":
        return ()
    flags: List[str] = []
    if c["slots"]:
        flags += ["-np", str(c["slots"])]
    if c["kv_type"] != "f16":
        flags += ["-ctk", c["kv_type"], "-ctv", c["kv_type"]]
    if not c["enabled"]:
        return tuple(flags + ["--no-cache-prompt", "--cache-ram", "0"])
    flags += ["--cache-ram", str(c["ram_mib"])]
    if c["reuse_tokens"]:
        flags += ["--cache-reuse", str(c["reuse_tokens"])]
    if c["disk"]:
        flags += ["--slot-save-path", str(_slot_dir(name))]
    return tuple(flags)


def _disk_on(m: Loaded) -> bool:
    return m.engine == "llama" and "--slot-save-path" in m.cache_flags


def _slot_key(m: Loaded) -> Dict[str, Any]:
    """What saved slots must match to be restored into ``m``: the KV of one
    file, context length, KV type and llama.cpp build is not the KV of another."""
    try:
        st = (MODELS_DIR / m.file).stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    return {"file": m.file, "size": size, "mtime": mtime, "context_length": m.context_length,
            "kv_type": m.kv_type, "llama": str(_llama_record().get("tag") or llama_bin())}


def _read_manifest(d: Path) -> Dict[str, Any]:
    try:
        data = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _drop_slot_files(d: Path) -> None:
    for f in d.glob("slot-*.bin"):
        f.unlink(missing_ok=True)
    (d / "manifest.json").unlink(missing_ok=True)


def trim_slot_disk(limit_mib: int) -> int:
    """Delete the oldest saved slots until all of them fit ``limit_mib``;
    the number of bytes deleted."""
    root = _cache_dir() / "slots"
    files = []
    for f in root.glob("*/slot-*.bin"):
        try:
            st = f.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, f))
    total = sum(size for _, size, _ in files)
    cap = max(0, limit_mib) * _MIB
    dropped = 0
    for _mtime, size, f in sorted(files, key=lambda t: t[0]):
        if total <= cap:
            break
        f.unlink(missing_ok=True)
        total -= size
        dropped += size
    return dropped


async def _slot_action(client: Any, m: Loaded, slot: int, action: str, filename: str) -> Dict[str, Any]:
    try:
        r = await client.post(f"http://127.0.0.1:{m.port}/slots/{slot}?action={action}",
                              json={"filename": filename})
        data = r.json() if r.status_code == 200 else {}
        if r.status_code != 200:
            log.info("slot %s %s of %s answered %s: %s", slot, action, m.name, r.status_code, r.text[:200])
        return data if isinstance(data, dict) else {}
    except (httpx.HTTPError, ValueError) as exc:
        log.info("slot %s %s of %s failed: %s", slot, action, m.name, exc)
        return {}


async def save_slots(m: Loaded) -> Dict[str, Any]:
    """Every slot of ``m`` that holds a prompt, written to its slot folder
    with a manifest of what it belongs to; the oldest saves are dropped past
    the disk limit. Nothing when ``m`` is not a llama-server with disk saving."""
    if not _disk_on(m) or m.proc.poll() is not None or m.slots <= 0:
        return {"saved": 0, "tokens": 0, "bytes": 0}
    d = _slot_dir(m.name)
    d.mkdir(parents=True, exist_ok=True)
    saved: List[Dict[str, Any]] = []
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=SLOT_IO_TIMEOUT) as client:
        for i in range(m.slots):
            fname = f"slot-{i}.bin"
            data = await _slot_action(client, m, i, "save", fname)
            n = int(_num(data.get("n_saved")))
            if n <= 0:
                (d / fname).unlink(missing_ok=True)
                continue
            saved.append({"file": fname, "tokens": n, "bytes": int(_num(data.get("n_written"))),
                          "saved_at": _now()})
    if saved:
        biggest = max(saved, key=lambda e: e["tokens"])
        if biggest["bytes"] and biggest["tokens"] >= 256:
            m.bytes_per_token = round(biggest["bytes"] / biggest["tokens"], 1)
        tmp = d / "manifest.json.tmp"
        tmp.write_text(json.dumps({"key": _slot_key(m), "slots": saved,
                                   "bytes_per_token": m.bytes_per_token}), encoding="utf-8")
        tmp.replace(d / "manifest.json")
    else:
        _drop_slot_files(d)
    trim_slot_disk(cache_settings()["disk_mib"])
    out = {"saved": len(saved), "tokens": sum(e["tokens"] for e in saved),
           "bytes": sum(e["bytes"] for e in saved), "ms": int((time.monotonic() - started) * 1000)}
    log.info("saved %d slot(s) of %s: %d tokens, %d MiB in %d ms", out["saved"], m.name, out["tokens"],
             out["bytes"] // _MIB, out["ms"])
    return out


async def restore_slots(m: Loaded) -> Dict[str, Any]:
    """The slots saved for ``m``'s model put back, the longest prompts first,
    as many as it has slots; saves made for another file, context length,
    KV type or build are deleted instead."""
    if not _disk_on(m) or m.slots <= 0:
        return {}
    d = _slot_dir(m.name)
    manifest = _read_manifest(d)
    if not manifest:
        return {}
    if manifest.get("key") != _slot_key(m):
        log.info("saved slots of %s were made for another start; dropped", m.name)
        _drop_slot_files(d)
        return {"stale": True}
    if isinstance(manifest.get("bytes_per_token"), (int, float)):
        m.bytes_per_token = float(manifest["bytes_per_token"])
    entries = [e for e in manifest.get("slots") or [] if isinstance(e, dict) and (d / str(e.get("file"))).is_file()]
    entries.sort(key=lambda e: -int(_num(e.get("tokens"))))
    started = time.monotonic()
    tokens = slots = 0
    async with httpx.AsyncClient(timeout=SLOT_IO_TIMEOUT) as client:
        for i, entry in enumerate(entries[:m.slots]):
            data = await _slot_action(client, m, i, "restore", str(entry["file"]))
            n = int(_num(data.get("n_restored")))
            if n > 0:
                tokens += n
                slots += 1
    out = {"slots": slots, "tokens": tokens, "ms": int((time.monotonic() - started) * 1000), "at": _now()}
    m.restored = out
    log.info("restored %d slot(s) of %s: %d tokens in %d ms", slots, m.name, tokens, out["ms"])
    return out


def prompt_head(body: Any) -> Optional[Dict[str, Any]]:
    """The part of a chat request every call of the same agent repeats: its
    leading system (or developer) messages and the fields that change how
    the template renders them, tools first among them. None for a request
    without one worth warming (:data:`HEAD_MIN_CHARS`)."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return None
    system: List[Dict[str, Any]] = []
    for msg in body["messages"]:
        if not isinstance(msg, dict) or msg.get("role") not in ("system", "developer"):
            break
        system.append({"role": msg["role"], "content": msg.get("content")})
    head: Dict[str, Any] = {"messages": system}
    for f in _HEAD_FIELDS:
        if body.get(f) not in (None, [], {}):
            head[f] = body[f]
    if len(head) == 1 and not system:
        return None
    if len(json.dumps(head, ensure_ascii=False)) < HEAD_MIN_CHARS:
        return None
    return head


class Heads:
    """The prompt heads each model was sent, newest last, at most
    ``warmup_prompts`` per model, in ``.cache/heads/<model>.json``. A new
    head is written at once (they are few: one per agent and tool set); the
    use counts of known ones on :meth:`flush`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: Dict[str, "OrderedDict[str, Dict[str, Any]]"] = {}
        self._dirty: set = set()

    def _file(self, model: str) -> Path:
        return _cache_dir() / "heads" / f"{model}.json"

    def _entries(self, model: str) -> "OrderedDict[str, Dict[str, Any]]":
        entries = self._by_model.get(model)
        if entries is None:
            entries = OrderedDict()
            try:
                raw = json.loads(self._file(model).read_text(encoding="utf-8"))
                for e in raw if isinstance(raw, list) else []:
                    if isinstance(e, dict) and e.get("key") and isinstance(e.get("head"), dict):
                        entries[str(e["key"])] = e
            except (OSError, ValueError):
                pass
            self._by_model[model] = entries
        return entries

    def _write(self, model: str) -> None:
        f = self._file(model)
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps(list(self._entries(model).values()), ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
            self._dirty.discard(model)
        except OSError as exc:
            log.warning("could not write %s: %s", f, exc)

    def note(self, model: str, body: Any, keep: int) -> Optional[str]:
        """Count ``body``'s head for ``model``; its key, or None for none."""
        if keep <= 0:
            return None
        head = prompt_head(body)
        if head is None:
            return None
        import hashlib
        key = hashlib.sha1(json.dumps(head, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        with self._lock:
            entries = self._entries(model)
            entry = entries.pop(key, None)
            fresh = entry is None
            if entry is None:
                entry = {"key": key, "head": head, "first_seen": _now(), "uses": 0,
                         "chars": len(json.dumps(head, ensure_ascii=False))}
            entry["uses"] = int(entry.get("uses") or 0) + 1
            entry["last_used"] = _now()
            entries[key] = entry
            while len(entries) > keep:
                entries.popitem(last=False)
                fresh = True
            if fresh:
                self._write(model)
            else:
                self._dirty.add(model)
        return key

    def list(self, model: str) -> List[Dict[str, Any]]:
        """``model``'s heads, oldest use first."""
        with self._lock:
            return [dict(e) for e in self._entries(model).values()]

    def models(self) -> List[str]:
        with self._lock:
            return self._models()

    def flush(self) -> None:
        with self._lock:
            for model in list(self._dirty):
                self._write(model)

    def clear(self, model: Optional[str] = None) -> None:
        with self._lock:
            for name in ([model] if model else self._models()):
                self._by_model[name] = OrderedDict()
                self._file(name).unlink(missing_ok=True)
                self._dirty.discard(name)

    def _models(self) -> List[str]:
        d = _cache_dir() / "heads"
        on_disk = {f.stem for f in d.glob("*.json")} if d.is_dir() else set()
        return sorted(on_disk | {k for k, v in self._by_model.items() if v})


heads = Heads()


async def warm_up(m: Loaded) -> Dict[str, Any]:
    """Send each prompt head kept for ``m`` once, oldest first, with a
    one-character question and one token to write, straight to its server
    (not counted as gateway calls). A head already in the cache costs a few
    milliseconds; the rest are computed now instead of on an agent's call."""
    c = cache_settings()
    if m.kind != "chat" or not (c["enabled"] and c["warmup"]):
        m.warmup = {}
        return m.warmup
    todo = heads.list(m.name)[-c["warmup_prompts"]:] if c["warmup_prompts"] else []
    w = m.warmup = {"state": "running" if todo else "idle", "total": len(todo), "done": 0, "errors": 0,
                    "prompt_tokens": 0, "cached_tokens": 0, "ms": 0, "at": _now()}
    if not todo:
        return w
    url = f"http://127.0.0.1:{m.port}/v1/chat/completions"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        for entry in todo:
            if state.loaded.get(m.name) is not m or m.proc.poll() is not None:
                w["state"] = "stopped"
                return w
            head = entry["head"]
            payload = {k: v for k, v in head.items() if k != "messages"}
            payload.update({"model": "default_model" if m.engine == "mlx" else m.name,
                            "messages": [*head.get("messages", []), {"role": "user", "content": "."}],
                            "max_tokens": 1, "stream": False})
            try:
                r = await client.post(url, json=payload)
                stats = call_stats(r.content[-_USAGE_TAIL_BYTES:]) if r.status_code == 200 else None
            except httpx.HTTPError:
                stats = None
            if stats is None:
                w["errors"] += 1
            else:
                w["prompt_tokens"] += stats["prompt_tokens"]
                w["cached_tokens"] += stats["cached_tokens"]
            w["done"] += 1
            w["ms"] = int((time.monotonic() - started) * 1000)
    w["state"] = "done"
    log.info("warmed %s with %d prompt head(s) in %d ms (%d of %d tokens were cached)", m.name,
             w["done"], w["ms"], w["cached_tokens"], w["prompt_tokens"])
    return w


_warmups: Dict[str, "asyncio.Task[Any]"] = {}


def start_warm_up(m: Loaded) -> bool:
    """Run :func:`warm_up` for ``m`` in the background; False when one runs."""
    task = _warmups.get(m.name)
    if task is not None and not task.done():
        return False
    _warmups[m.name] = asyncio.get_running_loop().create_task(warm_up(m))
    return True


async def _slot_count(m: Loaded) -> int:
    """llama-server's number of slots (``-np``, or what it chose)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://127.0.0.1:{m.port}/props")
            return int(_num(r.json().get("total_slots"))) if r.status_code == 200 else 0
    except (httpx.HTTPError, ValueError, AttributeError):
        return 0


_kv_geometry: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def kv_geometry(path: Path) -> Optional[Dict[str, Any]]:
    """Layers and attention shape of a chat model, for its KV size: from the
    GGUF header or an MLX folder's config.json, cached by modification time.
    Models with sliding-window or linear-attention layers keep less than this
    says, so the figure is an upper bound until a slot save measures it."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _kv_geometry.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    geo: Optional[Dict[str, Any]] = None
    try:
        if path.is_dir():
            cfg = mlx_config(path) or {}
            c = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
            layers, heads_n = int(c.get("num_hidden_layers") or 0), int(c.get("num_attention_heads") or 0)
            kv = int(c.get("num_key_value_heads") or heads_n)
            dim = int(c.get("head_dim") or (int(c.get("hidden_size") or 0) // heads_n if heads_n else 0))
            if layers and kv and dim:
                geo = {"kv_heads_total": layers * kv, "key_length": dim, "value_length": dim}
        else:
            module = _structure_module()
            md = module.parse_gguf(path)["metadata"] if module is not None else {}
            arch = md.get("general.architecture") or ""
            layers = int(_num(md.get(f"{arch}.block_count")))
            kv = md.get(f"{arch}.attention.head_count_kv", md.get(f"{arch}.attention.head_count"))
            heads_n = md.get(f"{arch}.attention.head_count")
            if isinstance(heads_n, list):
                heads_n = max(heads_n) if heads_n else 0
            total = int(sum(kv)) if isinstance(kv, list) else int(_num(kv)) * layers
            key_len = int(_num(md.get(f"{arch}.attention.key_length")))
            if not key_len and heads_n:
                key_len = int(_num(md.get(f"{arch}.embedding_length"))) // int(_num(heads_n))
            val_len = int(_num(md.get(f"{arch}.attention.value_length"))) or key_len
            if total and key_len:
                geo = {"kv_heads_total": total, "key_length": key_len, "value_length": val_len}
    except Exception:  # noqa: BLE001 - an estimate; an unreadable header leaves it out
        log.debug("kv geometry of %s failed", path, exc_info=True)
    _kv_geometry[str(path)] = (mtime, geo)
    return geo


def kv_bytes_per_token(m: Loaded) -> Tuple[Optional[float], bool]:
    """KV bytes one token takes in ``m``, and whether that was measured."""
    if m.bytes_per_token:
        return m.bytes_per_token, True
    geo = kv_geometry(MODELS_DIR / m.file)
    if not geo:
        return None, False
    per = _KV_TYPE_BYTES.get(m.kv_type if m.engine == "llama" else "f16", 2.0)
    return round(geo["kv_heads_total"] * (geo["key_length"] + geo["value_length"]) * per, 1), False


def _dir_bytes(d: Path, pattern: str = "*") -> int:
    total = 0
    for f in d.glob(pattern):
        try:
            total += f.stat().st_size if f.is_file() else 0
        except OSError:
            pass
    return total


def cache_overview() -> Dict[str, Any]:
    """Settings, what each loaded chat model holds and takes, what is kept
    on disk, and the memory it all comes out of."""
    c = cache_settings()
    _drop_dead()
    mem = memory()
    try:
        hw = hardware()
    except Exception:  # noqa: BLE001 - the GPU budget is informative
        hw = {}
    models: List[Dict[str, Any]] = []
    for m in sorted(state.loaded.values(), key=lambda x: x.name):
        if m.kind != "chat":
            continue
        per, measured = kv_bytes_per_token(m)
        try:
            weights = sum(f.stat().st_size for f in ((MODELS_DIR / m.file).rglob("*") if (MODELS_DIR / m.file).is_dir()
                                                     else split_parts(MODELS_DIR / m.file)) if f.is_file())
        except OSError:
            weights = 0
        expected = cache_flags(m.engine, m.name, c)
        models.append({
            "name": m.name, "file": m.file, "engine": m.engine, "context_length": m.context_length or None,
            "slots": m.slots or None, "kv_type": m.kv_type if m.engine == "llama" else None,
            "bytes_per_token": per, "bytes_per_token_measured": measured,
            # llama-server allocates the whole context at start; mlx-lm grows it.
            "kv_bytes": int(per * m.context_length) if per and m.engine == "llama" and m.context_length else None,
            "weights_bytes": weights, "rss_bytes": (mem.get("process_rss_bytes") or {}).get(m.name),
            "ram_cache_bytes": c["ram_mib"] * _MIB if c["enabled"] else 0,
            "restored": m.restored or None, "warmup": m.warmup or None,
            "heads": len(heads.list(m.name)), "pending": tuple(m.cache_flags) != expected,
            "disk_bytes": _dir_bytes(_slot_dir(m.name), "slot-*.bin"),
        })
    stored: List[Dict[str, Any]] = []
    slots_root = _cache_dir() / "slots"
    names = {d.name for d in slots_root.iterdir() if d.is_dir()} if slots_root.is_dir() else set()
    for name in sorted(names | set(heads.models())):
        manifest = _read_manifest(_slot_dir(name))
        kept = heads.list(name)
        disk = _dir_bytes(_slot_dir(name), "slot-*.bin")
        if not disk and not kept:
            continue
        stored.append({"name": name, "loaded": name in state.loaded,
                       "disk_bytes": disk,
                       "tokens": sum(int(_num(e.get("tokens"))) for e in manifest.get("slots") or []
                                     if isinstance(e, dict)),
                       "saved_at": max((str(e.get("saved_at") or "") for e in manifest.get("slots") or []
                                        if isinstance(e, dict)), default=None),
                       "heads": [{"key": e["key"], "uses": e.get("uses"), "chars": e.get("chars"),
                                  "last_used": e.get("last_used")} for e in reversed(kept)]})
    return {"settings": c, "defaults": cache_defaults(), "kv_types": list(CACHE_KV_TYPES),
            "limits": {k: list(v) for k, v in CACHE_LIMITS.items()},
            "models": models, "stored": stored,
            "disk_bytes": _dir_bytes(slots_root, "*/slot-*.bin") if slots_root.is_dir() else 0,
            "memory": {"ram_total_bytes": mem.get("ram_total_bytes"), "limit_bytes": memory_limit(),
                       "ram_available_bytes": mem.get("ram_available_bytes"),
                       "gpu_budget_bytes": hw.get("gpu_bytes"), "unified": hw.get("kind") == "apple"},
            "pending": [x["name"] for x in models if x["pending"]]}


def build_command(path: Path, name: str, port: int, context_length: int, gpu_layers: int,
                  threads: Optional[int], cache: Tuple[str, ...] = ()) -> List[str]:
    """llama-server's command line; ``cache`` is :func:`cache_flags`."""
    # -ngl larger than the layer count means "all"; llama-server has no -1.
    ngl = 999 if gpu_layers is None or gpu_layers < 0 else gpu_layers
    cmd = [llama_bin(), "-m", str(path), "--port", str(port), "--host", LLAMA_HOST,
           "-c", str(context_length), "-ngl", str(ngl), "--alias", name]
    if threads:
        cmd += ["-t", str(threads)]
    return cmd + list(cache)


async def wait_healthy(port: int, proc: Any, timeout: float = LOAD_TIMEOUT_SECONDS) -> None:
    """Poll the server's /health until it answers 200. llama-server answers
    503 while the weights load and a speech worker does not listen until its
    model is loaded, so only a dead process or the deadline fails this."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"the model server exited with code {proc.returncode}")
            try:
                r = await client.get(f"http://127.0.0.1:{port}/health")
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"the model server did not become healthy within {int(timeout)} s")


def _stop(m: Loaded) -> None:
    try:
        m.proc.terminate()
        try:
            m.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            m.proc.kill()
            m.proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - the process may already be gone
        log.debug("stopping %s failed", m.name, exc_info=True)


def _drop_dead() -> None:
    for name, m in list(state.loaded.items()):
        if m.proc.poll() is not None:
            log.warning("the server for %s exited (code %s)", name, m.proc.returncode)
            state.loaded.pop(name, None)


def build_speech_command(path: Path, engine: str, name: str, port: int,
                         threads: Optional[int]) -> List[str]:
    cmd = [engine_python(engine), str(SPEECH_WORKER), "--engine", engine, "--model", str(path),
           "--port", str(port), "--host", LLAMA_HOST, "--name", name]
    if threads:
        cmd += ["--threads", str(threads)]
    return cmd


async def load_model(file: str, context_length: int, gpu_layers: int,
                     threads: Optional[int], keep: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Start ``file``'s server, evicting the least recently used model of
    its pool when the pool is full; never one named in ``keep``."""
    path = _model_path(file)
    engine = speech_engine_of(path)
    speech = engine is not None
    mlx_cfg = mlx_config(path) if not speech else None
    if mlx_cfg is not None:
        engine = "mlx"
        if not mlx_platform():
            raise HTTPException(status_code=409, detail=f"{path.name} is an MLX model, and MLX runs on "
                                                        "Apple silicon only")
    if engine is None and not path.name.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail=f"{path.name} is neither a GGUF file, an MLX model "
                                                    "nor a speech model this runtime can load")
    if engine is not None and not engines().get(engine):
        raise HTTPException(status_code=409, detail=f"the {engine} engine is not installed in this "
                                                    "runtime; install it on the Models page first")
    engine = engine or "llama"
    kind = ENGINE_KIND.get(engine, "chat")
    name = model_name(path.name)
    evicted: List[str] = []
    async with _lock():
        _drop_dead()
        current = state.loaded.get(name)
        if current is not None:
            if engine != "llama" or current.context_length == context_length:
                current.touch()
                return {"ok": True, "name": name, "file": path.name, "port": current.port,
                        "context_length": current.context_length or None, "engine": current.engine,
                        "kind": current.kind, "already_loaded": True, "evicted": []}
            # A different context length needs a restart of that server.
            await asyncio.to_thread(_stop, state.loaded.pop(name))
        # Chat and speech models are two pools: a speech model never evicts
        # the chat model, and the other way round.
        cap = MAX_SPEECH_LOADED if speech else MAX_LOADED
        while True:
            pool = [m for m in state.loaded.values() if (m.kind in SPEECH_KINDS) == speech]
            if len(pool) < cap:
                break
            spare = [m for m in pool if m.name not in keep]
            if not spare:
                break  # over the cap for now; the next load evicts
            lru = min(spare, key=lambda m: m.last_used)
            state.loaded.pop(lru.name, None)
            await save_slots(lru)
            await asyncio.to_thread(_stop, lru)
            evicted.append(lru.file)
            log.info("evicted %s to make room for %s", lru.name, name)
        port = _next_port()
        cache = cache_flags(engine, name)
        if "--slot-save-path" in cache:
            _slot_dir(name).mkdir(parents=True, exist_ok=True)
        if speech:
            cmd = build_speech_command(path, engine, name, port, threads)
        elif engine == "mlx":
            cmd = build_mlx_command(path, port, cache)
        else:
            cmd = build_command(path, name, port, context_length, gpu_layers, threads, cache)
        logfile = _log_path(name)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with open(logfile, "wb") as fh:
            try:
                env = None
                if speech:
                    env = {**os.environ, "MODELS_VOICES_DIR": str(voices_dir())}
                if engine == "llama" and Path(cmd[0]).is_file():
                    # An installed build carries its shared libraries beside it.
                    lib = str(Path(cmd[0]).parent)
                    env = {**os.environ,
                           "LD_LIBRARY_PATH": os.pathsep.join(filter(None, [lib, os.environ.get("LD_LIBRARY_PATH")]))}
                proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env)
            except FileNotFoundError:
                if engine in ENGINE_VENVS:
                    raise HTTPException(status_code=409, detail=f"the {engine} engine is not installed in "
                                                                "this runtime; install it on the Models page")
                if engine != "llama":
                    raise HTTPException(status_code=500, detail=f"cannot start {SPEECH_PYTHON} "
                                                                "(MODELS_SPEECH_PYTHON)")
                raise HTTPException(status_code=409, detail="llama.cpp is not installed in this runtime; "
                                                            "install it on the Models page (Local tab)")
        ctx = 0 if speech else (mlx_context(mlx_cfg) if mlx_cfg is not None else context_length)
        harmony = bool(mlx_cfg) and str(mlx_cfg.get("model_type")) == "gpt_oss"
        try:
            await wait_healthy(port, proc)
        except Exception as exc:
            await asyncio.to_thread(_stop, Loaded(name, path.name, port, proc, ctx, engine, kind))
            raise HTTPException(status_code=502, detail=f"{exc}. Log tail: {_log_tail(name)}")
        m = Loaded(name=name, file=path.name, port=port, proc=proc, context_length=ctx,
                   engine=engine, kind=kind, harmony=harmony, gpu_layers=gpu_layers, threads=threads,
                   cache_flags=cache if kind == "chat" else ())
        if engine == "llama":
            m.kv_type = cache[cache.index("-ctk") + 1] if "-ctk" in cache else "f16"
            m.slots = await _slot_count(m)
            await restore_slots(m)
        state.loaded[name] = m
    if kind == "chat":
        start_warm_up(m)
    return {"ok": True, "name": name, "file": path.name, "port": port,
            "context_length": ctx or None, "engine": engine, "kind": kind,
            "already_loaded": False, "evicted": evicted, "restored": m.restored or None}


async def unload_model(file: str) -> Dict[str, Any]:
    async with _lock():
        m = _find_loaded(_safe_file(file))
        if m is None:
            return {"ok": True, "unloaded": False}
        state.loaded.pop(m.name, None)
        saved = await save_slots(m)
        await asyncio.to_thread(_stop, m)
    return {"ok": True, "unloaded": True, "name": m.name, "engine": m.engine, "kind": m.kind,
            "saved": saved if saved.get("saved") else None}


# ── Memory ───────────────────────────────────────────────────────────────────

def _meminfo_linux() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * 1024
    return {"total": out.get("MemTotal", 0), "available": out.get("MemAvailable", out.get("MemFree", 0))}


def _meminfo_macos() -> Dict[str, int]:
    total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                               timeout=5).stdout.strip() or 0)
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    page = 4096
    m = re.search(r"page size of (\d+) bytes", vm)
    if m:
        page = int(m.group(1))
    pages: Dict[str, int] = {}
    for line in vm.splitlines():
        key, _, val = line.partition(":")
        val = val.strip().rstrip(".")
        if val.isdigit():
            pages[key.strip()] = int(val)
    available = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative",
                                              "Pages purgeable")) * page
    return {"total": total, "available": available}


def _rss(pid: int) -> Optional[int]:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        return int(out) * 1024 if out else None
    except Exception:  # noqa: BLE001 - no ps in a slim image, or the process just exited
        return None


def _gpu() -> Optional[List[Dict[str, Any]]]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:  # noqa: BLE001 - a driver that hangs or is broken
        return None
    gpus = []
    for i, line in enumerate(out.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            gpus.append({"index": i, "memory_total_bytes": int(parts[0]) * 1024 * 1024,
                         "memory_used_bytes": int(parts[1]) * 1024 * 1024})
    return gpus or None


def memory() -> Dict[str, Any]:
    ram: Dict[str, int] = {"total": 0, "available": 0}
    try:
        if Path("/proc/meminfo").exists():
            ram = _meminfo_linux()
        elif platform.system() == "Darwin":
            ram = _meminfo_macos()
    except Exception:  # noqa: BLE001 - memory numbers are informative, never fatal
        log.debug("reading memory failed", exc_info=True)
    rss = {name: _rss(getattr(m.proc, "pid", 0)) for name, m in state.loaded.items()}
    return {"ram_total_bytes": ram.get("total") or None,
            "ram_available_bytes": ram.get("available") or None,
            "process_rss_bytes": rss, "gpu": _gpu()}


# ── App and auth ─────────────────────────────────────────────────────────────

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global TOKEN
    if not TOKEN and TOKEN_FILE:
        TOKEN = token_from_file(TOKEN_FILE)
    if not TOKEN:
        raise RuntimeError("MODELS_TOKEN is not set; the model runtime refuses to run without one")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    interrupted = jobs.load()
    if interrupted:
        log.info("%d job(s) were interrupted by the last restart", interrupted)
    usage.load()
    try:
        yield
    finally:
        usage.flush()
        heads.flush()
        # Docker stops only this process, so its servers are still up to
        # save their slots; the hub's own stop asks for that first
        # (POST /cache/save), since it signals the whole process group.
        for m in list(state.loaded.values()):
            try:
                await asyncio.wait_for(save_slots(m), timeout=SLOT_IO_TIMEOUT)
            except Exception:  # noqa: BLE001 - a save that fails must not keep the others running
                log.warning("saving the slots of %s at shutdown failed", m.name, exc_info=True)
            _stop(m)
        state.loaded.clear()


app = FastAPI(title="Agents Hub model runtime", lifespan=lifespan)


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


auth = [Depends(require_token)]


class DownloadBody(BaseModel):
    repo: str
    #: A GGUF file in the repo, or
    file: str = ""
    #: the name of a speech model GET /hf/files listed under ``packages``.
    package: str = ""
    revision: str = "main"


class LoadBody(BaseModel):
    file: str
    context_length: int = Field(default=4096, ge=256, le=1_048_576)
    gpu_layers: int = -1
    threads: Optional[int] = Field(default=None, ge=1, le=512)


class UnloadBody(BaseModel):
    file: str


def _structure_source() -> Path:
    """The file the structure reader comes from: the image's vendored copy,
    or in host mode the repository's own."""
    here = Path(__file__).resolve()
    vendored = here.with_name("hub_model_structure.py")
    return vendored if vendored.is_file() else here.parents[2] / "providers" / "model_structure.py"


def code_version() -> str:
    """A digest of this service's own code and the structure reader it
    loads: the hub restarts a runtime it started when they changed under it."""
    import hashlib
    h = hashlib.sha1()
    for f in (Path(__file__).resolve(), SPEECH_WORKER, SPEECH_WORKER.with_name("openvoice_vc.py"),
              _structure_source(), VOICE_ENHANCE):
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


VERSION = code_version()


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "loaded": len(state.loaded), "max_loaded": MAX_LOADED,
            "max_speech_loaded": MAX_SPEECH_LOADED, "mode": "docker" if in_docker() else "host",
            "version": VERSION, "pid": os.getpid()}


@app.get("/models", dependencies=auth)
async def get_models() -> Dict[str, Any]:
    _drop_dead()
    models, found = await asyncio.to_thread(lambda: (list_models(), engines()))
    return {"models_dir": str(MODELS_DIR), "max_loaded": MAX_LOADED,
            "max_speech_loaded": MAX_SPEECH_LOADED, "models": models, "engines": found}


@app.delete("/models/{file}", dependencies=auth)
async def delete_model(file: str) -> Dict[str, Any]:
    path = _model_path(file)
    if _find_loaded(path.name) is not None:
        raise HTTPException(status_code=409, detail=f"{path.name} is loaded; unload it first")
    if path.is_dir():
        shutil.rmtree(path)
        deleted = [path.name]
    else:
        deleted = []
        for part in split_parts(path):
            part.unlink()
            deleted.append(part.name)
    return {"ok": True, "deleted": path.name, "files": deleted}


def _structure_module() -> Any:
    """The hub's GGUF reader (providers/model_structure.py): the copy the
    image vendors as ``hub_model_structure``, or in host mode the
    repository's file, loaded by path so the hub's ``providers`` package (and
    its imports) never loads here. None when neither is there."""
    try:
        import hub_model_structure  # type: ignore[import-not-found]
        return hub_model_structure
    except ImportError:
        pass
    try:
        from providers import model_structure  # type: ignore[import-not-found]
        return model_structure
    except ImportError:
        pass
    src = _structure_source()
    if not src.is_file():
        return None
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("hub_model_structure", src)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        sys.modules["hub_model_structure"] = module
        return module
    except Exception:  # noqa: BLE001 - a reader that fails to import is a missing reader
        log.warning("loading %s failed", src, exc_info=True)
        return None


def _structure_reader() -> Any:
    """``structure_from_file`` from :func:`_structure_module`."""
    module = _structure_module()
    return getattr(module, "structure_from_file", None) if module is not None else None


@app.get("/models/{file}/structure", dependencies=auth)
async def model_structure(file: str) -> Dict[str, Any]:
    path = _model_path(file)
    structure_from_file = _structure_reader()
    if structure_from_file is None:
        raise HTTPException(status_code=501, detail="the structure reader is not installed in "
                                                    "this runtime (hub_model_structure.py)")
    try:
        return await asyncio.to_thread(structure_from_file, str(path))
    except Exception as exc:  # noqa: BLE001 - a file the parser cannot read
        raise HTTPException(status_code=422, detail=f"cannot read {path.name}: {exc}")


def _check_repo(repo: str, revision: str) -> None:
    if not _REPO_RE.match(repo):
        raise HTTPException(status_code=400, detail="repo must look like org/name")
    if not _REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="bad revision")


@app.post("/download", dependencies=auth)
async def download(body: DownloadBody) -> Dict[str, Any]:
    repo, revision = body.repo.strip(), body.revision.strip() or "main"
    _check_repo(repo, revision)
    if body.package.strip():
        return await _download_package(repo, revision, body.package.strip())
    src = body.file.strip().lstrip("/")
    if not src or ".." in src.split("/") or not src.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="file must be a .gguf path inside the repo")
    dest_name = _safe_file(src.rsplit("/", 1)[-1])
    running = jobs.running_for(dest_name)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"{dest_name} is already downloading (job {running['id']})")
    if (MODELS_DIR / dest_name).is_file():
        raise HTTPException(status_code=409, detail=f"{dest_name} is already here; delete it to download again")
    part = MODELS_DIR / f"{dest_name}.part"
    resuming = part.is_file() and part.stat().st_size > 0
    job = jobs.create("hf_download", f"{repo}/{src}",
                      meta={"repo": repo, "file": src, "revision": revision, "dest": dest_name})
    url = f"{HF_BASE}/{repo}/resolve/{revision}/{src}"
    threading.Thread(target=run_download, args=(job["id"], url, MODELS_DIR / dest_name),
                     name=f"download-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "file": dest_name, "resuming": resuming}


async def _download_package(repo: str, revision: str, name: str) -> Dict[str, Any]:
    """A speech model: the package of that name in the repo's tree, so the
    files fetched are the ones the listing showed and nothing a caller
    picked by hand."""
    _safe_file(name)
    packages = speech_packages(repo, await _hf_tree_or_error(repo, revision))
    package = next((p for p in packages if p["name"] == name), None)
    if package is None:
        raise HTTPException(status_code=404, detail=f"no speech model {name!r} in {repo} at {revision}")
    running = jobs.running_for(name)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"{name} is already downloading (job {running['id']})")
    if (MODELS_DIR / name).exists():
        raise HTTPException(status_code=409, detail=f"{name} is already here; delete it to download again")
    resuming = _staging_dir(name).is_dir()
    job = jobs.create("hf_package", f"{repo}: {name}",
                      meta={"repo": repo, "revision": revision, "dest": name, "engine": package["engine"],
                            "kind": package["kind"], "files": package["files"]})
    jobs.update(job["id"], total=package["size_bytes"])
    threading.Thread(target=run_package, args=(job["id"], repo, revision, package),
                     name=f"download-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "file": name, "resuming": resuming}


@app.get("/jobs", dependencies=auth)
async def list_jobs() -> Dict[str, Any]:
    return {"jobs": jobs.list()}


@app.get("/jobs/{job_id}", dependencies=auth)
async def get_job(job_id: str) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


def quantization_of(path: str) -> Optional[str]:
    m = _QUANT_RE.search(path.rsplit("/", 1)[-1])
    return m.group(1).upper() if m else None


#: Pages of 1000 entries GET /hf/files follows; a voice collection such as
#: rhasspy/piper-voices takes a handful.
HF_TREE_PAGES = 20


def hf_tree(repo: str, revision: str) -> List[Dict[str, Any]]:
    """Every entry of a repo's tree, following the Hub's ``Link: rel=next``
    cursor. Raises HTTPException for a missing repo, httpx errors as they are."""
    url: str = f"{HF_BASE}/api/models/{repo}/tree/{revision}"
    params: Optional[Dict[str, str]] = {"recursive": "true"}
    items: List[Dict[str, Any]] = []
    with http_client(timeout=20.0) as client:
        for _ in range(HF_TREE_PAGES):
            resp = client.get(url, params=params, headers=_hf_headers())
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"no repo {repo} at {revision}")
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Hugging Face answered HTTP {resp.status_code}")
            page = resp.json()
            items.extend(i for i in page or [] if isinstance(i, dict))
            nxt = (resp.links.get("next") or {}).get("url")
            if not nxt:
                break
            url, params = nxt, None
    return items


async def _hf_tree_or_error(repo: str, revision: str) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(hf_tree, repo, revision)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Hugging Face is unreachable: {exc}")


@app.get("/hf/files", dependencies=auth)
async def hf_files(repo: str, revision: str = "main") -> Dict[str, Any]:
    repo = repo.strip()
    if not _REPO_RE.match(repo) or not _REVISION_RE.match(revision) or ".." in revision:
        raise HTTPException(status_code=400, detail="repo must look like org/name")
    items = await _hf_tree_or_error(repo, revision)
    files = []
    for item in items:
        p = str(item.get("path") or "")
        if item.get("type") != "file" or not p.lower().endswith(".gguf"):
            continue
        size = (item.get("lfs") or {}).get("size") or item.get("size") or 0
        files.append({"file": p, "size_bytes": int(size), "quantization": quantization_of(p)})
    files.sort(key=lambda f: f["file"])
    packages = [{k: v for k, v in p.items() if k not in ("sizes", "save_as")}
                for p in speech_packages(repo, items)]
    for p in packages:
        p["downloaded"] = (MODELS_DIR / p["name"]).exists()
    info = await asyncio.to_thread(hf_model_info, repo)
    hw = await asyncio.to_thread(hardware)
    split_total: Dict[str, int] = {}
    for f in files:
        m = _SPLIT_RE.match(f["file"].rsplit("/", 1)[-1])
        if m:
            split_total[m.group("stem")] = split_total.get(m.group("stem"), 0) + f["size_bytes"]
    for f in files:
        m = _SPLIT_RE.match(f["file"].rsplit("/", 1)[-1])
        # A split model runs whole: every part's size counts for each part.
        model_bytes = split_total[m.group("stem")] if m else f["size_bytes"]
        if model_bytes and not f["file"].rsplit("/", 1)[-1].lower().startswith("mmproj"):
            f["fit"] = estimate_fit(model_bytes, hw, active_share=info.get("active_share"))
    return {"repo": repo, "revision": revision, "files": files, "packages": packages,
            "model": info, "hardware": hw}


# ── Hardware, fit and speed estimates ────────────────────────────────────────
#
# A model's answer is written one token at a time, and each token reads every
# weight it uses from memory once: generation speed is bounded by memory
# bandwidth over the bytes read per token. A dense model reads its whole file;
# a mixture of experts (Qwen3-30B-A3B, gpt-oss) only its active share. What
# fits is the file plus a working margin (the KV cache at the default 4096
# context and llama.cpp's buffers) against the memory the GPU may use.

GB = 1_000_000_000

#: Apple chips, memory bandwidth in GB/s. A Max comes in two GPU sizes with
#: different bandwidth: (cores at or above which, the higher figure).
_APPLE_BANDWIDTH: Dict[str, Any] = {
    "M1": 68, "M1 Pro": 200, "M1 Max": 400, "M1 Ultra": 800,
    "M2": 100, "M2 Pro": 200, "M2 Max": 400, "M2 Ultra": 800,
    "M3": 100, "M3 Pro": 150, "M3 Max": (300, 40, 400), "M3 Ultra": 819,
    "M4": 120, "M4 Pro": 273, "M4 Max": (410, 40, 546),
    "M5": 153,
}
#: A tier the table does not name yet (a newer chip): its last known figure.
_APPLE_TIER_FALLBACK = {"": 153, "Pro": 273, "Max": 546, "Ultra": 819}

#: NVIDIA cards by a name fragment, longest first when matched; GB/s.
_NVIDIA_BANDWIDTH = {
    "RTX 5090": 1792, "RTX 5080": 960, "RTX 5070 Ti": 896, "RTX 5070": 672, "RTX 5060 Ti": 448,
    "RTX 5060": 448, "RTX 4090": 1008, "RTX 4080 SUPER": 736, "RTX 4080": 717,
    "RTX 4070 Ti SUPER": 672, "RTX 4070 Ti": 504, "RTX 4070 SUPER": 504, "RTX 4070": 504,
    "RTX 4060 Ti": 288, "RTX 4060": 272, "RTX 3090 Ti": 1008, "RTX 3090": 936,
    "RTX 3080 Ti": 912, "RTX 3080": 760, "RTX 3070 Ti": 608, "RTX 3070": 448, "RTX 3060 Ti": 448,
    "RTX 3060": 360, "RTX 6000 Ada": 960, "RTX A6000": 768, "A6000": 768, "L40S": 864, "L40": 864,
    "L4": 300, "A10": 600, "T4": 320, "V100": 900, "A100 80GB": 2039, "A100": 1555,
    "H100 NVL": 3900, "H100 PCIe": 2000, "H100": 3350, "H200": 4800, "GH200": 4000, "B200": 8000,
}
_NVIDIA_FALLBACK = 500
#: Memory bandwidth assumed for a CPU the runtime cannot name (dual-channel
#: DDR4 or DDR5 desktop memory sits around here).
_CPU_FALLBACK = 50

#: Share of the bandwidth llama.cpp reaches in practice, before any
#: measurement on this machine replaces it.
_EFFICIENCY = {"apple": 0.7, "nvidia": 0.7, "cpu": 0.55}
#: Seconds every token costs whatever the model's size (kernel launches,
#: sampling): what keeps a 0.5B model near 200 tokens/s, not 600.
TOKEN_OVERHEAD_S = 0.003


def _token_rate(read_bytes: float, bytes_per_second: float) -> float:
    return 1.0 / (read_bytes / bytes_per_second + TOKEN_OVERHEAD_S)


def _apple_gpu_share(ram: int) -> float:
    """How much of the unified memory macOS lets the GPU wire by default."""
    return 0.75 if ram >= 64 * GB else 0.67


#: The default context a load gets, and the margin it needs on top of the file.
FIT_CONTEXT = 4096


def fit_margin(model_bytes: int) -> int:
    return int(model_bytes * 0.08 + 0.5 * GB)


#: Bits per weight of the common quantizations, for a model whose files are
#: not listed yet (a search result): its size at each is params × bits / 8.
QUANT_BITS = {"Q3_K_M": 3.9, "Q4_K_M": 4.85, "Q5_K_M": 5.7, "Q6_K": 6.6, "Q8_0": 8.5, "F16": 16.0}

#: Active parameters of mixtures of experts whose names do not say them.
_MOE_ACTIVE = {"gpt-oss-20b": 3.6e9, "gpt-oss-120b": 5.1e9, "mixtral-8x7b": 12.9e9,
               "mixtral-8x22b": 39e9, "qwen3-next": 3e9, "coder-next": 3e9,
               "llama-4-scout": 17e9, "llama-4-maverick": 17e9, "deepseek-v3": 37e9,
               "deepseek-r1": 37e9, "glm-4.5-air": 12e9, "glm-4.5": 32e9, "glm-4.6": 32e9,
               "kimi-k2": 32e9}
_ACTIVE_RE = re.compile(r"(?i)(?:^|[-_.])a(\d+(?:\.\d+)?)b(?:$|[-_.])")
_TOTAL_RE = re.compile(r"(?i)(?:^|[-_.])(\d+(?:\.\d+)?)b(?:$|[-_.])")

_hw_cache: Dict[str, Any] = {}
_HW_TTL = 600.0


def moe_active(name: str, total_params: Optional[float]) -> Optional[float]:
    """Active parameters of a mixture of experts, from its name
    (``...-30B-A3B``) or a table; None for a dense model or one that does
    not say."""
    low = name.lower()
    for key, active in _MOE_ACTIVE.items():
        if key in low:
            return active
    m = _ACTIVE_RE.search(name.rsplit("/", 1)[-1])
    if not m:
        return None
    active = float(m.group(1)) * 1e9
    if total_params and active >= total_params:
        return None
    return active


#: A mixture of experts reads more per token than its active experts: the
#: attention, embeddings and router are shared, and the experts it picks
#: change from token to token. Measured speeds of Qwen3-30B-A3B and
#: gpt-oss-20b on llama.cpp sit near 2.5 times the active share.
MOE_READ_FACTOR = 2.5


def active_share_of(name: str, total_params: Optional[float]) -> Optional[float]:
    """Share of the weights read per token: active over total parameters
    (the total from ``total_params`` or the name, ``30B``) times
    :data:`MOE_READ_FACTOR`, at most 1. None for a dense model."""
    active = moe_active(name, total_params)
    if active is None:
        return None
    total = total_params
    if not total:
        sizes = [float(x) * 1e9 for x in _TOTAL_RE.findall(name.rsplit("/", 1)[-1])]
        total = max(sizes) if sizes else None
    if not total or active >= total:
        return None
    return round(min(1.0, MOE_READ_FACTOR * active / total), 4)


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001 - not macOS, or sysctl missing
        return ""


def _apple_gpu_cores() -> Optional[int]:
    try:
        out = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"],
                             capture_output=True, text=True, timeout=15).stdout
        for gpu in json.loads(out or "{}").get("SPDisplaysDataType") or []:
            cores = str(gpu.get("sppci_cores") or "")
            if cores.isdigit():
                return int(cores)
    except Exception:  # noqa: BLE001 - informative only
        log.debug("system_profiler failed", exc_info=True)
    return None


def apple_bandwidth(chip: str, gpu_cores: Optional[int]) -> Tuple[Optional[int], bool]:
    """GB/s of an ``Apple M3 Max``-style chip, and whether the figure is the
    chip's own (False: a fallback for a chip the table does not know)."""
    m = re.search(r"\b(M\d+)(?:\s+(Pro|Max|Ultra))?\b", chip)
    if not m:
        return None, False
    key = m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
    value = _APPLE_BANDWIDTH.get(key)
    if isinstance(value, tuple):
        low, from_cores, high = value
        return (high if gpu_cores and gpu_cores >= from_cores else low), True
    if value:
        return value, True
    return _APPLE_TIER_FALLBACK.get(m.group(2) or ""), False


def nvidia_bandwidth(name: str) -> Tuple[int, bool]:
    for key in sorted(_NVIDIA_BANDWIDTH, key=len, reverse=True):
        if key.lower() in name.lower():
            return _NVIDIA_BANDWIDTH[key], True
    return _NVIDIA_FALLBACK, False


def _nvidia_gpus() -> List[Dict[str, Any]]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - a broken driver
        return []
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem = line.rpartition(",")
        if mem.strip().isdigit():
            gpus.append({"name": name.strip(), "memory_bytes": int(mem.strip()) * 1024 * 1024})
    return gpus


def _cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return _sysctl("machdep.cpu.brand_string") or platform.machine() or "CPU"


def _plain_cpu() -> Dict[str, Any]:
    return {"kind": "cpu", "name": platform.machine() or "CPU", "ram_bytes": 0, "gpu_bytes": 0,
            "gpu_count": 0, "bandwidth_gbps": _CPU_FALLBACK, "cpu_bandwidth_gbps": _CPU_FALLBACK,
            "known": False, "efficiency": _EFFICIENCY["cpu"], "calibrated": False}


def detect_hardware() -> Dict[str, Any]:
    """What the estimates run against: ``kind`` (apple, nvidia or cpu), a
    ``name``, the RAM, the memory the GPU may use (``gpu_bytes``, 0 without
    one), the bandwidth in GB/s and whether that figure is ``known`` or
    assumed. ``MODELS_BANDWIDTH_GBPS`` overrides the bandwidth."""
    ram = 0
    try:
        if Path("/proc/meminfo").exists():
            ram = _meminfo_linux().get("total", 0)
        elif platform.system() == "Darwin":
            ram = int(_sysctl("hw.memsize") or 0)
    except Exception:  # noqa: BLE001 - informative only
        log.debug("reading RAM failed", exc_info=True)
    hw: Dict[str, Any] = {"kind": "cpu", "name": _cpu_name(),
                          "ram_bytes": ram, "gpu_bytes": 0, "gpu_count": 0,
                          "bandwidth_gbps": _CPU_FALLBACK, "cpu_bandwidth_gbps": _CPU_FALLBACK,
                          "known": False}
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        chip = _sysctl("machdep.cpu.brand_string") or "Apple silicon"
        cores = _apple_gpu_cores()
        bw, known = apple_bandwidth(chip, cores)
        hw.update(kind="apple", name=chip + (f", {cores}-core GPU" if cores else ""),
                  gpu_bytes=int(ram * _apple_gpu_share(ram)), gpu_count=1,
                  bandwidth_gbps=bw or 100, cpu_bandwidth_gbps=bw or 100, known=known)
    else:
        gpus = _nvidia_gpus()
        if gpus:
            bws = [nvidia_bandwidth(g["name"]) for g in gpus]
            names = sorted({g["name"] for g in gpus})
            hw.update(kind="nvidia", name=(f"{len(gpus)} × " if len(gpus) > 1 else "") + ", ".join(names),
                      gpu_bytes=sum(g["memory_bytes"] for g in gpus), gpu_count=len(gpus),
                      # Layers split across cards run one after another: the
                      # slowest card sets the pace.
                      bandwidth_gbps=min(b for b, _ in bws), known=all(k for _, k in bws))
    override = os.environ.get("MODELS_BANDWIDTH_GBPS", "").strip()
    if override.replace(".", "", 1).isdigit():
        hw.update(bandwidth_gbps=float(override), known=True)
    hw["efficiency"] = _EFFICIENCY[hw["kind"]]
    hw["calibrated"] = False
    return hw


def calibrate(hw: Dict[str, Any]) -> Dict[str, Any]:
    """``hw`` with the efficiency measured on this machine: the median over
    the chat models the gateway served (llama-server reports how long it took
    to write how many tokens), each against its file size and bandwidth. Only
    models that fit the GPU count, a partly offloaded one runs slower for
    another reason."""
    sizes = {e["name"]: e.get("size_bytes") or 0 for e in list_models()
             if e.get("engine") == "llama" and e.get("kind") == "chat"}
    seen = []
    for row in usage.snapshot()["rows"]:
        tokens, ms = row.get("gen_tokens") or 0, row.get("gen_ms") or 0
        size = sizes.get(row["model"])
        if row.get("kind") != "chat" or tokens < 32 or ms <= 0 or not size:
            continue
        read = size * (active_share_of(row["model"], None) or 1.0)
        if hw["gpu_bytes"] and size + fit_margin(size) > hw["gpu_bytes"]:
            continue
        measured = tokens * 1000.0 / ms
        reading = 1.0 / measured - TOKEN_OVERHEAD_S
        if reading <= 0:
            continue
        seen.append({"model": row["model"], "tokens_per_second": round(measured, 1),
                     "efficiency": read / reading / (hw["bandwidth_gbps"] * GB)})
    if not seen:
        return hw
    effs = sorted(s["efficiency"] for s in seen)
    eff = effs[len(effs) // 2]
    return {**hw, "efficiency": round(max(0.15, min(1.0, eff)), 3), "calibrated": True,
            "measured": [{k: v for k, v in s.items() if k != "efficiency"} for s in seen]}


def hardware() -> Dict[str, Any]:
    """:func:`detect_hardware`, kept for a while (system_profiler takes a
    second), with the measured efficiency on top."""
    now = time.monotonic()
    if not _hw_cache or now - _hw_cache.get("at", 0) > _HW_TTL:
        try:
            detected = detect_hardware()
        except Exception:  # noqa: BLE001 - an estimate must never break a listing
            log.warning("detecting the hardware failed", exc_info=True)
            detected = _plain_cpu()
        _hw_cache.update(at=now, hw=detected)
    try:
        return calibrate(dict(_hw_cache["hw"]))
    except Exception:  # noqa: BLE001 - the uncalibrated figure still serves
        log.warning("calibrating the estimates failed", exc_info=True)
        return dict(_hw_cache["hw"])


def estimate_fit(model_bytes: int, hw: Dict[str, Any], *, active_share: Optional[float] = None) -> Dict[str, Any]:
    """Whether a model file of ``model_bytes`` runs here and how fast.

    ``verdict``: ``fits`` (in the GPU's memory, or RAM on a machine without
    one, with room to spare), ``tight`` (only just), ``offload`` (partly on
    the CPU, much slower) or ``no``. ``tokens_per_second`` is the expected
    generation speed, None for ``no``."""
    need = int(model_bytes) + fit_margin(int(model_bytes))
    read = model_bytes * (active_share or 1.0)
    eff = float(hw.get("efficiency") or 0.6)
    bw = float(hw.get("bandwidth_gbps") or _CPU_FALLBACK) * GB
    cpu_bw = float(hw.get("cpu_bandwidth_gbps") or _CPU_FALLBACK) * GB
    ram = int(hw.get("ram_bytes") or 0)
    gpu = int(hw.get("gpu_bytes") or 0)
    out: Dict[str, Any] = {"need_bytes": need, "context": FIT_CONTEXT, "active_share": active_share}
    budget = gpu or int(ram * 0.8)
    if budget and need <= budget * 0.9:
        verdict, tps = "fits", _token_rate(read, eff * bw)
    elif budget and need <= budget:
        verdict, tps = "tight", _token_rate(read, eff * bw)
    elif gpu and ram and need <= gpu + int(ram * 0.6) and hw.get("kind") == "nvidia":
        # The layers that do not fit run on the CPU, from system memory.
        on_gpu = gpu * 0.9 / need
        tps = 1.0 / (read * on_gpu / (eff * bw) + read * (1 - on_gpu) / (_EFFICIENCY["cpu"] * cpu_bw)
                     + TOKEN_OVERHEAD_S)
        verdict = "offload"
    elif hw.get("kind") == "apple" and ram and need <= ram * 0.9:
        # Past the GPU's share of unified memory: runs with fewer layers on
        # the GPU, at the CPU's pace for the rest.
        verdict, tps = "offload", _token_rate(read, _EFFICIENCY["cpu"] * cpu_bw * 0.7)
    else:
        verdict, tps = "no", None
    out.update(verdict=verdict, budget_bytes=budget,
               tokens_per_second=round(tps, 1) if tps else None)
    return out


def estimate_quants(params: float, hw: Dict[str, Any], *, active_share: Optional[float] = None) -> List[Dict[str, Any]]:
    """:func:`estimate_fit` at each of :data:`QUANT_BITS` for a model of
    ``params`` parameters, smallest first."""
    out = []
    for quant, bits in QUANT_BITS.items():
        size = int(params * bits / 8)
        out.append({"quant": quant, "size_bytes": size, **estimate_fit(size, hw, active_share=active_share)})
    return out


def best_quant(estimates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The largest quantization that fits with room, else the largest that
    fits at all, else the smallest (to say why not). F16 is never the pick:
    it is twice Q8_0 for no gain worth that here."""
    usable = [e for e in estimates if e["quant"] != "F16"]
    for ok in (("fits",), ("tight",), ("offload",)):
        hits = [e for e in usable if e["verdict"] in ok]
        if hits:
            return hits[-1]
    return usable[0] if usable else None


# ── Hugging Face search ──────────────────────────────────────────────────────

#: Purpose → the Hub tags a result must carry (each inner list is one query,
#: their results merged): chat models, coding models, embedding models, all
#: GGUF for llama.cpp; and the speech engines' own formats, which are not.
SEARCH_PURPOSES: Dict[str, List[List[str]]] = {
    "any": [[]],
    "chat": [["text-generation"]],
    "code": [["text-generation", "code"]],
    "embeddings": [["feature-extraction"], ["sentence-similarity"]],
    "transcription": [["ctranslate2", "automatic-speech-recognition"]],
    "speech": [["piper", "text-to-speech"], ["onnx", "text-to-speech"]],
}
#: Purposes the speech engines serve: no GGUF filter, and only repos one of
#: them runs (:func:`speech_engine_for`) stay in the results.
SPEECH_PURPOSES = ("transcription", "speech")
#: Speech repos without a task tag (the official Piper voices, Kokoro and
#: Kitten have none) are found by name: (tags, the word searched when the
#: person typed none; with a word, theirs is searched).
SPEECH_NAME_QUERIES: Dict[str, List[Tuple[List[str], str]]] = {
    "speech": [(["onnx"], "piper"), (["onnx"], "kokoro"), (["onnx"], "kitten-tts"), (["onnx"], "supertonic")],
    "transcription": [],
}
#: The repos the presets use, shown first when the search matches them: the
#: Hub reports no downloads for them, so a sort would bury them.
SPEECH_FEATURED = {"speech": ["rhasspy/piper-voices", "fastrtc/kokoro-onnx", "Supertone/supertonic-3",
                              "KittenML/kitten-tts-nano-0.8-int8", "ResembleAI/chatterbox",
                              "mlx-community/chatterbox-4bit", "myshell-ai/OpenVoiceV2"],
                   "transcription": ["Systran/faster-whisper-small", "Systran/faster-whisper-large-v3"]}


def speech_engine_for(repo: str, tags: List[str], purpose: str) -> Optional[str]:
    """The engine that would run a speech repo, from its name and tags, the
    same hints :func:`speech_packages` reads its files with; None for one
    none of them runs (an ONNX voice of another family)."""
    low = repo.lower()
    if purpose == "transcription":
        return "whisper" if "whisper" in low else None
    if "piper-plus" in low or "piper_plus" in low:
        return None  # a fork with its own runtime
    if "kokoro" in low:
        return "kokoro"
    if "kitten" in low:
        return "kitten"
    if "supertonic" in low:
        return "supertonic"
    if low == "resembleai/chatterbox":
        return "chatterbox"  # the multilingual weights live only here
    if low.startswith("mlx-community/chatterbox") and "turbo" not in low:
        return "chatterbox_mlx" if mlx_platform() else None  # MLX runs on Apple silicon only
    if "openvoice" in low:
        return "openvoice"  # its converter; a repo without one lists no package
    if "piper" in low or "piper" in tags:
        return "piper"
    return None
#: License filter → the Hub license tags it accepts.
SEARCH_LICENSES: Dict[str, List[str]] = {
    "any": [""],
    "permissive": ["apache-2.0", "mit"],
    "apache-2.0": ["apache-2.0"],
    "mit": ["mit"],
    "llama": ["llama3", "llama3.1", "llama3.2", "llama3.3", "llama4"],
    "gemma": ["gemma"],
}
SEARCH_SORTS = {"downloads": "downloads", "likes": "likes", "trending": "trendingScore",
                "updated": "lastModified"}
_SEARCH_EXPAND = ("gguf", "downloads", "likes", "lastModified", "pipeline_tag", "tags", "gated",
                  "trendingScore")
SEARCH_LIMIT = 30


def _license_of(tags: List[str]) -> Optional[str]:
    return next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), None)


def _search_item(raw: Dict[str, Any], hw: Dict[str, Any]) -> Dict[str, Any]:
    gguf = raw.get("gguf") if isinstance(raw.get("gguf"), dict) else {}
    tags = [str(t) for t in raw.get("tags") or []]
    if raw.get("_engine"):
        # A speech model: no parameter count or quantizations to estimate;
        # its packages, sizes and voices show once its files are listed.
        return {"repo": raw["id"], "task": raw.get("pipeline_tag"), "license": _license_of(tags),
                "downloads": int(raw.get("downloads") or 0), "likes": int(raw.get("likes") or 0),
                "updated": raw.get("lastModified"), "gated": bool(raw.get("gated")),
                "engine": raw["_engine"], "kind": ENGINE_KIND[raw["_engine"]],
                "packages": len(raw.get("_packages") or []),
                "size_min": min((p["size_bytes"] for p in raw.get("_packages") or []), default=0),
                "size_max": max((p["size_bytes"] for p in raw.get("_packages") or []), default=0),
                "params": None, "moe": False, "trending": raw.get("trendingScore")}
    params = float(gguf.get("total") or 0) or None
    arch = str(gguf.get("architecture") or "") or None
    share = active_share_of(raw["id"], params)
    item = {"repo": raw["id"], "task": raw.get("pipeline_tag"), "license": _license_of(tags),
            "downloads": int(raw.get("downloads") or 0), "likes": int(raw.get("likes") or 0),
            "updated": raw.get("lastModified"), "gated": bool(raw.get("gated")),
            "params": params, "architecture": arch,
            "context_length": int(gguf.get("context_length") or 0) or None,
            "moe": bool(share) or bool(arch and "moe" in arch), "active_share": share,
            "trending": raw.get("trendingScore")}
    if params:
        quants = estimate_quants(params, hw, active_share=share)
        item["estimates"] = quants
        item["best"] = best_quant(quants)
    return item


def hf_search(q: str, purpose: str, license: str, sort: str, limit: int) -> List[Dict[str, Any]]:
    """GGUF repos on the Hub matching ``q``, each query of the purpose and
    license combined, merged and sorted again (a query per combination: the
    Hub ANDs its filters, these are ORs)."""
    sort_key = SEARCH_SORTS.get(sort, "downloads")
    found: Dict[str, Dict[str, Any]] = {}
    speech = purpose in SPEECH_PURPOSES
    queries: List[Tuple[List[str], str]] = [(tags, q) for tags in SEARCH_PURPOSES.get(purpose, SEARCH_PURPOSES["any"])]
    if speech:
        queries += [(tags, q or word) for tags, word in SPEECH_NAME_QUERIES.get(purpose, [])]
        queries = list({(tuple(t), w): (t, w) for t, w in queries}.values())
    featured = [r for r in SPEECH_FEATURED.get(purpose, []) if not q or q.lower() in r.lower()] if speech else []
    with http_client(timeout=20.0) as client:
        for repo in featured:
            resp = client.get(f"{HF_BASE}/api/models/{repo}",
                              params=[("expand[]", e) for e in _SEARCH_EXPAND], headers=_hf_headers())
            raw = resp.json() if resp.status_code < 400 else None
            engine = speech_engine_for(repo, [str(t) for t in (raw or {}).get("tags") or []], purpose)
            allowed = SEARCH_LICENSES.get(license, [""])
            if (isinstance(raw, dict) and engine
                    and ("" in allowed or _license_of(raw.get("tags") or []) in allowed)):
                found[repo] = {**raw, "id": repo, "_engine": engine}
        for tags, search in queries:
            for lic in SEARCH_LICENSES.get(license, SEARCH_LICENSES["any"]):
                filters = ([] if speech else ["gguf"]) + [*tags] + ([f"license:{lic}"] if lic else [])
                params: List[Tuple[str, str]] = [("filter", f) for f in filters]
                # Speech results are thinned to what an engine runs: ask for more.
                fetch = min(100, limit * 3) if speech else limit
                params += [("sort", sort_key), ("direction", "-1"), ("limit", str(fetch))]
                params += [("expand[]", e) for e in _SEARCH_EXPAND]
                if search:
                    params.append(("search", search))
                resp = client.get(f"{HF_BASE}/api/models", params=params, headers=_hf_headers())
                if resp.status_code >= 400:
                    raise HTTPException(status_code=502, detail=f"Hugging Face answered HTTP {resp.status_code}")
                for raw in resp.json() or []:
                    if not isinstance(raw, dict) or not raw.get("id"):
                        continue
                    if speech:
                        engine = speech_engine_for(raw["id"], [str(t) for t in raw.get("tags") or []], purpose)
                        if engine is None:
                            continue
                        raw = {**raw, "_engine": engine}
                    found.setdefault(raw["id"], raw)
    rows = sorted(found.values(), key=lambda r: r.get(sort_key) or (0 if sort_key != "lastModified" else ""),
                  reverse=True)
    rows = [found[r] for r in featured if r in found] + [r for r in rows if r["id"] not in featured]
    if speech:
        rows = _with_packages(rows, limit)
    return rows[:limit]


def _with_packages(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The speech repos among ``rows`` that hold a package an engine runs,
    each with them listed (``_packages``): a name says little about the
    layout (Kokoro split into a file per voice, Piper forks), so each tree
    is read, several at a time, until ``limit`` are found."""
    from concurrent.futures import ThreadPoolExecutor

    def packages(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return speech_packages(raw["id"], hf_tree(raw["id"], "main"))
        except (HTTPException, httpx.HTTPError):
            return []

    kept: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(0, len(rows), 8):
            batch = rows[i:i + 8]
            for raw, pk in zip(batch, pool.map(packages, batch)):
                if pk:
                    kept.append({**raw, "_packages": pk})
            if len(kept) >= limit:
                break
    return kept


def hf_model_info(repo: str) -> Dict[str, Any]:
    """One repo's parameters, architecture, context and license from the
    Hub; empty when the Hub does not say (or cannot be reached: the file
    listing that asks has already succeeded, so this stays optional)."""
    try:
        with http_client(timeout=15.0) as client:
            resp = client.get(f"{HF_BASE}/api/models/{repo}",
                              params=[("expand[]", e) for e in ("gguf", "tags", "pipeline_tag")],
                              headers=_hf_headers())
        if resp.status_code >= 400:
            return {}
        raw = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    gguf = raw.get("gguf") if isinstance(raw.get("gguf"), dict) else {}
    params = float(gguf.get("total") or 0) or None
    share = active_share_of(repo, params)
    arch = str(gguf.get("architecture") or "") or None
    return {"params": params, "architecture": arch,
            "context_length": int(gguf.get("context_length") or 0) or None,
            "license": _license_of([str(t) for t in raw.get("tags") or []]),
            "task": raw.get("pipeline_tag"), "active_share": share,
            "moe": bool(share) or bool(arch and "moe" in arch)}


@app.get("/hf/search", dependencies=auth)
async def hf_search_route(q: str = "", purpose: str = "chat", license: str = "any",
                          sort: str = "downloads", limit: int = 20) -> Dict[str, Any]:
    """GGUF models on Hugging Face by text, purpose and license, each with
    what it takes to run here (:func:`estimate_quants`, :func:`best_quant`)."""
    q = q.strip()[:100]
    if purpose not in SEARCH_PURPOSES or license not in SEARCH_LICENSES or sort not in SEARCH_SORTS:
        raise HTTPException(status_code=400, detail="unknown purpose, license or sort")
    limit = max(1, min(int(limit), SEARCH_LIMIT))
    try:
        raws = await asyncio.to_thread(hf_search, q, purpose, license, sort, limit)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Hugging Face is unreachable: {exc}")
    hw = await asyncio.to_thread(hardware)
    return {"results": [_search_item(r, hw) for r in raws], "hardware": hw}


@app.get("/hardware", dependencies=auth)
async def get_hardware() -> Dict[str, Any]:
    """What fit and speed estimates assume about this machine."""
    return await asyncio.to_thread(hardware)


# ── Speech engines ───────────────────────────────────────────────────────────

def torch_index() -> str:
    """Where torch comes from: PyPI, except on Linux without an NVIDIA GPU,
    where PyPI's build carries gigabytes of CUDA the CPU never uses and the
    CPU-only build is taken instead. ``MODELS_TORCH_INDEX`` overrides it
    (empty for PyPI)."""
    pinned = os.environ.get("MODELS_TORCH_INDEX")
    if pinned is not None:
        return pinned.strip()
    if platform.system() == "Linux" and shutil.which("nvidia-smi") is None:
        return "https://download.pytorch.org/whl/cpu"
    return ""


def install_commands(engine: str) -> List[List[str]]:
    """The commands installing ``engine`` runs, in order. An engine with an
    environment of its own (:data:`ENGINE_VENVS`) gets it made first, and
    the worker's own packages in it; a torch engine gets torch from
    :func:`torch_index` before the rest, which then finds it in place."""
    python = engine_python(engine)
    pip = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    cmds: List[List[str]] = []
    venv = ENGINE_VENVS.get(engine)
    if venv is not None:
        if not _pinned_python(venv) and not Path(python).is_file():
            cmds.append([SPEECH_PYTHON, "-m", "venv", str(venv_dir(venv))])
        index = torch_index() if venv == "torch" else ""
        if index:
            torch_reqs = [r for r in ENGINE_PACKAGES[engine] if r.startswith(("torch==", "torch>=", "torchaudio"))]
            cmds.append([*pip, "--index-url", index, *torch_reqs])
        cmds.append([*pip, *WORKER_PACKAGES, *ENGINE_PACKAGES[engine]])
    else:
        cmds.append([*pip, *ENGINE_PACKAGES[engine]])
    if ENGINE_NO_DEPS.get(engine):
        cmds.append([*pip, "--no-deps", *ENGINE_NO_DEPS[engine]])
    return cmds


#: One pip at a time: an engine install and a voice cleanup that installs
#: its engine may both write to the same environment.
_pip_lock = threading.Lock()


def install_steps(job_id: str, engine: str) -> Optional[str]:
    """``pip install`` an engine's packages into the Python it runs under
    (:func:`engine_python`), the last line of pip's output as the job's
    message. Returns what went wrong, None once the engine imports."""
    with _pip_lock:
        for cmd in install_commands(engine):
            jobs.update(job_id, status="running", message=" ".join(cmd[2:])[:200])
            tail: List[str] = []
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        stdin=subprocess.DEVNULL)
                for line in proc.stdout or []:
                    line = line.strip()
                    if line:
                        tail = (tail + [line])[-8:]
                        jobs.update(job_id, message=line[:200])
                code = proc.wait()
            except Exception as exc:  # noqa: BLE001 - the job records why
                return f"{type(exc).__name__}: {exc}"[:400]
            if code != 0:
                tool = "venv" if "venv" in cmd[1:3] else "pip"
                return (f"{tool} exited with code {code}: " + " | ".join(tail[-3:]))[:500]
    if not engines().get(engine):
        return f"pip finished, but {ENGINE_MODULES[engine]} still does not import"
    return None


def run_install(job_id: str, engine: str) -> None:
    error = install_steps(job_id, engine)
    if error:
        jobs.update(job_id, status="error", error=error, finished_at=_now())
        return
    jobs.update(job_id, status="done", percent=100.0, message=f"{engine} installed", finished_at=_now())


class OllamaImportBody(BaseModel):
    name: str


@app.get("/ollama/models", dependencies=auth)
async def get_ollama_models() -> Dict[str, Any]:
    models = await asyncio.to_thread(ollama_models)
    return {"dir": str(OLLAMA_DIR), "found": (OLLAMA_DIR / "manifests").is_dir(), "models": models}


@app.post("/ollama/import", dependencies=auth)
async def import_ollama_model(body: OllamaImportBody) -> Dict[str, Any]:
    """An Ollama model as a runtime file: a hard link to Ollama's blob, so
    nothing is copied and nothing more is taken on disk (the file stays here
    even when Ollama deletes its copy), or a copy as a job when the two
    directories are on different disks."""
    found = next(((e, b) for e, b in await asyncio.to_thread(_ollama_entries) if e["name"] == body.name.strip()),
                 None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Ollama has no model {body.name!r} with GGUF weights "
                                                    f"in {OLLAMA_DIR}")
    entry, src = found
    if not entry.get("compatible", True):
        raise HTTPException(status_code=422, detail=f"{entry['name']}: {entry['note']}")
    dest = MODELS_DIR / entry["file"]
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"{entry['file']} is already here")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
        return {"ok": True, "file": entry["file"], "linked": True, "job_id": None}
    except OSError as exc:
        log.info("hard link of %s failed (%s); copying", src, exc)
    job = jobs.create("ollama_import", f"{entry['name']} from Ollama",
                      meta={"name": entry["name"], "dest": entry["file"]})
    threading.Thread(target=run_ollama_copy, args=(job["id"], src, dest), name=f"import-{job['id']}",
                     daemon=True).start()
    return {"ok": True, "file": entry["file"], "linked": False, "job_id": job["id"]}


@app.get("/lmstudio/models", dependencies=auth)
async def get_lmstudio_models() -> Dict[str, Any]:
    root = lmstudio_dir()
    models = [e for e, _ in await asyncio.to_thread(_lmstudio_entries)]
    return {"dir": str(root), "found": root.is_dir(), "models": models}


@app.post("/lmstudio/import", dependencies=auth)
async def import_lmstudio_model(body: OllamaImportBody) -> Dict[str, Any]:
    """An LM Studio model as a runtime model: hard links (a GGUF file, or
    every file of an MLX folder), nothing copied; a copy as a job across
    disks."""
    found = next(((e, p) for e, p in await asyncio.to_thread(_lmstudio_entries) if e["name"] == body.name.strip()),
                 None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"LM Studio has no model {body.name!r} in {lmstudio_dir()}")
    entry, src = found
    if not entry["compatible"]:
        raise HTTPException(status_code=422, detail=f"{entry['name']}: {entry['note']}")
    dest = MODELS_DIR / entry["file"]
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"{entry['file']} is already here")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if src.is_dir():
            staging = MODELS_DIR / f".{dest.name}.import"
            shutil.rmtree(staging, ignore_errors=True)
            try:
                _link_tree(src, staging)
                (staging / MODEL_MARKER).write_text(json.dumps(
                    {"source": f"LM Studio: {entry['name']}", "imported_at": _now()}), encoding="utf-8")
                staging.replace(dest)
            except OSError:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        else:
            os.link(src, dest)
        return {"ok": True, "file": entry["file"], "linked": True, "job_id": None}
    except OSError as exc:
        log.info("hard link of %s failed (%s); copying", src, exc)
    job = jobs.create("lmstudio_import", f"{entry['name']} from LM Studio",
                      meta={"name": entry["name"], "dest": entry["file"]})
    target = run_tree_copy if src.is_dir() else run_ollama_copy
    threading.Thread(target=target, args=(job["id"], src, dest), name=f"import-{job['id']}",
                     daemon=True).start()
    return {"ok": True, "file": entry["file"], "linked": False, "job_id": job["id"]}


@app.get("/engines", dependencies=auth)
async def get_engines() -> Dict[str, Any]:
    found = await asyncio.to_thread(engines)
    llama = {"id": "llama", "kind": "chat", "installed": found.get("llama", False),
             "packages": [f"llama.cpp release build ({llama_asset_suffix() or 'none for this platform'})"],
             "version": _llama_record().get("tag"), "bin": llama_bin()}
    return {"python": SPEECH_PYTHON, "engines": [llama] + [
        {"id": e, "kind": ENGINE_KIND[e], "installed": found.get(e, False),
         "packages": ENGINE_PACKAGES[e] + ENGINE_NO_DEPS.get(e, []), "python": engine_python(e)}
        for e in ENGINE_MODULES if e in found]}


@app.post("/engines/{engine}/install", dependencies=auth)
async def install_engine(engine: str) -> Dict[str, Any]:
    if engine in APPLE_ENGINES and not mlx_platform():
        raise HTTPException(status_code=409, detail="MLX runs on Apple silicon only")
    if engine not in ENGINE_PACKAGES and engine != "llama":
        raise HTTPException(status_code=404, detail=f"no engine {engine!r}; one of llama, {', '.join(ENGINE_PACKAGES)}")
    for j in jobs.list():
        if j["kind"] == "engine_install" and j["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"an install is already running (job {j['id']})")
    job = jobs.create("engine_install", f"{engine} engine", meta={"engine": engine})
    if engine == "llama":
        target, args = run_llama_install, (job["id"],)
    else:
        target, args = run_install, (job["id"], engine)
    threading.Thread(target=target, args=args, name=f"install-{job['id']}", daemon=True).start()
    return {"job_id": job["id"], "engine": engine}


@app.post("/load", dependencies=auth)
async def load(body: LoadBody) -> Dict[str, Any]:
    return await load_model(body.file, body.context_length, body.gpu_layers, body.threads)


@app.post("/unload", dependencies=auth)
async def unload(body: UnloadBody) -> Dict[str, Any]:
    return await unload_model(body.file)


@app.get("/memory", dependencies=auth)
async def get_memory() -> Dict[str, Any]:
    return await asyncio.to_thread(memory)


# ── Recorded voices ──────────────────────────────────────────────────────────
# Samples the cloning engines (Chatterbox, OpenVoice) read and speak in; see
# "Voices" in speech_worker.py. Kept here, not per model, so one recording
# serves both engines. Who recorded a voice and whether others may pick it
# is the hub's to decide: it sends ``owner`` and ``shared`` and filters.

def voices_dir() -> Path:
    """Where the recordings live: hidden beside the models, so the model
    list skips it."""
    return MODELS_DIR / ".voices"


_VOICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
#: Chatterbox's own voice, never a recording's name.
_RESERVED_VOICES = {"default"}
VOICE_GENDERS = ("", "female", "male")
_VOICE_FIELDS = ("language", "gender", "shared", "base_model", "base_voice")
#: What a recording keeps beside ``sample.wav``: the sample before any
#: cleanup, so cleaning again starts from it and "none" brings it back.
ORIGINAL_SAMPLE = "original.wav"


def _voice_dir(name: str) -> Path:
    if not _VOICE_NAME.match(name or "") or name.lower() in _RESERVED_VOICES:
        raise HTTPException(status_code=400, detail="a voice name is 1 to 40 letters, digits, '.', '_' or '-', "
                                                    "starting with a letter or digit, and not 'default'")
    return voices_dir() / name


def voice_record(name: str) -> Optional[Dict[str, Any]]:
    """``voice.json`` of a recorded voice, with its name and the cleanup
    running on it (``cleaning``: the job's id and mode); None when there is
    no such voice."""
    d = voices_dir() / name
    if not (d / "sample.wav").is_file():
        return None
    try:
        data = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    record = {**(data if isinstance(data, dict) else {}), "name": name}
    running = cleanup_running(name)
    if running is not None:
        record["cleaning"] = {"job_id": running["id"], "mode": (running.get("meta") or {}).get("mode")}
    return record


def list_voices() -> List[Dict[str, Any]]:
    return [r for n in _worker().recorded_voices(voices_dir()) if (r := voice_record(n)) is not None]


def _write_voice(name: str, record: Dict[str, Any]) -> None:
    d = voices_dir() / name
    tmp = d / "voice.json.tmp"
    # The name and a running cleanup are read from elsewhere, not kept.
    tmp.write_text(json.dumps({k: v for k, v in record.items() if k not in ("name", "cleaning")},
                              ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(d / "voice.json")


def save_voice(name: str, audio: bytes, fields: Dict[str, Any], *, replace: bool = False) -> Dict[str, Any]:
    """A new recording of ``name`` (or a new sample for it, with
    ``replace``): the sample cleaned up by speech_worker.prepare_sample,
    the engines' caches of an older one dropped."""
    d = _voice_dir(name)
    old = voice_record(name)
    if old is not None and not replace:
        raise HTTPException(status_code=409, detail=f"a voice named {name!r} exists already")
    try:
        wav, seconds = _worker().prepare_sample(audio)
    except _worker().WorkerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"reading audio needs {exc.name} in the runtime's Python")
    if old is not None and cleanup_running(name) is not None:
        raise HTTPException(status_code=409, detail=f"{name!r} is being cleaned; wait for it to finish")
    d.mkdir(parents=True, exist_ok=True)
    for target in (ORIGINAL_SAMPLE, "sample.wav"):
        tmp = d / f"{target}.tmp"
        tmp.write_bytes(wav)
        tmp.replace(d / target)
    shutil.rmtree(d / "cache", ignore_errors=True)
    record = {**(old or {}), **{k: v for k, v in fields.items() if v is not None},
              "duration": seconds, "cleanup": "", "updated_at": _now()}
    record.setdefault("created_at", record["updated_at"])
    _write_voice(name, record)
    return {**record, "name": name}


def update_voice(name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    record = voice_record(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    record.update({k: v for k, v in fields.items() if k in _VOICE_FIELDS and v is not None})
    record["updated_at"] = _now()
    _write_voice(name, record)
    return record


# ── Voice cleanup ──
# A sample's room (echo, hum) taken out before the cloning engines learn
# from it, by voice_enhance.py in its engine's environment: ``denoise``
# (DeepFilterNet on Apple silicon, Resemble Enhance's denoiser elsewhere)
# or ``restore`` (Resemble Enhance, noise and echo). A job: the engine is
# installed and its weights fetched the first time. The original stays in
# ``original.wav``; every cleanup starts from it and ``none`` puts it back.

CLEANUP_MODES = ("none", "denoise", "restore")
#: Engine -> its weights on Hugging Face: the repo and, per file there, where
#: it goes under the engine's weights directory (:func:`cleanup_weights_dir`).
CLEANUP_WEIGHTS: Dict[str, Tuple[str, Dict[str, str]]] = {
    "deepfilternet": ("mlx-community/DeepFilterNet-mlx", {
        "v3/config.json": "config.json", "v3/model.safetensors": "model.safetensors"}),
    "resemble_enhance": ("ResembleAI/resemble-enhance", {
        "enhancer_stage2/hparams.yaml": "hparams.yaml",
        "enhancer_stage2/ds/G/latest": "ds/G/latest",
        "enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt": "ds/G/default/mp_rank_00_model_states.pt"}),
}
#: How long one cleanup may run: Resemble Enhance takes about twice the
#: recording's length on a laptop CPU, and a sample is 30 s at most.
CLEANUP_TIMEOUT = 900.0


def cleanup_engine(mode: str) -> str:
    return "deepfilternet" if mode == "denoise" and mlx_platform() else "resemble_enhance"


def cleanup_weights_dir(engine: str) -> Path:
    """Hidden beside the models, so the model list skips it."""
    return MODELS_DIR / ".cleanup" / engine


def cleanup_running(name: str) -> Optional[Dict[str, Any]]:
    """The queued or running cleanup of voice ``name``, if any."""
    return next((j for j in jobs.list() if j["kind"] == "voice_cleanup" and j["status"] in ("queued", "running")
                 and (j.get("meta") or {}).get("voice") == name), None)


def _write_sample(d: Path, wav: bytes) -> None:
    tmp = d / "sample.wav.tmp"
    tmp.write_bytes(wav)
    tmp.replace(d / "sample.wav")
    shutil.rmtree(d / "cache", ignore_errors=True)


def _original(d: Path) -> Path:
    """The sample before cleanup; a voice recorded before cleanups existed
    has only ``sample.wav``, which is its original."""
    original = d / ORIGINAL_SAMPLE
    if not original.is_file():
        shutil.copyfile(d / "sample.wav", original)
    return original


def restore_original(name: str) -> Dict[str, Any]:
    """Cleanup ``none``: the sample as it was recorded."""
    d = voices_dir() / name
    record = voice_record(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    if record.get("cleanup"):
        _write_sample(d, _original(d).read_bytes())
    record.pop("cleaning", None)
    record.update(cleanup="", updated_at=_now())
    _write_voice(name, record)
    return record


def _fetch_cleanup_weights(job_id: str, engine: str) -> None:
    repo, files = CLEANUP_WEIGHTS[engine]
    root = cleanup_weights_dir(engine)
    for src, dest in files.items():
        target = root / dest
        if not target.is_file():
            _fetch(job_id, f"{HF_BASE}/{repo}/resolve/main/{src}", target)


def run_cleanup(job_id: str, name: str, mode: str) -> None:
    """Install the engine when missing, fetch its weights, clean the
    original and keep the result as the voice's sample, levelled and trimmed
    the way a recording is (speech_worker.prepare_sample)."""
    engine = cleanup_engine(mode)
    d = voices_dir() / name

    def fail(error: str) -> None:
        jobs.update(job_id, status="error", error=error[:500], finished_at=_now())

    jobs.update(job_id, status="running", message=f"preparing {engine}")
    if not engines().get(engine):
        error = install_steps(job_id, engine)
        if error:
            return fail(f"installing {engine}: {error}")
    try:
        _fetch_cleanup_weights(job_id, engine)
    except _FetchError as exc:
        return fail(f"downloading the {engine} weights: {exc}; ask again to resume")
    try:
        original = _original(d)
        stamp = original.stat().st_mtime
    except OSError:
        return fail(f"the voice {name!r} is gone")
    out = d / "cleaned.tmp.wav"
    cmd = [engine_python(engine), str(VOICE_ENHANCE), "--engine", engine, "--mode", mode,
           "--weights", str(cleanup_weights_dir(engine)), str(original), str(out)]
    jobs.update(job_id, message=f"cleaning with {engine}", percent=0.0, completed=0, total=0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLEANUP_TIMEOUT,
                              stdin=subprocess.DEVNULL, env={**os.environ, "TQDM_DISABLE": "1"})
    except subprocess.TimeoutExpired:
        out.unlink(missing_ok=True)
        return fail(f"{engine} took longer than {CLEANUP_TIMEOUT:.0f} s")
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        lines = [ln for ln in (proc.stderr or proc.stdout or "").strip().splitlines() if ln.strip()]
        return fail(f"{engine} failed: " + " | ".join(lines[-3:]))
    try:
        wav, seconds = _worker().prepare_sample(out.read_bytes())
    except _worker().WorkerError as exc:
        return fail(f"the cleaned recording: {exc}")
    finally:
        out.unlink(missing_ok=True)
    record = voice_record(name)
    try:
        replaced = original.stat().st_mtime != stamp
    except OSError:
        replaced = True
    if record is None or replaced:
        return fail(f"{name!r} was recorded again or removed while it was being cleaned")
    _write_sample(d, wav)
    record.pop("cleaning", None)
    record.update(cleanup=mode, cleanup_engine=engine, duration=seconds, updated_at=_now())
    _write_voice(name, record)
    took = ""
    try:
        took = f" in {json.loads(proc.stdout.strip().splitlines()[-1])['seconds']:.0f} s"
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    jobs.update(job_id, status="done", percent=100.0, message=f"{name} cleaned with {engine}{took}",
                finished_at=_now())


def start_cleanup(name: str, mode: str) -> Dict[str, Any]:
    """Cleanup ``mode`` of voice ``name``: a job (``job_id``), or for
    ``none`` the voice's record at once."""
    if mode not in CLEANUP_MODES:
        raise HTTPException(status_code=400, detail=f"cleanup is one of {', '.join(CLEANUP_MODES)}")
    _voice_dir(name)
    if voice_record(name) is None:
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    running = cleanup_running(name)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"{name!r} is being cleaned already (job {running['id']})")
    if mode == "none":
        return {"job_id": None, "voice": restore_original(name)}
    engine = cleanup_engine(mode)
    if engine in APPLE_ENGINES and not mlx_platform():
        raise HTTPException(status_code=409, detail="MLX runs on Apple silicon only")
    job = jobs.create("voice_cleanup", f"{name}: {mode}", meta={"voice": name, "mode": mode, "engine": engine})
    threading.Thread(target=run_cleanup, args=(job["id"], name, mode), name=f"cleanup-{job['id']}",
                     daemon=True).start()
    return {"job_id": job["id"], "engine": engine, "voice": voice_record(name)}


def _clean_fields(language: Optional[str], gender: Optional[str], shared: Optional[bool],
                  base_model: Optional[str], base_voice: Optional[str]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {"shared": shared}
    if language is not None:
        language = language.strip().lower()[:2]
        if language and not re.match(r"^[a-z]{2}$", language):
            raise HTTPException(status_code=400, detail="language is a two-letter code, such as ru or en")
        fields["language"] = language
    if gender is not None:
        if gender not in VOICE_GENDERS:
            raise HTTPException(status_code=400, detail="gender is female, male or empty")
        fields["gender"] = gender
    for key, value in (("base_model", base_model), ("base_voice", base_voice)):
        if value is not None:
            if value and not re.match(r"^[\w.:-]{1,120}$", value):
                raise HTTPException(status_code=400, detail=f"{key} is a model or voice name")
            fields[key] = value
    return fields


#: The engines that read for OpenVoice, best first, and the languages each
#: reads (None: the model says, see :func:`_base_languages`).
_BASE_ENGINES = ("kokoro", "supertonic", "piper", "kitten")
_KOKORO_PREFIX_LANG = {"a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi", "i": "it", "j": "ja",
                       "p": "pt", "z": "zh"}
#: Kokoro's voices that read best, per language prefix and gender.
_KOKORO_BEST = {"af_heart", "am_michael", "bf_emma", "bm_george", "ef_dora", "em_alex", "ff_siwis",
                "if_sara", "im_nicola", "pf_dora", "pm_alex", "jf_alpha", "jm_kumo", "zf_xiaobei",
                "zm_yunjian", "hf_alpha", "hm_omega"}


def _base_languages(entry: Dict[str, Any]) -> Optional[set]:
    """The languages a reading model reads; None for all of them
    (Supertonic 3 reads 31, and anything else with its "na" token)."""
    engine, name = entry["engine"], entry["name"].lower()
    if engine == "kokoro":
        return {_KOKORO_PREFIX_LANG[v[0]] for v in entry.get("voices") or [] if v[:1] in _KOKORO_PREFIX_LANG}
    if engine == "kitten":
        return {"en"}
    if engine == "supertonic":
        if name.endswith("3") or "supertonic-3" in name:
            return None
        return {"en", "ko", "es", "pt", "fr"} if "2" in name else {"en"}
    if engine == "piper":
        m = re.search(r"(?:^|[-_])([a-z]{2,3})_[a-z]{2}(?=[-_.]|$)", name)
        return {m.group(1)} if m else set()
    return set()


def _base_voice(entry: Dict[str, Any], lang: str, gender: str) -> str:
    """The voice of a reading model closest to the recording: its language
    and gender where the names tell (Kokoro's ``af_``, Supertonic's F1)."""
    voices = [str(v) for v in entry.get("voices") or []]
    if not voices:
        return ""
    g = gender[:1] if gender else "f"
    if entry["engine"] == "kokoro":
        prefix = {v: k for k, v in _KOKORO_PREFIX_LANG.items() if k not in ("b",)}.get(lang, "a")
        fits = [v for v in voices if v.startswith(f"{prefix}{g}_")] or [v for v in voices if v.startswith(prefix)]
        best = [v for v in fits if v in _KOKORO_BEST]
        return (best or fits or voices)[0]
    if entry["engine"] == "supertonic":
        fits = [v for v in voices if v[:1].lower() == g]
        return (fits or voices)[0]
    return voices[0]


def pick_base(text: str, record: Optional[Dict[str, Any]],
              models: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """``(model, voice)`` that reads ``text`` for OpenVoice: the recording's
    own choice when it made one and that model is here, else the best
    downloaded model that reads the text's language (a running one first),
    in the voice nearest the recording's gender. None when none reads it."""
    record = record or {}
    usable = [m for m in models if m.get("kind") == "speech" and m.get("loadable")
              and m.get("engine") in _BASE_ENGINES]
    chosen = str(record.get("base_model") or "")
    if chosen:
        entry = next((m for m in usable if m["name"] == chosen), None)
        if entry is not None:
            return entry["name"], str(record.get("base_voice") or "") or _base_voice(entry, "", "")
    lang = _worker().chatterbox_language(text, record.get("language"))
    fits = []
    for m in usable:
        langs = _base_languages(m)
        if langs is None or lang in langs:
            fits.append(m)
    if not fits:
        return None
    fits.sort(key=lambda m: (_BASE_ENGINES.index(m["engine"]), not m.get("loaded"), m["name"]))
    best = fits[0]
    return best["name"], _base_voice(best, lang, str(record.get("gender") or ""))


@app.get("/voices", dependencies=auth)
async def get_voices() -> Dict[str, Any]:
    return {"voices": await asyncio.to_thread(list_voices)}


@app.post("/voices", dependencies=auth)
async def post_voice(request: Request) -> Dict[str, Any]:
    """multipart: ``name``, ``file`` (any audio), ``consent`` (must be
    true: the person confirmed the voice is theirs or they may use it),
    ``owner``, ``shared``, ``language``, ``gender``, ``replace``, and
    ``cleanup`` (see :func:`start_cleanup`; its job's id comes back as
    ``job_id``)."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="file is missing")
    if str(form.get("consent") or "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=400, detail="consent is required: a voice may be recorded only by "
                                                    "its owner or with their permission")
    audio = await upload.read()
    if len(audio) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="the recording is larger than 50 MB")
    name = str(form.get("name") or "").strip()
    fields = _clean_fields(form.get("language"), form.get("gender"),
                           str(form.get("shared") or "").lower() in ("1", "true", "yes"), None, None)
    fields.update({"owner": str(form.get("owner") or "")[:200], "consent_at": _now()})
    replace = str(form.get("replace") or "").lower() in ("1", "true", "yes")
    cleanup = str(form.get("cleanup") or "none").strip().lower()
    if cleanup not in CLEANUP_MODES:
        raise HTTPException(status_code=400, detail=f"cleanup is one of {', '.join(CLEANUP_MODES)}")
    record = await asyncio.to_thread(save_voice, name, audio, fields, replace=replace)
    if cleanup != "none":
        started = await asyncio.to_thread(start_cleanup, name, cleanup)
        record = {**(started.get("voice") or record), "job_id": started["job_id"]}
    return record


class VoicePatch(BaseModel):
    language: Optional[str] = None
    gender: Optional[str] = None
    shared: Optional[bool] = None
    base_model: Optional[str] = None
    base_voice: Optional[str] = None


@app.patch("/voices/{name}", dependencies=auth)
async def patch_voice(name: str, body: VoicePatch) -> Dict[str, Any]:
    _voice_dir(name)
    fields = _clean_fields(body.language, body.gender, body.shared, body.base_model, body.base_voice)
    return await asyncio.to_thread(update_voice, name, fields)


class VoiceCleanupBody(BaseModel):
    mode: str


@app.post("/voices/{name}/cleanup", dependencies=auth)
async def cleanup_voice(name: str, body: VoiceCleanupBody) -> Dict[str, Any]:
    return await asyncio.to_thread(start_cleanup, name, body.mode.strip().lower())


@app.get("/voices/{name}/audio", dependencies=auth)
async def voice_audio(name: str, original: bool = False) -> Response:
    """The sample the engines use, or with ``original`` the one recorded."""
    d = _voice_dir(name)
    if not (d / "sample.wav").is_file():
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    path = d / ORIGINAL_SAMPLE if original and (d / ORIGINAL_SAMPLE).is_file() else d / "sample.wav"
    return Response(content=path.read_bytes(), media_type="audio/wav")


@app.delete("/voices/{name}", dependencies=auth)
async def delete_voice(name: str) -> Dict[str, Any]:
    d = _voice_dir(name)
    if not d.is_dir():
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    if cleanup_running(name) is not None:
        raise HTTPException(status_code=409, detail=f"{name!r} is being cleaned; wait for it to finish")
    await asyncio.to_thread(shutil.rmtree, d)
    return {"ok": True, "name": name}


# ── OpenAI-compatible gateway ────────────────────────────────────────────────

@app.get("/v1/models", dependencies=auth)
async def v1_models() -> Dict[str, Any]:
    """Loaded chat models, and every speech model that can run here, loaded
    or not (the gateway loads one on its first request). ``kind`` tells them
    apart; the hub keeps speech models out of its chat model picker."""
    _drop_dead()
    data: List[Dict[str, Any]] = [
        {"id": m.name, "object": "model", "owned_by": "hub-local", "created": 0, "kind": "chat",
         "context_length": m.context_length} for m in state.loaded.values() if m.engine == "llama"]
    for e in await asyncio.to_thread(list_models):
        if e.get("kind") in SPEECH_KINDS and e.get("loadable"):
            data.append({"id": e["name"], "object": "model", "owned_by": "hub-local", "created": 0,
                         "kind": e["kind"], "engine": e["engine"], "loaded": e["loaded"],
                         "voices": e.get("voices") or []})
    return {"object": "list", "data": data}


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": "invalid_request_error", "code": code}})


def _not_loaded(model: str) -> JSONResponse:
    return _error(404, f"model {model!r} is not loaded in the hub runtime; load it on the Models page "
                       f"(loaded: {', '.join(sorted(state.loaded)) or 'none'})", "model_not_loaded")


async def _forward(m: Loaded, path: str, raw: bytes, content_type: str) -> Response:
    """``raw`` as it came, to the model's own server, the answer streamed back."""
    m.touch()
    url = f"http://127.0.0.1:{m.port}{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream = await client.send(client.build_request("POST", url, content=raw,
                                                          headers={"content-type": content_type}),
                                     stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(status_code=502, content={"error": {"message": f"{m.engine}: {exc}"}})

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type"),
                             background=BackgroundTask(close))


async def _proxy(request: Request, path: str) -> Response:
    started = time.monotonic()
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return metered(request, path, "", JSONResponse(
            status_code=400, content={"error": {"message": "body is not JSON"}}), started)
    model = str(body.get("model") or "") if isinstance(body, dict) else ""
    streamed = isinstance(body, dict) and bool(body.get("stream"))
    return metered(request, path, model, await _route_chat(path, raw, body, model), started,
                   streamed=streamed)


async def _route_chat(path: str, raw: bytes, body: Any, model: str) -> Response:
    m = _find_loaded(model) if model else None
    if m is None or m.proc.poll() is not None:
        return _not_loaded(model)
    if m.kind != "chat":
        return _error(400, f"{model!r} is a {m.kind} model; use /v1/audio/"
                           f"{'speech' if m.kind == 'speech' else 'transcriptions'}", "wrong_model_kind")
    if path == "/v1/chat/completions":
        c = cache_settings()
        if c["enabled"] and c["warmup"]:
            heads.note(m.name, body, c["warmup_prompts"])
    if m.engine == "mlx":
        return await _forward_mlx(m, path, body)
    return await _forward(m, path, raw, "application/json")


async def _forward_mlx(m: Loaded, path: str, body: Dict[str, Any]) -> Response:
    """A chat call to an MLX model. mlx-lm takes any other ``model`` value as
    a model to fetch from Hugging Face, so it always gets ``default_model``
    (the one it was started with); its answer is brought to llama-server's
    shape on the way back (:func:`mlx_message`)."""
    m.touch()
    payload = {**body, "model": "default_model"}
    # mlx-lm counts a streamed answer's tokens (cached ones included) only
    # when asked; a caller that did not ask gets that chunk as an SSE comment,
    # which clients skip and the gateway's count still reads.
    hide_usage = False
    if body.get("stream") and not (isinstance(body.get("stream_options"), dict)
                                   and body["stream_options"].get("include_usage")):
        payload["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        hide_usage = True
    url = f"http://127.0.0.1:{m.port}{path}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream = await client.send(client.build_request("POST", url, json=payload), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(status_code=502, content={"error": {"message": f"mlx: {exc}"}})
    name = m.name

    def fix(data: Dict[str, Any], harmony: Optional[HarmonyStream], whole: bool) -> Dict[str, Any]:
        if isinstance(data, dict):
            data["model"] = name
            for choice in data.get("choices") or []:
                if isinstance(choice, dict):
                    mlx_message(choice.get("message") or choice.get("delta"), harmony, whole=whole)
        return data

    if not body.get("stream") or upstream.status_code >= 400:
        try:
            raw = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        try:
            data = fix(json.loads(raw), None if not m.harmony else HarmonyStream(), True)
        except ValueError:
            return Response(content=raw, status_code=upstream.status_code,
                            media_type=upstream.headers.get("content-type"))
        return JSONResponse(status_code=upstream.status_code, content=data)

    harmony = HarmonyStream() if m.harmony else None

    async def events() -> Any:
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        data = fix(json.loads(line[6:]), harmony, False)
                        line = "data: " + json.dumps(data, ensure_ascii=False)
                        if hide_usage and isinstance(data, dict) and data.get("usage") and not data.get("choices"):
                            line = ": " + json.dumps({"usage": data["usage"]})
                    except ValueError:
                        pass
                yield (line + "\n").encode()
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(events(), status_code=upstream.status_code, media_type="text/event-stream")


@app.post("/v1/chat/completions", dependencies=auth)
async def v1_chat(request: Request) -> Response:
    return await _proxy(request, "/v1/chat/completions")


@app.post("/v1/completions", dependencies=auth)
async def v1_completions(request: Request) -> Response:
    return await _proxy(request, "/v1/completions")


@app.post("/v1/embeddings", dependencies=auth)
async def v1_embeddings(request: Request) -> Response:
    return await _proxy(request, "/v1/embeddings")


@app.get("/usage", dependencies=auth)
async def get_usage() -> Dict[str, Any]:
    """The gateway's calls since ``since``: ``totals``, ``rows`` per model,
    caller (``source``) and ``kind``, and the latest calls in ``recent``."""
    return usage.snapshot()


@app.delete("/usage", dependencies=auth)
async def clear_usage() -> Dict[str, Any]:
    usage.clear()
    return usage.snapshot()


class CacheModelBody(BaseModel):
    model: str = Field(min_length=1, max_length=300)


@app.get("/cache", dependencies=auth)
async def get_cache() -> Dict[str, Any]:
    """The prompt cache: ``settings`` (over ``defaults``), each loaded chat
    model's slots, KV size, restore and warm-up (``models``), what is kept
    for every model on disk (``stored``), the memory it comes out of, and
    which models run with older settings (``pending``)."""
    return await asyncio.to_thread(cache_overview)


@app.put("/cache/settings", dependencies=auth)
async def put_cache_settings(body: Dict[str, Any]) -> Dict[str, Any]:
    """Change some settings; they reach a running model when it is loaded
    again (``POST /cache/apply``). A smaller disk limit applies at once."""
    try:
        settings = check_cache_settings(body, cache_settings())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    save_cache_settings(settings)
    await asyncio.to_thread(trim_slot_disk, settings["disk_mib"])
    return await asyncio.to_thread(cache_overview)


@app.post("/cache/apply", dependencies=auth)
async def apply_cache() -> Dict[str, Any]:
    """Load again every chat model whose cache flags differ from the
    settings, with the file, context length, GPU layers and threads it had;
    its slots are saved first and come back if they still fit it."""
    reloaded: List[str] = []
    failed: List[Dict[str, str]] = []
    for m in list(state.loaded.values()):
        if m.kind != "chat" or tuple(m.cache_flags) == cache_flags(m.engine, m.name):
            continue
        async with _lock():
            if state.loaded.get(m.name) is not m:
                continue
            state.loaded.pop(m.name, None)
            await save_slots(m)
            await asyncio.to_thread(_stop, m)
        try:
            await load_model(m.file, m.context_length or 4096, m.gpu_layers, m.threads)
            reloaded.append(m.name)
        except HTTPException as exc:
            failed.append({"name": m.name, "error": str(exc.detail)})
    return {**await asyncio.to_thread(cache_overview), "reloaded": reloaded, "failed": failed}


@app.post("/cache/save", dependencies=auth)
async def save_cache() -> Dict[str, Any]:
    """Save every loaded model's slots now: what the hub calls before it
    stops this runtime, whose servers die with it."""
    saved = {}
    async with _lock():
        for m in list(state.loaded.values()):
            saved[m.name] = await save_slots(m)
    return {"saved": saved}


@app.post("/cache/warmup", dependencies=auth)
async def warmup_cache(body: CacheModelBody) -> Dict[str, Any]:
    """Send a loaded model its kept prompt heads again, in the background."""
    m = _find_loaded(body.model)
    if m is None or m.kind != "chat":
        raise HTTPException(status_code=404, detail=f"no loaded chat model {body.model!r}")
    return {"started": start_warm_up(m), "heads": len(heads.list(m.name))}


@app.delete("/cache", dependencies=auth)
async def clear_cache(model: Optional[str] = None) -> Dict[str, Any]:
    """Forget what is kept on disk, the saved slots and the prompt heads,
    for ``model`` or for every model. What the running servers hold in
    memory stays until they are loaded again."""
    root = _cache_dir() / "slots"
    names = [model_name(_safe_file(model))] if model else (
        [d.name for d in root.iterdir() if d.is_dir()] if root.is_dir() else [])
    for name in names:
        _drop_slot_files(_slot_dir(name))
    heads.clear(model_name(model) if model else None)
    return await asyncio.to_thread(cache_overview)


async def _speech_model(model: str, kind: str, keep: Tuple[str, ...] = ()) -> Any:
    """The running server of speech model ``model``, loaded now when it is
    on disk but not running (evicting none of ``keep``); or the error answer
    to send instead."""
    _drop_dead()
    m = _find_loaded(model) if model else None
    if m is None and model:
        entry = next((e for e in await asyncio.to_thread(list_models)
                      if e["name"] == model and e.get("kind") in SPEECH_KINDS), None)
        if entry is not None:
            if not entry["loadable"]:
                return _error(409, entry.get("note") or f"{model} cannot run here", "engine_missing")
            try:
                await load_model(entry["file"], 4096, -1, None, keep=keep)
            except HTTPException as exc:
                return _error(exc.status_code if exc.status_code < 500 else 502, str(exc.detail),
                              "load_failed")
            m = _find_loaded(model)
    if m is None:
        here = sorted(e["name"] for e in await asyncio.to_thread(list_models)
                      if e.get("kind") == kind and e.get("loadable"))
        return _error(404, f"no {kind} model {model!r} in the hub runtime; download one on the Models page "
                           f"(here: {', '.join(here) or 'none'})", "model_not_found")
    if m.kind != kind:
        return _error(400, f"{model!r} is a {m.kind} model, not a {kind} model", "wrong_model_kind")
    return m


@app.post("/v1/audio/speech", dependencies=auth)
async def v1_speech(request: Request) -> Response:
    started = time.monotonic()
    path = "/v1/audio/speech"
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return metered(request, path, "", _error(400, "body is not JSON", "bad_request"), started)
    model = str(body.get("model") or "") if isinstance(body, dict) else ""
    target = await _speech_model(model, "speech")
    if isinstance(target, Response):
        return metered(request, path, model, target, started)
    if target.engine == "openvoice":
        return metered(request, path, model, await _converted_speech(target, body), started)
    return metered(request, path, model, await _forward(target, path, raw, "application/json"), started)


async def _converted_speech(converter: Loaded, body: Dict[str, Any]) -> Response:
    """Speech in a recorded voice the fast way: a reading model
    (:func:`pick_base`) reads the text, OpenVoice gives it the voice."""
    text = str(body.get("input") or "").strip()
    if not text:
        return _error(400, "input is empty", "bad_request")
    voice = str(body.get("voice") or "")
    record = await asyncio.to_thread(voice_record, voice) if _VOICE_NAME.match(voice) else None
    base = pick_base(text, record, await asyncio.to_thread(list_models))
    if base is None:
        lang = _worker().chatterbox_language(text, (record or {}).get("language"))
        return _error(409, f"OpenVoice needs a speech model that reads {lang!r} to read the text first; "
                           "download one on the Models page (Supertonic 3 reads 31 languages, Piper has a "
                           "voice per language, Kokoro reads English and seven more)", "no_reading_model")
    reader = await _speech_model(base[0], "speech", keep=(converter.name,))
    if isinstance(reader, Response):
        return reader
    converter.touch()
    reader.touch()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        try:
            read = await client.post(f"http://127.0.0.1:{reader.port}/v1/audio/speech", json={
                "input": text, "voice": base[1], "speed": body.get("speed") or 1.0, "response_format": "wav"})
            if read.status_code >= 400:
                return Response(content=read.content, status_code=read.status_code,
                                media_type=read.headers.get("content-type"))
            out = await client.post(f"http://127.0.0.1:{converter.port}/v1/audio/convert",
                                    files={"file": ("speech.wav", read.content, "audio/wav")},
                                    data={"voice": voice, "source": f"{base[0]}/{base[1]}",
                                          "tau": str(body.get("tau") or 0.3),
                                          "response_format": str(body.get("response_format") or "mp3")})
        except httpx.HTTPError as exc:
            return JSONResponse(status_code=502, content={"error": {"message": f"openvoice: {exc}"}})
    return Response(content=out.content, status_code=out.status_code, media_type=out.headers.get("content-type"))


@app.post("/v1/audio/transcriptions", dependencies=auth)
async def v1_transcriptions(request: Request) -> Response:
    started = time.monotonic()
    path = "/v1/audio/transcriptions"
    raw = await request.body()
    try:
        form = await request.form()
        model = str(form.get("model") or "")
        await form.close()
    except Exception:  # noqa: BLE001 - anything that is not a readable form
        return metered(request, path, "", _error(400, "expected multipart/form-data with a file and a model",
                                                 "bad_request"), started)
    target = await _speech_model(model, "transcription")
    if isinstance(target, Response):
        return metered(request, path, model, target, started)
    return metered(request, path, model,
                   await _forward(target, path, raw, request.headers.get("content-type") or "multipart/form-data"),
                   started)


if __name__ == "__main__":
    import uvicorn
    # No line per request: the hub checks /healthz every half minute.
    uvicorn.run(app, host=os.environ.get("MODELS_HOST") or ("0.0.0.0" if in_docker() else "127.0.0.1"),
                port=PORT, access_log=False)

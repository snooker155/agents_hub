"""
The ready local set: going local in one action (docs/local-models.md, "Ready
local set").

In order, as one background job on the hub's job list (``kind`` ``local_set``):
start the hub's model runtime, install the llama.cpp engine, download one chat
model sized to this machine's memory and load it (which registers it in the
catalog), install Whisper and download a transcription model, install Kokoro
and download a speech model, and give the workspace those two as its speech
models. The chat model becomes the global default only when nothing else is
configured: a hub with a cloud key keeps its choice.

**Safe to press again.** Every step first looks at what is already there (the
engine installed, the file in the runtime's listing, the special model set) and
is marked ``skipped`` when it is; a runtime download resumes from its ``.part``
file. A second press while a job runs returns that job.

**Cancel** stops after the current wait: a runtime download or install that
already started goes on in the runtime (it has no cancel of its own) and is
simply found done the next time.

The ladder below is the one place that names the candidate chat models.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from providers import local_models as lm

log = logging.getLogger(__name__)

JOB_KIND = "local_set"

#: Candidate chat models, smallest first: (id, label, repo, file, approximate
#: size in bytes, context length to load with). Official Qwen3 GGUF builds,
#: Q8 for the two tiny ones and Q4_K_M above. The pick is the largest one that
#: fits this machine's memory with room for the context.
CHAT_LADDER = [
    ("qwen3-0.6b", "Qwen3 0.6B", "Qwen/Qwen3-0.6B-GGUF", "Qwen3-0.6B-Q8_0.gguf", 640_000_000, 4096),
    ("qwen3-1.7b", "Qwen3 1.7B", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf", 1_830_000_000, 4096),
    ("qwen3-4b", "Qwen3 4B", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf", 2_500_000_000, 8192),
    ("qwen3-8b", "Qwen3 8B", "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", 5_030_000_000, 8192),
    ("qwen3-14b", "Qwen3 14B", "Qwen/Qwen3-14B-GGUF", "Qwen3-14B-Q4_K_M.gguf", 9_000_000_000, 8192),
]
#: The speech engines and models of the set, taken from the setup presets.
SPEECH_ID = "kokoro"
TRANSCRIPTION_ID = "whisper-small"

#: Seconds between looks at a runtime job, and the longest a step may wait.
POLL_SECONDS = 2.0
STEP_TIMEOUT_SECONDS = 4 * 3600
#: How long the runtime may take to come up (the first start makes its Python
#: environment).
START_TIMEOUT_SECONDS = 20 * 60

#: Step ids, in order, with their labels.
STEPS = [
    ("runtime", "Start the model runtime"),
    ("engine", "Install the llama.cpp engine"),
    ("chat", "Download the chat model"),
    ("load", "Load the chat model"),
    ("hearing", "Install Whisper and download its model"),
    ("voice", "Install Kokoro and download its model"),
    ("assign", "Set the workspace speech models"),
]

_cancel: Dict[str, threading.Event] = {}


class Cancelled(Exception):
    """The person pressed cancel."""


# ── the plan ─────────────────────────────────────────────────────────────────

def budget_bytes(hardware: Dict[str, Any]) -> int:
    """The memory a model may use here: the GPU's, else 80 % of RAM (the
    runtime's own fit rule, deploy/models/app.py ``estimate_fit``)."""
    gpu = int(hardware.get("gpu_bytes") or 0)
    return gpu or int(int(hardware.get("ram_bytes") or 0) * 0.8)


def pick_chat(hardware: Optional[Dict[str, Any]]) -> tuple:
    """The largest ladder entry that fits ``hardware`` with a margin for the
    context and llama.cpp's buffers (the file and 25 %, within 90 % of the
    budget, as the runtime's ``fits``); the smallest when memory is unknown."""
    budget = budget_bytes(hardware or {})
    chosen = CHAT_LADDER[0]
    for row in CHAT_LADDER:
        if budget and int(row[4] * 1.25) + 500_000_000 <= budget * 0.9:
            chosen = row
    return chosen


def _preset(table: List[tuple], wanted: str) -> tuple:
    row = next((r for r in table if r[0] == wanted), None)
    if row is None:
        raise lm.LocalModelError(f"unknown preset {wanted}")
    return row


def presets() -> Dict[str, tuple]:
    from cli.onboard.presets import VOICE_LOCAL_SPEECH, VOICE_LOCAL_TRANSCRIPTION
    return {"speech": _preset(VOICE_LOCAL_SPEECH, SPEECH_ID),
            "transcription": _preset(VOICE_LOCAL_TRANSCRIPTION, TRANSCRIPTION_ID)}


def _have(client: "lm.RuntimeClient") -> Dict[str, Any]:
    """What the runtime has: ``None`` when it does not answer."""
    try:
        listing = client.listing()
    except lm.LocalModelError:
        return {}
    return {"engines": listing.get("engines") or {},
            "files": {str(m.get("file")) for m in listing.get("models") or [] if isinstance(m, dict)},
            "names": {str(m.get("name")) for m in listing.get("models") or [] if isinstance(m, dict)}}


def _hardware(client: "lm.RuntimeClient") -> Dict[str, Any]:
    try:
        return client.hardware()
    except lm.LocalModelError:
        return {}


def plan(workspace: str = "default") -> Dict[str, Any]:
    """What pressing the button would do, with nothing changed: the chosen
    chat model, and each step as ``todo`` or ``skipped`` (already there).
    ``ready`` is every step present, ``installed`` every step but ``load``.
    Works with the runtime down (everything is ``todo``)."""
    out: Dict[str, Any] = {"available": lm.runtime_configured(), "steps": [], "chat": None}
    if not out["available"]:
        return out
    client = lm.RuntimeClient(timeout=15)
    have = _have(client)
    hardware = _hardware(client)
    row = pick_chat(hardware)
    pre = presets()
    out["hardware"] = {k: hardware.get(k) for k in ("kind", "name", "ram_bytes", "gpu_bytes")}
    out["chat"] = {"id": row[0], "label": row[1], "repo": row[2], "file": row[3], "size_bytes": row[4]}
    out["default_would_change"] = not _other_provider_usable()
    engines = have.get("engines") or {}
    files = have.get("files") or set()
    names = have.get("names") or set()
    done = {
        "runtime": bool(have),
        "engine": bool(engines.get("llama")),
        "chat": row[3] in files,
        "load": _chat_loaded(client, row[3]),
        "hearing": bool(engines.get("whisper")) and pre["transcription"][4] in names,
        "voice": bool(engines.get(pre["speech"][5])) and pre["speech"][4] in names,
        "assign": _assigned(workspace, pre),
    }
    out["steps"] = [{"id": sid, "label": label, "status": "skipped" if done[sid] else "todo"}
                    for sid, label in STEPS]
    out["ready"] = all(done.values())
    # Everything is on disk; only loading the chat model (lost on a runtime
    # restart) may remain. The card folds on this, not on ``ready``.
    out["installed"] = all(v for k, v in done.items() if k != "load")
    return out


def _chat_loaded(client: "lm.RuntimeClient", file: str) -> bool:
    try:
        return any(m.get("loaded") and m.get("file") == file for m in client.models())
    except lm.LocalModelError:
        return False


def _assigned(workspace: str, pre: Dict[str, tuple]) -> bool:
    from providers import special
    stored = special.stored(workspace)
    return bool((stored.get("speech") or {}).get("model")) and bool((stored.get("transcription") or {}).get("model"))


def _other_provider_usable() -> bool:
    from common import setup_guide
    try:
        return any(p != lm.HUB_LOCAL_ID for p in setup_guide.usable_providers())
    except Exception:  # noqa: BLE001 - unreadable settings count as nothing configured
        log.debug("local set: usable providers unreadable", exc_info=True)
        return False


# ── the job ──────────────────────────────────────────────────────────────────

def running_job() -> Optional[Dict[str, Any]]:
    return next((j for j in lm.JOBS.list()
                 if j.get("kind") == JOB_KIND and j.get("status") in ("queued", "running")), None)


def latest_job() -> Optional[Dict[str, Any]]:
    return next((j for j in lm.JOBS.list() if j.get("kind") == JOB_KIND), None)


def start(workspace: str = "default", *, background: bool = True) -> Dict[str, Any]:
    """Begin the set as a job (or return the one already running). With
    ``background`` False the work runs inline, which the tests use."""
    existing = running_job()
    if existing is not None:
        return {**existing, "already_running": True}
    steps = [{"id": sid, "label": label, "status": "todo", "percent": 0, "detail": ""}
             for sid, label in STEPS]
    job = lm.JOBS.create(JOB_KIND, "Ready local set", meta={"steps": steps, "workspace": workspace})
    _cancel[job["id"]] = threading.Event()
    if background:
        threading.Thread(target=_run, args=(job["id"], workspace), name=f"local-set-{job['id']}",
                         daemon=True).start()
    else:
        _run(job["id"], workspace)
    return lm.JOBS.get(job["id"]) or job


def cancel(job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    job = lm.JOBS.get(job_id) if job_id else running_job()
    if job is None or job.get("kind") != JOB_KIND:
        return None
    event = _cancel.get(job["id"])
    if event is not None:
        event.set()
    lm.JOBS.update(job["id"], message="cancelling after the current step")
    return lm.JOBS.get(job["id"])


class _Progress:
    """Writes step state into the job's ``meta.steps`` and its overall percent."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.stop = _cancel.setdefault(job_id, threading.Event())

    def check(self) -> None:
        if self.stop.is_set():
            raise Cancelled()

    def set(self, step: str, status: str, *, percent: Optional[float] = None, detail: str = "") -> None:
        job = lm.JOBS.get(self.job_id) or {}
        meta = dict(job.get("meta") or {})
        steps = [dict(s) for s in meta.get("steps") or []]
        index = 0
        for i, s in enumerate(steps):
            if s["id"] == step:
                s["status"] = status
                if percent is not None:
                    s["percent"] = round(percent, 1)
                elif status in ("done", "skipped"):
                    s["percent"] = 100
                s["detail"] = detail
                index = i
        meta["steps"] = steps
        finished = sum(1 for s in steps if s["status"] in ("done", "skipped"))
        current = next((s.get("percent") or 0 for s in steps if s["status"] == "running"), 0)
        overall = round(100.0 * (finished + current / 100.0) / max(len(steps), 1), 1)
        label = steps[index]["label"] if steps else ""
        lm.JOBS.update(self.job_id, meta=meta, percent=overall, message=detail or label)


def _wait_runtime_job(client: "lm.RuntimeClient", job_id: str, progress: _Progress, step: str,
                      label: str) -> None:
    deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
    while True:
        progress.check()
        rec = client.job(job_id)
        status = rec.get("status")
        if status == "done":
            return
        if status == "error":
            raise lm.LocalModelError(f"{label}: {str(rec.get('error') or rec.get('message') or 'failed')[:300]}")
        progress.set(step, "running", percent=float(rec.get("percent") or 0),
                     detail=str(rec.get("message") or label))
        if time.monotonic() > deadline:
            raise lm.LocalModelError(f"{label}: timed out")
        time.sleep(POLL_SECONDS)


def _submit(call: Callable[[], Dict[str, Any]], progress: _Progress) -> Optional[str]:
    """Start a runtime job. An install already running (only one at a time) is
    waited out and asked again; a file already there is no job at all."""
    for _ in range(int(STEP_TIMEOUT_SECONDS / max(POLL_SECONDS, 0.01)) or 1):
        progress.check()
        try:
            return str(call().get("job_id") or "") or None
        except lm.LocalModelError as exc:
            text = str(exc)
            if exc.status_code == 409 and "already here" in text:
                return None
            if exc.status_code == 409 and "already running" in text:
                time.sleep(POLL_SECONDS)
                continue
            if exc.status_code == 409 and "already downloading" in text:
                # Someone else's download of the same file: follow that job.
                import re
                m = re.search(r"job (\w+)", text)
                return m.group(1) if m else None
            raise
    raise lm.LocalModelError("timed out waiting for another install to finish")


def _install_engine(client: "lm.RuntimeClient", engine: str, progress: _Progress, step: str) -> None:
    if (client.listing().get("engines") or {}).get(engine):
        return
    label = f"Installing the {engine} engine"
    jid = _submit(lambda: client.install_engine(engine), progress)
    if jid:
        _wait_runtime_job(client, jid, progress, step, label)


def _download(client: "lm.RuntimeClient", repo: str, progress: _Progress, step: str, label: str, *,
              file: str = "", package: str = "") -> None:
    jid = _submit(lambda: client.download(repo, file, package=package), progress)
    if jid:
        _wait_runtime_job(client, jid, progress, step, label)


def _resolve_file(client: "lm.RuntimeClient", row: tuple) -> str:
    """The repo's file for ``row``: its named file, else a Q4_K_M or Q8_0 one
    when the repo renamed it. Falls back to the named file if the listing
    cannot be read."""
    try:
        files = [f.get("file") for f in client.hf_files(row[2]).get("files") or []]
    except lm.LocalModelError:
        return row[3]
    if row[3] in files:
        return row[3]
    stem = row[3].split("-GGUF")[0].split("-Q")[0].lower()
    for quant in ("q4_k_m", "q8_0"):
        hit = next((f for f in files if f and quant in f.lower() and stem in f.lower()), None)
        if hit:
            return str(hit)
    return row[3]


def _make_default(name: str) -> bool:
    """Make the loaded chat model the global default, only when no other
    provider is usable. Returns whether it did."""
    if _other_provider_usable():
        return False
    from common import provider_env
    from providers import registry
    backend = registry.get_backend(lm.HUB_LOCAL_ID) or {}
    registry.upsert_backend({**backend, "default_model": name})
    provider_env.save({"DEFAULT_PROVIDER": lm.HUB_LOCAL_ID})
    return True


def _assign(workspace: str, pre: Dict[str, tuple]) -> List[str]:
    """Fill the workspace's speech and transcription models with the downloaded
    ones where it has none (an existing choice is left alone)."""
    from providers import special
    from workspace import create_workspace_folder, get_workspace_folder
    if not get_workspace_folder(workspace):
        create_workspace_folder(workspace)
    stored = special.stored(workspace)
    entries = dict(stored)
    set_now: List[str] = []
    if not (stored.get("speech") or {}).get("model"):
        entries["speech"] = {"provider": lm.HUB_LOCAL_ID, "model": pre["speech"][4], "options": {}}
        set_now.append("speech")
    if not (stored.get("transcription") or {}).get("model"):
        entries["transcription"] = {"provider": lm.HUB_LOCAL_ID, "model": pre["transcription"][4],
                                    "options": {}}
        set_now.append("transcription")
    if set_now:
        special.save(workspace, entries)
    return set_now


def _run(job_id: str, workspace: str) -> None:
    progress = _Progress(job_id)
    lm.JOBS.update(job_id, status="running")
    try:
        _steps(job_id, workspace, progress)
    except Cancelled:
        lm.JOBS.update(job_id, status="error", error="cancelled", resumable=True,
                       finished_at=lm._now(), message="cancelled; press the button again to continue")
    except Exception as exc:  # noqa: BLE001 - the job records why, the thread never dies loudly
        log.warning("ready local set failed: %s", exc)
        lm.JOBS.update(job_id, status="error", error=str(exc)[:500], resumable=True, finished_at=lm._now())
    else:
        lm.JOBS.update(job_id, status="done", percent=100.0, finished_at=lm._now(),
                       message="the local set is ready")
    finally:
        _cancel.pop(job_id, None)


def _steps(job_id: str, workspace: str, progress: _Progress) -> None:
    try:
        from routes.models import _load_catalog  # the seed of a first catalog, as the Local routes pass it
    except ImportError:
        from dashboard.backend.routes.models import _load_catalog
    pre = presets()
    cur = "runtime"
    try:
        # 1. the runtime
        progress.set("runtime", "running", detail="starting the runtime")
        client = lm.RuntimeClient(timeout=30)
        if not _have(client):
            from providers import model_runtime_host as host
            if host.active():
                host.ensure(explicit=True)
            deadline = time.monotonic() + START_TIMEOUT_SECONDS
            while not _have(client):
                progress.check()
                if time.monotonic() > deadline:
                    raise lm.LocalModelError("the model runtime did not start: the Models page, Local tab, "
                                             "shows why")
                time.sleep(POLL_SECONDS)
            progress.set("runtime", "done")
        else:
            progress.set("runtime", "skipped")
        lm.ensure_hub_local_backend()
        progress.check()

        # 2. the llama.cpp engine
        cur = "engine"
        if (_have(client).get("engines") or {}).get("llama"):
            progress.set("engine", "skipped")
        else:
            progress.set("engine", "running", detail="installing llama.cpp")
            _install_engine(client, "llama", progress, "engine")
            progress.set("engine", "done")

        # 3. the chat model, sized to this machine
        cur = "chat"
        row = pick_chat(_hardware(client))
        lm.JOBS.update(job_id, meta={**(lm.JOBS.get(job_id) or {}).get("meta", {}),
                                     "chat": {"id": row[0], "label": row[1], "repo": row[2]}})
        file = _resolve_file(client, row)
        if file in (_have(client).get("files") or set()):
            progress.set("chat", "skipped", detail=row[1])
        else:
            progress.set("chat", "running", detail=f"downloading {row[1]}")
            _download(client, row[2], progress, "chat", f"Downloading {row[1]}", file=file)
            progress.set("chat", "done", detail=row[1])

        # 4. load it: that registers it in the catalog
        cur = "load"
        name = lm.model_name(file)
        if _chat_loaded(client, file):
            progress.set("load", "skipped", detail=row[1])
        else:
            progress.set("load", "running", detail=f"loading {row[1]}")
            loaded = client.load(file, row[5], -1, None)
            for evicted in loaded.get("evicted") or []:
                lm.catalog_set_enabled(lm.model_name(evicted), False, seed=_load_catalog)
            lm.catalog_set_enabled(name, True, context_window=int(loaded.get("context_length") or row[5]),
                                   seed=_load_catalog)
            made_default = _make_default(name)
            progress.set("load", "done", detail=row[1] + (", now the default model" if made_default else ""))
        progress.check()

        # 5. hearing and 6. speaking
        for cur, kind, engine, repo, package, label in (
                ("hearing", "transcription", "whisper", pre["transcription"][3], pre["transcription"][4],
                 pre["transcription"][1]),
                ("voice", "speech", pre["speech"][5], pre["speech"][3], pre["speech"][4], pre["speech"][1])):
            have = _have(client)
            if (have.get("engines") or {}).get(engine) and package in (have.get("names") or set()):
                progress.set(cur, "skipped", detail=label)
                continue
            progress.set(cur, "running", detail=f"installing the {engine} engine")
            _install_engine(client, engine, progress, cur)
            progress.set(cur, "running", detail=f"downloading {label}")
            _download(client, repo, progress, cur, f"Downloading {label}", package=package)
            progress.set(cur, "done", detail=label)

        # 7. the workspace's speech models
        cur = "assign"
        set_now = _assign(workspace, pre)
        progress.set("assign", "done" if set_now else "skipped",
                     detail=", ".join(set_now) if set_now else "the workspace already has its own")
    except Exception as exc:
        # Mark the step that failed (a cancel leaves it as it was).
        if not isinstance(exc, Cancelled):
            progress.set(cur, "error", detail=str(exc)[:200])
        raise


__all__ = ["CHAT_LADDER", "JOB_KIND", "STEPS", "budget_bytes", "cancel", "latest_job", "pick_chat",
           "plan", "running_job", "start"]

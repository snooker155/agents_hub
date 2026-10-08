"""Launching and stopping model servers, load and unload, and the memory report."""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_cache
import app_engines
import app_settings
import app_speech_models
import app_voices
import httpx
from fastapi import HTTPException

log = logging.getLogger("models_service")


def build_command(path: Path, name: str, port: int, context_length: int, gpu_layers: int,
                  threads: Optional[int], cache: Tuple[str, ...] = ()) -> List[str]:
    """llama-server's command line; ``cache`` is :func:`cache_flags`."""
    # -ngl larger than the layer count means "all"; llama-server has no -1.
    ngl = 999 if gpu_layers is None or gpu_layers < 0 else gpu_layers
    cmd = [app_engines.llama_bin(), "-m", str(path), "--port", str(port), "--host", app_settings.LLAMA_HOST,
           "-c", str(context_length), "-ngl", str(ngl), "--alias", name]
    if threads:
        cmd += ["-t", str(threads)]
    return cmd + list(cache)


async def wait_healthy(port: int, proc: Any, timeout: float = app_settings.LOAD_TIMEOUT_SECONDS) -> None:
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


def _stop(m: app_speech_models.Loaded) -> None:
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
    for name, m in list(app_speech_models.state.loaded.items()):
        if m.proc.poll() is not None:
            log.warning("the server for %s exited (code %s)", name, m.proc.returncode)
            app_speech_models.state.loaded.pop(name, None)


def build_speech_command(path: Path, engine: str, name: str, port: int,
                         threads: Optional[int]) -> List[str]:
    cmd = [app_speech_models.engine_python(engine), str(app_settings.SPEECH_WORKER), "--engine", engine, "--model", str(path),
           "--port", str(port), "--host", app_settings.LLAMA_HOST, "--name", name]
    if threads:
        cmd += ["--threads", str(threads)]
    return cmd


async def load_model(file: str, context_length: int, gpu_layers: int,
                     threads: Optional[int], keep: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Start ``file``'s server, evicting the least recently used model of
    its pool when the pool is full; never one named in ``keep``."""
    path = app_speech_models._model_path(file)
    engine = app_speech_models.speech_engine_of(path)
    speech = engine is not None
    mlx_cfg = app_engines.mlx_config(path) if not speech else None
    if mlx_cfg is not None:
        engine = "mlx"
        if not app_speech_models.mlx_platform():
            raise HTTPException(status_code=409, detail=f"{path.name} is an MLX model, and MLX runs on "
                                                        "Apple silicon only")
    if engine is None and not path.name.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail=f"{path.name} is neither a GGUF file, an MLX model "
                                                    "nor a speech model this runtime can load")
    if engine is not None and not app_speech_models.engines().get(engine):
        raise HTTPException(status_code=409, detail=f"the {engine} engine is not installed in this "
                                                    "runtime; install it on the Models page first")
    engine = engine or "llama"
    kind = app_speech_models.ENGINE_KIND.get(engine, "chat")
    name = app_speech_models.model_name(path.name)
    evicted: List[str] = []
    async with app_speech_models._lock():
        _drop_dead()
        current = app_speech_models.state.loaded.get(name)
        if current is not None:
            if engine != "llama" or current.context_length == context_length:
                current.touch()
                return {"ok": True, "name": name, "file": path.name, "port": current.port,
                        "context_length": current.context_length or None, "engine": current.engine,
                        "kind": current.kind, "already_loaded": True, "evicted": []}
            # A different context length needs a restart of that server.
            await asyncio.to_thread(_stop, app_speech_models.state.loaded.pop(name))
        # Chat and speech models are two pools: a speech model never evicts
        # the chat model, and the other way round.
        cap = app_settings.MAX_SPEECH_LOADED if speech else app_settings.MAX_LOADED
        while True:
            pool = [m for m in app_speech_models.state.loaded.values() if (m.kind in app_speech_models.SPEECH_KINDS) == speech]
            if len(pool) < cap:
                break
            spare = [m for m in pool if m.name not in keep]
            if not spare:
                break  # over the cap for now; the next load evicts
            lru = min(spare, key=lambda m: m.last_used)
            app_speech_models.state.loaded.pop(lru.name, None)
            await app_cache.save_slots(lru)
            await asyncio.to_thread(_stop, lru)
            evicted.append(lru.file)
            log.info("evicted %s to make room for %s", lru.name, name)
        port = app_speech_models._next_port()
        cache = app_cache.cache_flags(engine, name)
        if "--slot-save-path" in cache:
            app_cache._slot_dir(name).mkdir(parents=True, exist_ok=True)
        if speech:
            cmd = build_speech_command(path, engine, name, port, threads)
        elif engine == "mlx":
            cmd = app_engines.build_mlx_command(path, port, cache)
        else:
            cmd = build_command(path, name, port, context_length, gpu_layers, threads, cache)
        logfile = app_speech_models._log_path(name)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with open(logfile, "wb") as fh:
            try:
                env = None
                if speech:
                    env = {**os.environ, "MODELS_VOICES_DIR": str(app_voices.voices_dir())}
                if engine == "llama" and Path(cmd[0]).is_file():
                    # An installed build carries its shared libraries beside it.
                    lib = str(Path(cmd[0]).parent)
                    env = {**os.environ,
                           "LD_LIBRARY_PATH": os.pathsep.join(filter(None, [lib, os.environ.get("LD_LIBRARY_PATH")]))}
                proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env)
            except FileNotFoundError:
                if engine in app_speech_models.ENGINE_VENVS:
                    raise HTTPException(status_code=409, detail=f"the {engine} engine is not installed in "
                                                                "this runtime; install it on the Models page")
                if engine != "llama":
                    raise HTTPException(status_code=500, detail=f"cannot start {app_settings.SPEECH_PYTHON} "
                                                                "(MODELS_SPEECH_PYTHON)")
                raise HTTPException(status_code=409, detail="llama.cpp is not installed in this runtime; "
                                                            "install it on the Models page (Local tab)")
        ctx = 0 if speech else (app_engines.mlx_context(mlx_cfg) if mlx_cfg is not None else context_length)
        harmony = bool(mlx_cfg) and str(mlx_cfg.get("model_type")) == "gpt_oss"
        try:
            await wait_healthy(port, proc)
        except Exception as exc:
            await asyncio.to_thread(_stop, app_speech_models.Loaded(name, path.name, port, proc, ctx, engine, kind))
            raise HTTPException(status_code=502, detail=f"{exc}. Log tail: {app_speech_models._log_tail(name)}")
        m = app_speech_models.Loaded(name=name, file=path.name, port=port, proc=proc, context_length=ctx,
                   engine=engine, kind=kind, harmony=harmony, gpu_layers=gpu_layers, threads=threads,
                   cache_flags=cache if kind == "chat" else ())
        if engine == "llama":
            m.kv_type = cache[cache.index("-ctk") + 1] if "-ctk" in cache else "f16"
            m.slots = await app_cache._slot_count(m)
            await app_cache.restore_slots(m)
        app_speech_models.state.loaded[name] = m
    if kind == "chat":
        app_cache.start_warm_up(m)
    return {"ok": True, "name": name, "file": path.name, "port": port,
            "context_length": ctx or None, "engine": engine, "kind": kind,
            "already_loaded": False, "evicted": evicted, "restored": m.restored or None}


async def unload_model(file: str) -> Dict[str, Any]:
    async with app_speech_models._lock():
        m = app_speech_models._find_loaded(app_speech_models._safe_file(file))
        if m is None:
            return {"ok": True, "unloaded": False}
        app_speech_models.state.loaded.pop(m.name, None)
        saved = await app_cache.save_slots(m)
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
    rss = {name: _rss(getattr(m.proc, "pid", 0)) for name, m in app_speech_models.state.loaded.items()}
    return {"ram_total_bytes": ram.get("total") or None,
            "ram_available_bytes": ram.get("available") or None,
            "process_rss_bytes": rss, "gpu": _gpu()}

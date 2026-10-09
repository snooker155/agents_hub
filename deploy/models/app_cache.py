"""The prompt cache: settings, slot save and restore, prompt heads, warm-up and the cache overview."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_core
import app_engines
import app_hardware_search
import app_routes_models
import app_serving
import app_settings
import app_speech_models
import httpx

log = logging.getLogger("models_service")


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
    return app_settings.MODELS_DIR / ".cache"


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


def _disk_on(m: app_speech_models.Loaded) -> bool:
    return m.engine == "llama" and "--slot-save-path" in m.cache_flags


def _slot_key(m: app_speech_models.Loaded) -> Dict[str, Any]:
    """What saved slots must match to be restored into ``m``: the KV of one
    file, context length, KV type and llama.cpp build is not the KV of another."""
    try:
        st = (app_settings.MODELS_DIR / m.file).stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    return {"file": m.file, "size": size, "mtime": mtime, "context_length": m.context_length,
            "kv_type": m.kv_type, "llama": str(app_engines._llama_record().get("tag") or app_engines.llama_bin())}


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


async def _slot_action(client: Any, m: app_speech_models.Loaded, slot: int, action: str, filename: str) -> Dict[str, Any]:
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


async def save_slots(m: app_speech_models.Loaded) -> Dict[str, Any]:
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
            n = int(app_core._num(data.get("n_saved")))
            if n <= 0:
                (d / fname).unlink(missing_ok=True)
                continue
            saved.append({"file": fname, "tokens": n, "bytes": int(app_core._num(data.get("n_written"))),
                          "saved_at": app_core._now()})
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


async def restore_slots(m: app_speech_models.Loaded) -> Dict[str, Any]:
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
    entries.sort(key=lambda e: -int(app_core._num(e.get("tokens"))))
    started = time.monotonic()
    tokens = slots = 0
    async with httpx.AsyncClient(timeout=SLOT_IO_TIMEOUT) as client:
        for i, entry in enumerate(entries[:m.slots]):
            data = await _slot_action(client, m, i, "restore", str(entry["file"]))
            n = int(app_core._num(data.get("n_restored")))
            if n > 0:
                tokens += n
                slots += 1
    out = {"slots": slots, "tokens": tokens, "ms": int((time.monotonic() - started) * 1000), "at": app_core._now()}
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
                entry = {"key": key, "head": head, "first_seen": app_core._now(), "uses": 0,
                         "chars": len(json.dumps(head, ensure_ascii=False))}
            entry["uses"] = int(entry.get("uses") or 0) + 1
            entry["last_used"] = app_core._now()
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


async def warm_up(m: app_speech_models.Loaded) -> Dict[str, Any]:
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
                    "prompt_tokens": 0, "cached_tokens": 0, "ms": 0, "at": app_core._now()}
    if not todo:
        return w
    url = f"http://127.0.0.1:{m.port}/v1/chat/completions"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
        for entry in todo:
            if app_speech_models.state.loaded.get(m.name) is not m or m.proc.poll() is not None:
                w["state"] = "stopped"
                return w
            head = entry["head"]
            payload = {k: v for k, v in head.items() if k != "messages"}
            payload.update({"model": "default_model" if m.engine == "mlx" else m.name,
                            "messages": [*head.get("messages", []), {"role": "user", "content": "."}],
                            "max_tokens": 1, "stream": False})
            try:
                r = await client.post(url, json=payload)
                stats = app_core.call_stats(r.content[-app_core._USAGE_TAIL_BYTES:]) if r.status_code == 200 else None
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


def start_warm_up(m: app_speech_models.Loaded) -> bool:
    """Run :func:`warm_up` for ``m`` in the background; False when one runs."""
    task = _warmups.get(m.name)
    if task is not None and not task.done():
        return False
    _warmups[m.name] = asyncio.get_running_loop().create_task(warm_up(m))
    return True


async def _slot_count(m: app_speech_models.Loaded) -> int:
    """llama-server's number of slots (``-np``, or what it chose)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://127.0.0.1:{m.port}/props")
            return int(app_core._num(r.json().get("total_slots"))) if r.status_code == 200 else 0
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
            cfg = app_engines.mlx_config(path) or {}
            c = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
            layers, heads_n = int(c.get("num_hidden_layers") or 0), int(c.get("num_attention_heads") or 0)
            kv = int(c.get("num_key_value_heads") or heads_n)
            dim = int(c.get("head_dim") or (int(c.get("hidden_size") or 0) // heads_n if heads_n else 0))
            if layers and kv and dim:
                geo = {"kv_heads_total": layers * kv, "key_length": dim, "value_length": dim}
        else:
            module = app_routes_models._structure_module()
            md = module.parse_gguf(path)["metadata"] if module is not None else {}
            arch = md.get("general.architecture") or ""
            layers = int(app_core._num(md.get(f"{arch}.block_count")))
            kv = md.get(f"{arch}.attention.head_count_kv", md.get(f"{arch}.attention.head_count"))
            heads_n = md.get(f"{arch}.attention.head_count")
            if isinstance(heads_n, list):
                heads_n = max(heads_n) if heads_n else 0
            total = int(sum(kv)) if isinstance(kv, list) else int(app_core._num(kv)) * layers
            key_len = int(app_core._num(md.get(f"{arch}.attention.key_length")))
            if not key_len and heads_n:
                key_len = int(app_core._num(md.get(f"{arch}.embedding_length"))) // int(app_core._num(heads_n))
            val_len = int(app_core._num(md.get(f"{arch}.attention.value_length"))) or key_len
            if total and key_len:
                geo = {"kv_heads_total": total, "key_length": key_len, "value_length": val_len}
    except Exception:  # noqa: BLE001 - an estimate; an unreadable header leaves it out
        log.debug("kv geometry of %s failed", path, exc_info=True)
    _kv_geometry[str(path)] = (mtime, geo)
    return geo


def kv_bytes_per_token(m: app_speech_models.Loaded) -> Tuple[Optional[float], bool]:
    """KV bytes one token takes in ``m``, and whether that was measured."""
    if m.bytes_per_token:
        return m.bytes_per_token, True
    geo = kv_geometry(app_settings.MODELS_DIR / m.file)
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
    app_serving._drop_dead()
    mem = app_serving.memory()
    try:
        hw = app_hardware_search.hardware()
    except Exception:  # noqa: BLE001 - the GPU budget is informative
        hw = {}
    models: List[Dict[str, Any]] = []
    for m in sorted(app_speech_models.state.loaded.values(), key=lambda x: x.name):
        if m.kind != "chat":
            continue
        per, measured = kv_bytes_per_token(m)
        try:
            weights = sum(f.stat().st_size for f in ((app_settings.MODELS_DIR / m.file).rglob("*") if (app_settings.MODELS_DIR / m.file).is_dir()
                                                     else app_speech_models.split_parts(app_settings.MODELS_DIR / m.file)) if f.is_file())
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
        stored.append({"name": name, "loaded": name in app_speech_models.state.loaded,
                       "disk_bytes": disk,
                       "tokens": sum(int(app_core._num(e.get("tokens"))) for e in manifest.get("slots") or []
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

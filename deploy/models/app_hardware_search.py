"""Hardware detection, fit and speed estimates, and the Hugging Face search with its route."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_core
import app_routes_models
import app_serving
import app_settings
import app_speech_models
import httpx
from fastapi import APIRouter, HTTPException

log = logging.getLogger("models_service")
router = APIRouter()


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
            ram = app_serving._meminfo_linux().get("total", 0)
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
    sizes = {e["name"]: e.get("size_bytes") or 0 for e in app_speech_models.list_models()
             if e.get("engine") == "llama" and e.get("kind") == "chat"}
    seen = []
    for row in app_core.usage.snapshot()["rows"]:
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
    "image": [["text-to-image"]],
}
#: Purposes the worker engines serve (speech, transcription, images): no
#: GGUF filter, and only repos one of them runs (:func:`speech_engine_for`)
#: stay in the results.
SPEECH_PURPOSES = ("transcription", "speech", "image")
#: Speech repos without a task tag (the official Piper voices, Kokoro and
#: Kitten have none) are found by name: (tags, the word searched when the
#: person typed none; with a word, theirs is searched).
SPEECH_NAME_QUERIES: Dict[str, List[Tuple[List[str], str]]] = {
    "speech": [(["onnx"], "piper"), (["onnx"], "kokoro"), (["onnx"], "kitten-tts"), (["onnx"], "supertonic"),
               (["mlx"], "qwen3-tts")],
    "transcription": [],
    "image": [([], "qwen-image")],
}
#: The repos the presets use, shown first when the search matches them: the
#: Hub reports no downloads for them, so a sort would bury them.
SPEECH_FEATURED = {"speech": ["rhasspy/piper-voices", "fastrtc/kokoro-onnx", "Supertone/supertonic-3",
                              "KittenML/kitten-tts-nano-0.8-int8", "ResembleAI/chatterbox",
                              "mlx-community/chatterbox-4bit", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
                              "myshell-ai/OpenVoiceV2"],
                   "transcription": ["Systran/faster-whisper-small", "Systran/faster-whisper-large-v3"],
                   "image": ["mlx-community/Qwen-Image-2512-8bit", "mlx-community/Qwen-Image-2512-4bit",
                             "Qwen/Qwen-Image-2512"]}


def speech_engine_for(repo: str, tags: List[str], purpose: str) -> Optional[str]:
    """The engine that would run a speech repo, from its name and tags, the
    same hints :func:`speech_packages` reads its files with; None for one
    none of them runs (an ONNX voice of another family)."""
    low = repo.lower()
    if purpose == "transcription":
        return "whisper" if "whisper" in low else None
    if purpose == "image":
        # Qwen-Image repos: mflux's saved layout or Qwen's own; MLX runs on Apple silicon only.
        return "mflux" if "qwen" in low and "image" in low and app_speech_models.mlx_platform() else None
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
        return "chatterbox_mlx" if app_speech_models.mlx_platform() else None  # MLX runs on Apple silicon only
    if low.startswith("mlx-community/qwen3-tts"):
        return "qwen3_tts" if app_speech_models.mlx_platform() else None
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
                "engine": raw["_engine"], "kind": app_speech_models.ENGINE_KIND[raw["_engine"]],
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
    with app_core.http_client(timeout=20.0) as client:
        for repo in featured:
            resp = client.get(f"{app_settings.HF_BASE}/api/models/{repo}",
                              params=[("expand[]", e) for e in _SEARCH_EXPAND], headers=app_core._hf_headers())
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
                resp = client.get(f"{app_settings.HF_BASE}/api/models", params=params, headers=app_core._hf_headers())
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
            return app_speech_models.speech_packages(raw["id"], app_routes_models.hf_tree(raw["id"], "main"))
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
        with app_core.http_client(timeout=15.0) as client:
            resp = client.get(f"{app_settings.HF_BASE}/api/models/{repo}",
                              params=[("expand[]", e) for e in ("gguf", "tags", "pipeline_tag")],
                              headers=app_core._hf_headers())
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


@router.get("/hf/search", dependencies=app_core.auth)
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


@router.get("/hardware", dependencies=app_core.auth)
async def get_hardware() -> Dict[str, Any]:
    """What fit and speed estimates assume about this machine."""
    return await asyncio.to_thread(hardware)

"""Speech engine tables, engine environments, speech packages and the loaded-model state."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_core
import app_engines
import app_settings
import app_voices
from fastapi import HTTPException

log = logging.getLogger("models_service")


# ── Speech models ────────────────────────────────────────────────────────────

#: Engine -> purpose, the same words the hub's special models use.
ENGINE_KIND = {"whisper": "transcription", "piper": "speech", "kokoro": "speech", "kitten": "speech",
               "supertonic": "speech", "chatterbox": "speech", "chatterbox_mlx": "speech", "openvoice": "speech",
               "mflux": "image",
               "mlx": "chat", "deepfilternet": "cleanup", "resemble_enhance": "cleanup"}
#: Every engine the Models page lists, chat first.
ALL_ENGINES = ("llama", "mlx", "whisper", "piper", "kokoro", "kitten", "supertonic", "chatterbox", "chatterbox_mlx",
               "openvoice", "mflux", "deepfilternet", "resemble_enhance")
#: Engines that run only on Apple silicon: listed nowhere else.
APPLE_ENGINES = ("mlx", "chatterbox_mlx", "deepfilternet", "mflux")
SPEECH_KINDS = ("speech", "transcription")
IMAGE_KINDS = ("image",)
#: Every kind a worker (speech_worker.py) serves: not chat.
WORKER_KINDS = SPEECH_KINDS + IMAGE_KINDS
#: Engine -> the modules that have to import for it to run, comma separated.
#: Kitten runs on the worker's own code over onnxruntime and phonemizer,
#: OpenVoice's converter on the worker's openvoice_vc.py over torch.
ENGINE_MODULES = {"whisper": "faster_whisper", "piper": "piper", "kokoro": "kokoro_onnx",
                  "kitten": "onnxruntime,phonemizer,espeakng_loader", "supertonic": "supertonic",
                  "chatterbox": "chatterbox,torch,librosa,perth,pkg_resources", "openvoice": "torch,numpy,av",
                  "chatterbox_mlx": "mlx_audio,mlx,av",
                  # The worker's own modules too: a found interpreter (FOUND_ENGINES) may lack them.
                  "mflux": "mlx,mflux,fastapi,uvicorn,multipart",
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
    # Qwen-Image on MLX (speech_worker.py, QwenImageMflux), the version it
    # was checked with; it brings torch and transformers, so an environment
    # of its own (ENGINE_VENVS).
    "mflux": ["mflux>=0.19,<0.20"],
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
                "deepfilternet": "mlx-audio", "resemble_enhance": "torch", "mflux": "mflux"}
#: Environment name -> the variable that points at an interpreter of one's
#: own instead.
VENV_PYTHON_ENV = {"torch": "MODELS_TORCH_PYTHON", "mlx-audio": "MODELS_MLX_AUDIO_PYTHON",
                   "mflux": "MODELS_MFLUX_PYTHON"}
#: Engines whose package people often have installed already, under the
#: command they run it with: before making an environment of its own, the
#: runtime looks for an interpreter that imports the package
#: (:func:`found_python`) and runs the engine there.
FOUND_ENGINES = {"mflux": ("mflux", "mflux-generate")}
#: Seconds a fruitless search is remembered before looking again.
FOUND_SEARCH_TTL = 120.0
TORCH_ENGINES = tuple(e for e, v in ENGINE_VENVS.items() if v == "torch")
#: What a speech worker itself needs, for an environment made for it.
WORKER_PACKAGES = ["fastapi>=0.110,<1", "uvicorn>=0.29,<1", "python-multipart>=0.0.9", "numpy>=1.24", "av>=12"]
#: Engine -> the format column of the model list.
ENGINE_FORMAT = {"whisper": "ctranslate2", "piper": "onnx", "kokoro": "onnx", "kitten": "onnx",
                 "supertonic": "onnx", "chatterbox": "torch", "chatterbox_mlx": "mlx", "openvoice": "torch",
                 "mflux": "mlx", "mlx": "mlx", "deepfilternet": "mlx", "resemble_enhance": "torch"}
#: The folders of a Qwen-Image repo on Hugging Face (speech_worker.QWEN_IMAGE_DIRS).
QWEN_IMAGE_DIRS = ("transformer", "text_encoder", "vae", "tokenizer")
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
        spec = importlib.util.spec_from_file_location("hub_speech_worker", app_settings.SPEECH_WORKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        _worker_module = module
    return _worker_module


def speech_engine_of(path: Path) -> Optional[str]:
    """The worker engine (speech or image) that serves the directory
    ``path``, from its files; None for anything else."""
    return _worker().detect_engine(path) if path.is_dir() else None


def pool_of(kind: str) -> str:
    """Which pool of loaded models ``kind`` counts in: ``chat``, ``speech``
    (speech and transcription) or ``image``; each has its own cap and a
    load evicts only within its pool."""
    if kind in IMAGE_KINDS:
        return "image"
    return "speech" if kind in SPEECH_KINDS else "chat"


def engines() -> Dict[str, bool]:
    """Which engines run here: llama.cpp (a binary), MLX and the speech
    engines (packages that import under ``SPEECH_PYTHON``). MLX is only
    listed on Apple silicon, the one place it runs."""
    found = {"llama": app_engines.llama_installed(), **speech_engines()}
    if not mlx_platform():
        for e in APPLE_ENGINES:
            found.pop(e, None)
    return found


def mlx_platform() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def venv_dir(name: str) -> Path:
    """An engine environment (:data:`ENGINE_VENVS`), beside the models (a
    volume in the image, so an install outlives the container)."""
    return app_engines._engines_dir() / name


def _pinned_python(name: str) -> str:
    return (os.environ.get(VENV_PYTHON_ENV.get(name, "")) or "").strip()


def engine_python(engine: str) -> str:
    """The interpreter ``engine`` runs under: the one its ``VENV_PYTHON_ENV``
    variable names, else its own environment once made, else (for a
    :data:`FOUND_ENGINES` engine) an interpreter on this machine that
    already has the package, else its own environment, to be made;
    ``SPEECH_PYTHON`` for an engine with no environment of its own."""
    name = ENGINE_VENVS.get(engine)
    if name is None:
        return app_settings.SPEECH_PYTHON
    pinned = _pinned_python(name)
    if pinned:
        return pinned
    own = str(venv_dir(name) / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    if engine not in FOUND_ENGINES or Path(own).is_file():
        return own
    return found_python(engine) or own


def python_source(engine: str) -> str:
    """Where :func:`engine_python` got the interpreter: ``runtime`` (this
    one), ``pinned`` (the variable), ``own`` (the engine's environment) or
    ``found`` (an install of the person's own)."""
    name = ENGINE_VENVS.get(engine)
    if name is None:
        return "runtime"
    if _pinned_python(name):
        return "pinned"
    python = engine_python(engine)
    return "found" if engine in FOUND_ENGINES and python == found_python(engine) else "own"


_found: Dict[str, Tuple[float, str]] = {}


def _conda_roots() -> List[Path]:
    home = Path.home()
    roots: List[Path] = []
    exe = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if exe:
        roots.append(Path(exe).resolve().parents[1])
    roots += [home / d for d in ("miniforge3", "mambaforge", "miniconda3", "anaconda3", ".conda")]
    roots += [Path("/opt/homebrew/Caskroom/miniforge/base"), Path("/opt/miniconda3"), Path("/opt/anaconda3"),
              Path("/usr/local/Caskroom/miniforge/base")]
    return roots


def candidate_pythons(package: str, command: str) -> List[str]:
    """Interpreters that may have ``package``: the one ``command`` on PATH
    runs under (its shebang, else the python beside it), a uv tool, a pipx
    venv and every conda environment, those that exist, in that order."""
    out: List[str] = []
    exe = shutil.which(command)
    if exe:
        try:
            first = Path(exe).open("rb").readline().decode("utf-8", "ignore").strip()
        except OSError:
            first = ""
        if first.startswith("#!") and not first[2:].strip().startswith("/usr/bin/env"):
            out.append(first[2:].strip().split()[0])
        out.append(str(Path(exe).resolve().parent / "python"))
    home = Path.home()
    out += [str(home / ".local" / "share" / "uv" / "tools" / package / "bin" / "python"),
            str(home / ".local" / "pipx" / "venvs" / package / "bin" / "python")]
    seen_roots = set()
    for root in _conda_roots():
        envs = root / "envs"
        if root in seen_roots or not envs.is_dir():
            continue
        seen_roots.add(root)
        for env in sorted(p for p in envs.iterdir() if p.is_dir()):
            out.append(str(env / "bin" / "python"))
    found: List[str] = []
    for p in out:
        if p not in found and Path(p).is_file():
            found.append(p)
    return found


def found_python(engine: str) -> str:
    """An interpreter on this machine that already imports ``engine``'s
    package (:data:`FOUND_ENGINES`), or "" when none does. A find is kept
    while that interpreter exists; a miss is kept :data:`FOUND_SEARCH_TTL`
    seconds, so the model list does not search on every call."""
    spec = FOUND_ENGINES.get(engine)
    if spec is None:
        return ""
    package, command = spec
    cached = _found.get(engine)
    now = time.monotonic()
    if cached is not None:
        if cached[1] and Path(cached[1]).is_file():
            return cached[1]
        if not cached[1] and now - cached[0] < FOUND_SEARCH_TTL:
            return ""
    hit = ""
    for python in candidate_pythons(package, command):
        if _modules_found(python, {engine: package}).get(engine):
            hit = python
            break
    _found[engine] = (now, hit)
    if hit:
        log.info("%s runs under %s, which already has %s", engine, hit, package)
    return hit


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
            return own + _worker().recorded_voices(app_voices.voices_dir())
    except (OSError, ValueError, zipfile.BadZipFile):
        log.debug("could not read the voices of %s", path, exc_info=True)
    return []


def package_name(engine: str, stem: str) -> str:
    """A model directory's name: the stem, made file-safe, with the engine in
    front when the stem does not say it (``ru_RU-irina-medium`` becomes
    ``piper-ru_RU-irina-medium``), so the hub can tell what it is from the id
    alone."""
    slug = re.sub(r"[^\w.-]+", "-", stem).strip("-.") or engine
    if engine == "mflux":
        return slug  # a Qwen-Image repo's name says what it is
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
    low_repo = repo.lower()
    if ("qwen" in low_repo and "image" in low_repo and "text_encoder_2" not in by_dir
            and all(any(d == q or d.startswith(q + "/") for d in by_dir) for q in QWEN_IMAGE_DIRS)
            and any(d.split("/")[0] == "transformer" and any(n.endswith(".safetensors") for n in names)
                    for d, names in by_dir.items())):
        # A Qwen-Image model (mflux's saved layout or Qwen's diffusers one):
        # the four folders whole, kept as folders, plus the index at the top.
        files = sorted(p for p in sizes if p.split("/")[0] in QWEN_IMAGE_DIRS
                       and not p.rsplit("/", 1)[-1].startswith("."))
        files += [f for f in ("model_index.json",) if f in top]
        add("mflux", repo_name, files, save_as={f: f for f in files})
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
    loaded_at: str = field(default_factory=app_core._now)
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
    path = app_settings.MODELS_DIR / f
    if not path.exists() and not f.lower().endswith(".gguf"):
        if (app_settings.MODELS_DIR / f"{f}.gguf").is_file():
            path = app_settings.MODELS_DIR / f"{f}.gguf"
        else:
            # A split model is named by its stem; its file is the first part.
            firsts = sorted(app_settings.MODELS_DIR.glob(f"{f}-00001-of-*.gguf"))
            if firsts:
                path = firsts[0]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no model file {f!r} in {app_settings.MODELS_DIR}")
    return _first_part(path)


def _find_loaded(name_or_file: str) -> Optional[Loaded]:
    key = model_name(name_or_file)
    return state.loaded.get(key)


def _marker(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads((path / app_core.MODEL_MARKER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_models() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not app_settings.MODELS_DIR.is_dir():
        return out
    # Asked once per listing, and only when a speech model is there.
    installed_engines: Optional[Dict[str, bool]] = None
    for p in sorted(app_settings.MODELS_DIR.iterdir()):
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
                     "voices": speech_voices(p, engine),
                     # A download records its repo, an import where it came from.
                     "source": _marker(p).get("repo") or _marker(p).get("source")}
            if not installed:
                entry["note"] = (f"the {engine} engine is not installed in this runtime; install it "
                                 "on the Models page or with pip install -r requirements-speech.txt")
            out.append(entry)
        elif p.is_dir() and (cfg := app_engines.mlx_config(p)) is not None:
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
            s.bind((app_settings.LLAMA_HOST, port))
            return True
        except OSError:
            return False


def _next_port() -> int:
    used = {m.port for m in state.loaded.values()}
    for port in range(app_settings.BASE_PORT, app_settings.BASE_PORT + 200):
        if port not in used and _port_free(port):
            return port
    raise HTTPException(status_code=503, detail=f"no free port from {app_settings.BASE_PORT}")


def _log_path(name: str) -> Path:
    return app_settings.MODELS_DIR / ".logs" / f"{name}.log"


def _log_tail(name: str, chars: int = 800) -> str:
    try:
        return _log_path(name).read_text(errors="replace")[-chars:]
    except OSError:
        return ""

"""The llama.cpp install and the imports from Ollama, LM Studio and MLX folders."""
from __future__ import annotations

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
import app_settings
import app_speech_models
import httpx

log = logging.getLogger("models_service")


# ── llama.cpp ────────────────────────────────────────────────────────────────

#: Where installed engines live: beside the models, hidden from the list.
def _engines_dir() -> Path:
    return app_settings.MODELS_DIR / ".engines"


def _llama_record() -> Dict[str, Any]:
    try:
        data = json.loads((_engines_dir() / "llama.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def llama_bin() -> str:
    """The llama-server to start: a pinned ``LLAMA_SERVER_BIN``, else the
    build installed here, else ``llama-server`` found on PATH at start."""
    if not app_settings._LLAMA_BIN_PINNED:
        installed = str(_llama_record().get("bin") or "")
        if installed and Path(installed).is_file():
            return installed
    return app_settings.LLAMA_SERVER_BIN


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
    app_core.jobs.update(job_id, status="running", message="looking for the latest llama.cpp build")
    try:
        with app_core.http_client(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            tag, url, size = find_llama_release(client)
            target = _engines_dir() / "llama.cpp" / tag
            archive = _engines_dir() / f"llama-{tag}.tar.gz"
            archive.parent.mkdir(parents=True, exist_ok=True)
            app_core.jobs.update(job_id, total=size, message=f"downloading llama.cpp {tag}")
            done, last = 0, 0.0
            with client.stream("GET", url) as resp, open(archive, "wb") as fh:
                if resp.status_code >= 400:
                    raise RuntimeError(f"GitHub answered HTTP {resp.status_code} for {url}")
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last > 0.5:
                        last = time.monotonic()
                        app_core.jobs.update(job_id, completed=done,
                                    percent=round(100.0 * done / size, 1) if size else 0.0)
        app_core.jobs.update(job_id, completed=done, message=f"unpacking llama.cpp {tag}")
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
            {"tag": tag, "bin": str(binary), "installed_at": app_core._now()}), encoding="utf-8")
        for old in (_engines_dir() / "llama.cpp").iterdir():
            if old != target and old.is_dir() and not any(m.engine == "llama" for m in app_speech_models.state.loaded.values()):
                shutil.rmtree(old, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - the job records why
        app_core.jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=app_core._now())
        return
    app_core.jobs.update(job_id, status="done", percent=100.0, message=f"llama.cpp {tag} installed", finished_at=app_core._now())


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
                 "quantization": config.get("file_type"), "imported": (app_settings.MODELS_DIR / dest).exists(),
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
    module = app_routes_models._structure_module()
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
    app_core.jobs.update(job_id, status="running", total=total, message=f"copying {dest.name}")
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
                    app_core.jobs.update(job_id, completed=done, percent=round(100.0 * done / total, 1) if total else 0.0)
        part.replace(dest)
    except Exception as exc:  # noqa: BLE001 - the job records why
        part.unlink(missing_ok=True)
        app_core.jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:400], finished_at=app_core._now())
        return
    app_core.jobs.update(job_id, status="done", completed=done, percent=100.0, message=f"saved {dest.name}",
                finished_at=app_core._now())


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
    mlx_ok = app_speech_models.mlx_platform()
    for repo in sorted(p for p in root.glob("*/*") if p.is_dir()):
        publisher = repo.parent.name
        if app_speech_models.speech_engine_of(repo) == "mflux":
            # A Qwen-Image folder (LM Studio downloads them for other apps):
            # an image model the mflux engine runs.
            dest = re.sub(r"[^\w.-]+", "-", repo.name).strip("-.")
            bits = re.search(r"(\d+)[-_]?bit", repo.name.lower())
            out.append(({"name": f"{publisher}/{repo.name}", "file": dest, "format": "mlx", "kind": "image",
                         "engine": "mflux",
                         "size_bytes": sum(f.stat().st_size for f in repo.rglob("*") if f.is_file()),
                         "family": "qwen-image", "quantization": f"{bits.group(1)}-bit" if bits else None,
                         "parameter_size": None, "imported": (app_settings.MODELS_DIR / dest).exists(),
                         "compatible": mlx_ok, "embedding": False,
                         "note": None if mlx_ok else "an MLX model: MLX runs on Apple silicon only"}, repo))
            continue
        config = mlx_config(repo)
        if config is not None:
            quant = config.get("quantization") if isinstance(config.get("quantization"), dict) else {}
            dest = re.sub(r"[^\w.-]+", "-", repo.name).strip("-.")
            entry = {"name": f"{publisher}/{repo.name}", "file": dest, "format": "mlx",
                     "size_bytes": sum(f.stat().st_size for f in repo.rglob("*") if f.is_file()),
                     "family": config.get("model_type"),
                     "quantization": f"{quant['bits']}-bit" if quant.get("bits") else None,
                     "parameter_size": None, "imported": (app_settings.MODELS_DIR / dest).exists(),
                     "compatible": mlx_ok, "embedding": False,
                     "note": None if mlx_ok else "an MLX model: MLX runs on Apple silicon only"}
            out.append((entry, repo))
            continue
        for gguf in sorted(repo.glob("*.gguf")):
            if gguf.name.lower().startswith("mmproj"):
                continue
            entry = {"name": f"{publisher}/{repo.name}/{gguf.name}", "file": gguf.name, "format": "gguf",
                     "size_bytes": gguf.stat().st_size, "family": None,
                     "quantization": app_routes_models.quantization_of(gguf.name), "parameter_size": None,
                     "imported": (app_settings.MODELS_DIR / gguf.name).exists(), "compatible": True,
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
    staging = app_settings.MODELS_DIR / f".{dest.name}.import"
    files = [f for f in src.rglob("*") if f.is_file() and not f.name.startswith(".")]
    total = sum(f.stat().st_size for f in files)
    app_core.jobs.update(job_id, status="running", total=total, message=f"copying {dest.name}")
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
                        app_core.jobs.update(job_id, completed=done, percent=round(100.0 * done / total, 1) if total else 0.0)
        staging.replace(dest)
    except Exception as exc:  # noqa: BLE001 - the job records why
        shutil.rmtree(staging, ignore_errors=True)
        app_core.jobs.update(job_id, status="error", error=f"{type(exc).__name__}: {exc}"[:400], finished_at=app_core._now())
        return
    app_core.jobs.update(job_id, status="done", completed=done, percent=100.0, message=f"saved {dest.name}",
                finished_at=app_core._now())


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
    return [app_settings.SPEECH_PYTHON, "-m", "mlx_lm.server", "--model", str(path), "--port", str(port),
            "--host", app_settings.LLAMA_HOST, "--log-level", "WARNING", *cache]


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

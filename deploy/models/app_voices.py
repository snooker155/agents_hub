"""Recorded voices, voice cleanup, base voice choice and the voice routes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import app_core
import app_routes_engines
import app_settings
import app_speech_models
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

log = logging.getLogger("models_service")
router = APIRouter()


# ── Recorded voices ──────────────────────────────────────────────────────────
# Samples the cloning engines (Chatterbox, OpenVoice) read and speak in; see
# "Voices" in speech_worker.py. Kept here, not per model, so one recording
# serves both engines. Who recorded a voice and whether others may pick it
# is the hub's to decide: it sends ``owner`` and ``shared`` and filters.

def voices_dir() -> Path:
    """Where the recordings live: hidden beside the models, so the model
    list skips it."""
    return app_settings.MODELS_DIR / ".voices"


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
    if "reference" not in record:
        # Recorded before the best part was picked: worked out once, kept.
        try:
            record["reference"] = _reference((d / "sample.wav").read_bytes())
            _write_voice(name, record)
        except OSError:
            pass
    running = cleanup_running(name)
    if running is not None:
        record["cleaning"] = {"job_id": running["id"], "mode": (running.get("meta") or {}).get("mode")}
    return record


def _reference(wav: bytes) -> Optional[List[float]]:
    """``[start, end]``: the seconds of a sample Chatterbox listens to
    closely (speech_worker.reference_start); None for one it cannot read."""
    try:
        return list(app_speech_models._worker().reference_window(wav))
    except Exception:  # noqa: BLE001 - a sample it cannot read shows no part
        log.warning("could not find the reference part of a sample", exc_info=True)
        return None


def list_voices() -> List[Dict[str, Any]]:
    return [r for n in app_speech_models._worker().recorded_voices(voices_dir()) if (r := voice_record(n)) is not None]


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
        wav, seconds = app_speech_models._worker().prepare_sample(audio)
    except app_speech_models._worker().WorkerError as exc:
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
              "duration": seconds, "cleanup": "", "reference": _reference(wav),
              "updated_at": app_core._now()}
    record.setdefault("created_at", record["updated_at"])
    _write_voice(name, record)
    return {**record, "name": name}


def update_voice(name: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    record = voice_record(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    record.update({k: v for k, v in fields.items() if k in _VOICE_FIELDS and v is not None})
    record["updated_at"] = app_core._now()
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
    return "deepfilternet" if mode == "denoise" and app_speech_models.mlx_platform() else "resemble_enhance"


def cleanup_weights_dir(engine: str) -> Path:
    """Hidden beside the models, so the model list skips it."""
    return app_settings.MODELS_DIR / ".cleanup" / engine


def cleanup_running(name: str) -> Optional[Dict[str, Any]]:
    """The queued or running cleanup of voice ``name``, if any."""
    return next((j for j in app_core.jobs.list() if j["kind"] == "voice_cleanup" and j["status"] in ("queued", "running")
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
        wav = _original(d).read_bytes()
        _write_sample(d, wav)
        record["reference"] = _reference(wav)
    record.pop("cleaning", None)
    record.update(cleanup="", updated_at=app_core._now())
    _write_voice(name, record)
    return record


def _fetch_cleanup_weights(job_id: str, engine: str) -> None:
    repo, files = CLEANUP_WEIGHTS[engine]
    root = cleanup_weights_dir(engine)
    for src, dest in files.items():
        target = root / dest
        if not target.is_file():
            app_core._fetch(job_id, f"{app_settings.HF_BASE}/{repo}/resolve/main/{src}", target)


def run_cleanup(job_id: str, name: str, mode: str) -> None:
    """Install the engine when missing, fetch its weights, clean the
    original and keep the result as the voice's sample, levelled and trimmed
    the way a recording is (speech_worker.prepare_sample)."""
    engine = cleanup_engine(mode)
    d = voices_dir() / name

    def fail(error: str) -> None:
        app_core.jobs.update(job_id, status="error", error=error[:500], finished_at=app_core._now())

    app_core.jobs.update(job_id, status="running", message=f"preparing {engine}")
    if not app_speech_models.engines().get(engine):
        error = app_routes_engines.install_steps(job_id, engine)
        if error:
            return fail(f"installing {engine}: {error}")
    try:
        _fetch_cleanup_weights(job_id, engine)
    except app_core._FetchError as exc:
        return fail(f"downloading the {engine} weights: {exc}; ask again to resume")
    try:
        original = _original(d)
        stamp = original.stat().st_mtime
    except OSError:
        return fail(f"the voice {name!r} is gone")
    out = d / "cleaned.tmp.wav"
    cmd = [app_speech_models.engine_python(engine), str(app_settings.VOICE_ENHANCE), "--engine", engine, "--mode", mode,
           "--weights", str(cleanup_weights_dir(engine)), str(original), str(out)]
    app_core.jobs.update(job_id, message=f"cleaning with {engine}", percent=0.0, completed=0, total=0)
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
        wav, seconds = app_speech_models._worker().prepare_sample(out.read_bytes())
    except app_speech_models._worker().WorkerError as exc:
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
    record.update(cleanup=mode, cleanup_engine=engine, duration=seconds,
                  reference=_reference(wav), updated_at=app_core._now())
    _write_voice(name, record)
    took = ""
    try:
        took = f" in {json.loads(proc.stdout.strip().splitlines()[-1])['seconds']:.0f} s"
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    app_core.jobs.update(job_id, status="done", percent=100.0, message=f"{name} cleaned with {engine}{took}",
                finished_at=app_core._now())


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
    if engine in app_speech_models.APPLE_ENGINES and not app_speech_models.mlx_platform():
        raise HTTPException(status_code=409, detail="MLX runs on Apple silicon only")
    job = app_core.jobs.create("voice_cleanup", f"{name}: {mode}", meta={"voice": name, "mode": mode, "engine": engine})
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
    lang = app_speech_models._worker().chatterbox_language(text, record.get("language"))
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


@router.get("/voices", dependencies=app_core.auth)
async def get_voices() -> Dict[str, Any]:
    return {"voices": await asyncio.to_thread(list_voices)}


@router.post("/voices", dependencies=app_core.auth)
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
    fields.update({"owner": str(form.get("owner") or "")[:200], "consent_at": app_core._now()})
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


@router.patch("/voices/{name}", dependencies=app_core.auth)
async def patch_voice(name: str, body: VoicePatch) -> Dict[str, Any]:
    _voice_dir(name)
    fields = _clean_fields(body.language, body.gender, body.shared, body.base_model, body.base_voice)
    return await asyncio.to_thread(update_voice, name, fields)


class VoiceCleanupBody(BaseModel):
    mode: str


@router.post("/voices/{name}/cleanup", dependencies=app_core.auth)
async def cleanup_voice(name: str, body: VoiceCleanupBody) -> Dict[str, Any]:
    return await asyncio.to_thread(start_cleanup, name, body.mode.strip().lower())


@router.get("/voices/{name}/audio", dependencies=app_core.auth)
async def voice_audio(name: str, original: bool = False) -> Response:
    """The sample the engines use, or with ``original`` the one recorded."""
    d = _voice_dir(name)
    if not (d / "sample.wav").is_file():
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    path = d / ORIGINAL_SAMPLE if original and (d / ORIGINAL_SAMPLE).is_file() else d / "sample.wav"
    return Response(content=path.read_bytes(), media_type="audio/wav")


@router.delete("/voices/{name}", dependencies=app_core.auth)
async def delete_voice(name: str) -> Dict[str, Any]:
    d = _voice_dir(name)
    if not d.is_dir():
        raise HTTPException(status_code=404, detail=f"no recorded voice {name!r}")
    if cleanup_running(name) is not None:
        raise HTTPException(status_code=409, detail=f"{name!r} is being cleaned; wait for it to finish")
    await asyncio.to_thread(shutil.rmtree, d)
    return {"ok": True, "name": name}

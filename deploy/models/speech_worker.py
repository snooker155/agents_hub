"""
One loaded speech model of the model runtime, served on its own port.

The runtime (app.py next to this file) starts one of these per loaded speech
model, the way it starts one llama-server per loaded GGUF, and proxies its
OpenAI-shaped gateway routes here. The engines:

whisper
    Transcription with faster-whisper over a CTranslate2 Whisper directory
    (``model.bin``, ``config.json``, ``tokenizer.json`` or ``vocabulary.*``),
    for example Systran/faster-whisper-small on Hugging Face.
piper
    Speech with Piper over one voice (``<voice>.onnx`` and
    ``<voice>.onnx.json``), for example rhasspy/piper-voices.
kokoro
    Speech with kokoro-onnx over ``kokoro-*.onnx`` and ``voices-*.bin``,
    for example fastrtc/kokoro-onnx.
kitten
    Speech with Kitten TTS over ``config.json``, one ``.onnx`` and
    ``voices.npz``, for example KittenML/kitten-tts-nano-0.8-int8. English
    only. Run here on onnxruntime and espeak-ng phonemes, the way the
    kittentts package does it, without that package's spaCy dependency.
supertonic
    Speech with the supertonic package over ``onnx/`` (four models,
    ``tts.json``, ``unicode_indexer.json``) and ``voice_styles/*.json``,
    for example Supertone/supertonic-3, which reads 31 languages, Russian
    among them.
chatterbox
    Speech in a recorded voice with Chatterbox Multilingual (Resemble AI,
    23 languages, Russian among them) over ``t3_mtl23ls_v2.safetensors``,
    ``s3gen.pt``, ``ve.pt`` and ``grapheme_mtl_merged_expanded_v1.json``
    from ResembleAI/chatterbox. Its voices are the samples people recorded
    (see Voices below) and ``default``, the model's own. Every result
    carries Resemble's inaudible Perth watermark, which marks it as
    synthesized. About four times slower than the speech lasts on a Mac's
    GPU, faster than it on CUDA.
chatterbox_mlx
    The same model on Apple's MLX through mlx-audio's port, over a
    mlx-community/chatterbox-* checkpoint (``model.safetensors``,
    ``tokenizer.json``, ``config.json`` with ``model_type`` chatterbox, the
    speech tokenizer in ``s3tokenizer/``). Apple silicon only; about twice
    as fast as the speech lasts, ten times the torch engine on the same
    Mac. The port adds no watermark.
openvoice
    OpenVoice's tone color converter (``config.json`` and
    ``checkpoint.pth`` from myshell-ai/OpenVoiceV2's ``converter/``). It
    reads no text: the runtime has another speech model read it and sends
    that audio here, and the converter gives it the timbre of a recorded
    voice. Fast, since both steps take one pass each; the intonation stays
    the reading model's. See openvoice_vc.py.

Voices
    Samples people recorded, in ``MODELS_VOICES_DIR`` (the runtime sets
    it): one directory per voice with ``sample.wav`` and ``voice.json``.
    The two cloning engines list them as their voices and keep what they
    compute from a sample (Chatterbox's conditionals, OpenVoice's tone
    color) in its ``cache/``, so a voice is analysed once per model.

Routes
    GET  /health                    200 once the model is loaded
    GET  /voices                    the voices a speech model knows
    POST /v1/audio/transcriptions   multipart: file, language, prompt, response_format
    POST /v1/audio/speech           JSON: input, voice, response_format, speed
                                    (Chatterbox also takes exaggeration and cfg_weight)
    POST /v1/audio/convert          multipart: file, voice, source, tau, response_format
                                    (OpenVoice: speech in, the same speech in ``voice`` out)

The model loads before the server starts listening, so a refused connection
means "still loading" to the runtime's health wait. Engines are imported only
here and only when a model loads, so the runtime lists and detects speech
models without any engine installed.

    python speech_worker.py --engine piper --model /models/piper-ru_RU-irina-medium --port 8400
"""
from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("speech_worker")

#: Engine -> what it does, the purpose name the hub uses for it.
ENGINE_KIND = {"whisper": "transcription", "piper": "speech", "kokoro": "speech", "kitten": "speech",
               "supertonic": "speech", "chatterbox": "speech", "chatterbox_mlx": "speech", "openvoice": "speech"}
#: Engines whose voices are the samples people recorded.
CLONING_ENGINES = ("chatterbox", "chatterbox_mlx", "openvoice")

#: Chatterbox Multilingual's weights; the first file is what tells it apart.
CHATTERBOX_FILES = ("t3_mtl23ls_v2.safetensors", "s3gen.pt", "ve.pt", "grapheme_mtl_merged_expanded_v1.json")
#: The languages Chatterbox Multilingual reads.
CHATTERBOX_LANGS = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms",
                    "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh")

#: The rate a recorded sample is kept at, and how long it may be: Chatterbox
#: listens closely to 10 s of it (:func:`reference_start` picks which),
#: OpenVoice averages whatever it gets.
SAMPLE_RATE = 24000
SAMPLE_MIN_SECONDS = 3.0
SAMPLE_MAX_SECONDS = 30.0
#: A voice name: what the ``voice`` field of a speech request carries.
VOICE_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$"

#: espeak-ng keeps its data path in a fixed buffer and quietly falls back to
#: the path it was built with when the real one is longer, which ends the
#: process. A data directory past this length is reached through a symlink.
_ESPEAK_PATH_MAX = 100

#: Kokoro voice name prefix -> the language its phonemizer needs.
_KOKORO_LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it",
                "p": "pt-br", "j": "ja", "z": "cmn"}

#: Supertonic's model files, under ``onnx/`` beside ``voice_styles/``.
SUPERTONIC_FILES = ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx",
                    "vocoder.onnx", "tts.json", "unicode_indexer.json")
#: What Supertonic 2 reads; Supertonic 3 reads 31 languages and "na" for
#: any other, Supertonic 1 English only.
_SUPERTONIC2_LANGS = ("en", "ko", "es", "pt", "fr")

#: Output format -> (PyAV container, codec, MIME type). WAV and raw PCM need
#: no encoder.
_ENCODED = {
    "mp3": ("mp3", "libmp3lame", "audio/mpeg"),
    "opus": ("ogg", "libopus", "audio/ogg"),
    "aac": ("adts", "aac", "audio/aac"),
    "flac": ("flac", "flac", "audio/flac"),
}


class WorkerError(Exception):
    """A request the model cannot serve; ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ── files ────────────────────────────────────────────────────────────────────

def detect_engine(path: Path) -> Optional[str]:
    """Which engine serves the model directory ``path``, from its files
    alone; None when it is not a speech model this worker knows."""
    if not path.is_dir():
        return None
    names = {p.name for p in path.iterdir() if p.is_file()}
    lower = {n.lower() for n in names}
    if ("model.bin" in lower and "config.json" in lower
            and ({"tokenizer.json", "vocabulary.txt", "vocabulary.json"} & lower)):
        return "whisper"
    onnx = [n for n in names if n.lower().endswith(".onnx")]
    if any(f"{n}.json" in names for n in onnx):
        return "piper"
    if onnx and any(n.lower().startswith("voices") and n.lower().endswith(".bin") for n in names):
        return "kokoro"
    if onnx and "config.json" in names and any(n.lower().endswith(".npz") for n in names):
        return "kitten"
    if all((path / "onnx" / f).is_file() for f in SUPERTONIC_FILES) and (path / "voice_styles").is_dir():
        return "supertonic"
    if all(f in names for f in CHATTERBOX_FILES):
        return "chatterbox"
    if {"model.safetensors", "config.json", "tokenizer.json"} <= names and _is_mlx_chatterbox(path / "config.json"):
        return "chatterbox_mlx"
    if "checkpoint.pth" in names and "config.json" in names and _is_tone_converter(path / "config.json"):
        return "openvoice"
    return None


def _is_mlx_chatterbox(config: Path) -> bool:
    """Whether ``config`` is an mlx-audio Chatterbox checkpoint's. Turbo
    (``chatterbox_turbo``, English only) is another model."""
    import json
    try:
        return json.loads(config.read_text(encoding="utf-8")).get("model_type") == "chatterbox"
    except (OSError, ValueError, AttributeError):
        return False


def _is_tone_converter(config: Path) -> bool:
    """Whether ``config`` is an OpenVoice converter's: no speakers, and the
    reference encoder's embedding size."""
    import json
    try:
        cfg = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    data, model = cfg.get("data") or {}, cfg.get("model") or {}
    return (isinstance(data, dict) and isinstance(model, dict) and "sampling_rate" in data
            and int(data.get("n_speakers") or 0) == 0 and "gin_channels" in model)


def _first(path: Path, suffix: str, *, prefix: str = "") -> Optional[Path]:
    found = sorted(p for p in path.iterdir()
                   if p.is_file() and p.name.lower().endswith(suffix) and p.name.lower().startswith(prefix))
    return found[0] if found else None


def short_dir(data_dir: Path, *, copy: bool = False) -> Path:
    """``data_dir`` itself, or a short path to it when espeak-ng would
    refuse the path for its length (see ``_ESPEAK_PATH_MAX``): a symlink,
    or with ``copy`` a copy, for callers that resolve symlinks (phonemizer
    does, which brings the long path back)."""
    if len(str(data_dir)) <= _ESPEAK_PATH_MAX:
        return data_dir
    digest = hashlib.sha1(str(data_dir).encode()).hexdigest()[:10]
    link = Path(tempfile.gettempdir()) / f"ah-espeak-{'copy-' if copy else ''}{digest}"
    try:
        if copy:
            if not link.is_dir():
                import shutil
                staging = Path(tempfile.mkdtemp(prefix=f"{link.name}.", dir=link.parent))
                shutil.copytree(data_dir, staging, dirs_exist_ok=True)
                try:
                    staging.replace(link)
                except OSError:  # another worker got there first
                    shutil.rmtree(staging, ignore_errors=True)
            return link
        if link.is_symlink() and Path(os.readlink(link)) != data_dir:
            link.unlink()
        if not link.exists():
            link.symlink_to(data_dir, target_is_directory=True)
        return link
    except OSError:
        log.warning("could not shorten %s; espeak-ng may not find it", data_dir)
        return data_dir


# ── audio ────────────────────────────────────────────────────────────────────

def wav_bytes(samples: Any, rate: int) -> bytes:
    """16-bit mono WAV from float samples in [-1, 1] or int16 samples."""
    import numpy as np
    arr = np.asarray(samples)
    if arr.dtype != np.int16:
        arr = (np.clip(arr.astype(np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(arr.reshape(-1).tobytes())
    return buf.getvalue()


def encode(wav: bytes, fmt: str) -> Tuple[bytes, str]:
    """The WAV ``wav`` in ``fmt``: wav and pcm as they are, the rest through
    PyAV's encoders. Returns the bytes and their MIME type."""
    fmt = (fmt or "mp3").lower()
    if fmt == "wav":
        return wav, "audio/wav"
    if fmt == "pcm":
        with wave.open(io.BytesIO(wav)) as src:
            return src.readframes(src.getnframes()), "audio/L16"
    if fmt not in _ENCODED:
        raise WorkerError(f"response_format {fmt!r} is not supported; use one of "
                          f"wav, pcm, {', '.join(_ENCODED)}")
    try:
        import av
    except ImportError:
        raise WorkerError(f"{fmt} needs PyAV (pip install av); ask for wav instead") from None
    import numpy as np
    container, codec, mime = _ENCODED[fmt]
    with wave.open(io.BytesIO(wav)) as src:
        rate = src.getframerate()
        pcm = np.frombuffer(src.readframes(src.getnframes()), dtype=np.int16)
    # Opus takes only a handful of rates; 48 kHz is the one every decoder plays.
    out_rate = 48000 if codec == "libopus" else rate
    out = io.BytesIO()
    with av.open(out, "w", format=container) as box:
        stream = box.add_stream(codec, rate=out_rate)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = rate
        resampler = av.AudioResampler(format=stream.format.name, layout="mono", rate=out_rate)
        for chunk in resampler.resample(frame) + resampler.resample(None):
            for packet in stream.encode(chunk):
                box.mux(packet)
        for packet in stream.encode(None):
            box.mux(packet)
    return out.getvalue(), mime


def decode(data: bytes, rate: int = 16000) -> Any:
    """Any audio or video file to mono float32 at ``rate``: 16 kHz is what
    Whisper takes. Done here and not by faster-whisper, whose own decoder
    passes an argument newer PyAV releases no longer accept."""
    import av
    import numpy as np
    chunks: List[Any] = []
    try:
        with av.open(io.BytesIO(data)) as box:
            if not box.streams.audio:
                raise WorkerError("the file has no audio track")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            for frame in box.decode(audio=0):
                chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except WorkerError:
        raise
    except Exception as exc:  # noqa: BLE001 - a file PyAV cannot read
        raise WorkerError(f"cannot read the audio: {exc}") from None
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def prepare_sample(data: bytes) -> Tuple[bytes, float]:
    """A recorded voice the way it is kept: mono WAV at ``SAMPLE_RATE``, the
    silence at both ends cut, its peak brought to about -1 dBFS, at most
    ``SAMPLE_MAX_SECONDS`` long. Returns the WAV and its length in seconds;
    raises WorkerError for a recording with too little speech in it."""
    import numpy as np
    audio = decode(data, SAMPLE_RATE)
    frame = SAMPLE_RATE // 50  # 20 ms
    n = audio.size // frame
    if n == 0:
        raise WorkerError("the recording is empty")
    rms = np.sqrt(np.mean(audio[:n * frame].reshape(n, frame) ** 2, axis=1))
    if float(rms.max()) < 1e-3:
        raise WorkerError("the recording is silent")
    # Speech: frames within about 26 dB of the loudest one.
    voiced = np.flatnonzero(rms > max(float(rms.max()) * 0.05, 1e-3))
    if voiced.size * 0.02 < SAMPLE_MIN_SECONDS * 0.6:
        raise WorkerError(f"the recording holds too little speech; record at least "
                          f"{SAMPLE_MIN_SECONDS:.0f} seconds, 10 to 20 work best")
    start, end = max(0, int(voiced[0]) - 10) * frame, min(n, int(voiced[-1]) + 11) * frame
    audio = audio[start:end][:int(SAMPLE_MAX_SECONDS * SAMPLE_RATE)]
    if audio.size < SAMPLE_MIN_SECONDS * SAMPLE_RATE:
        raise WorkerError(f"the recording is shorter than {SAMPLE_MIN_SECONDS:.0f} seconds; "
                          "10 to 20 seconds of speech work best")
    audio = audio * (0.89 / max(float(np.abs(audio).max()), 1e-6))
    return wav_bytes(audio, SAMPLE_RATE), round(audio.size / SAMPLE_RATE, 2)


#: How much of a sample Chatterbox's decoder takes (its DEC_COND_LEN; the
#: first 6 s of it also prompt its text model): the part picked for it.
REFERENCE_SECONDS = 10.0
_REF_FRAME = 0.02
_REF_STEP = 0.25


def reference_start(audio: Any, rate: int, seconds: float = REFERENCE_SECONDS) -> float:
    """Where in a sample the best ``seconds`` for Chatterbox begin. It hears
    the beginning of what it is given, so a longer recording that opens
    hesitantly, far from the microphone or with a cough would teach it
    those. Every start a quarter second apart, moved back to the quietest
    moment within 0.3 s so no word is cut, is scored by how much of the
    window is speech, how loud the speech is against the loudest window
    (farther from the microphone means quieter and more room), the longest
    pause past 0.6 s and frames 12 dB above the speech (coughs, clicks,
    bumps). 0.0 for a sample not much longer than ``seconds``."""
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = int(rate * _REF_FRAME)
    count = audio.size // n
    window = int(seconds / _REF_FRAME)
    if count * _REF_FRAME <= seconds + 0.5:
        return 0.0
    db = 20 * np.log10(np.maximum(np.sqrt(np.mean(audio[:count * n].reshape(count, n) ** 2, axis=1)), 1e-6))
    voiced = db > np.percentile(db, 99) - 30
    back = int(0.3 / _REF_FRAME)
    rows = []
    for at in range(0, count - window + 1, int(_REF_STEP / _REF_FRAME)):
        lo = max(0, at - back)
        start = lo + int(np.argmin(db[lo:at + 1]))
        seg, v = db[start:start + window], voiced[start:start + window]
        if seg.size < window:
            continue
        level = float(np.median(seg[v])) if v.any() else -120.0
        # The longest run of frames without speech.
        edges = np.flatnonzero(np.diff(np.concatenate(([1], v.astype(np.int8), [1]))))
        pause = float(np.max(edges[1::2] - edges[::2])) * _REF_FRAME if edges.size else 0.0
        rows.append((start, float(v.mean()), level, pause, float(np.mean(seg > level + 12))))
    if not rows:
        return 0.0
    loudest = max(r[2] for r in rows)
    # Between windows about as good, the earlier one (0.002 a second).
    best = max(rows, key=lambda r: r[1] - 0.02 * (loudest - r[2]) - 0.15 * max(0.0, r[3] - 0.6) - 2.0 * r[4]
               - 0.002 * r[0] * _REF_FRAME)
    return round(best[0] * _REF_FRAME, 2)


def read_wav(data: bytes) -> Tuple[Any, int]:
    """A 16-bit WAV (a kept sample) as mono float32 and its rate, without
    PyAV."""
    import numpy as np
    with wave.open(io.BytesIO(data)) as src:
        rate, width, channels = src.getframerate(), src.getsampwidth(), src.getnchannels()
        raw = src.readframes(src.getnframes())
    if width != 2:
        raise WorkerError("the sample is not 16-bit PCM")
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return (pcm.reshape(-1, channels).mean(axis=1) if channels > 1 else pcm), rate


def reference_window(data: bytes) -> Tuple[float, float]:
    """The seconds of a kept sample Chatterbox listens to closely."""
    audio, rate = read_wav(data)
    start = reference_start(audio, rate)
    return start, round(min(audio.size / rate, start + REFERENCE_SECONDS), 2)


def voices_root() -> Optional[Path]:
    """Where the recorded voices live: ``MODELS_VOICES_DIR``, set by the
    runtime; None when it is not set."""
    root = (os.environ.get("MODELS_VOICES_DIR") or "").strip()
    return Path(root) if root else None


def recorded_voices(root: Optional[Path] = None) -> List[str]:
    """The names of the voices recorded so far, the ones with a sample."""
    root = root or voices_root()
    if root is None or not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "sample.wav").is_file())


def voice_meta(name: Optional[str]) -> Dict[str, Any]:
    """What ``voice.json`` says of a recorded voice (its language, above
    all); empty for any other name."""
    import json
    root = voices_root()
    if not name or root is None or name.startswith(".") or "/" in name:
        return {}
    try:
        data = json.loads((root / name / "voice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def voice_files(name: str, key: str) -> Tuple[Path, Path]:
    """A recorded voice's sample and the cache file ``key`` of an engine
    keeps what it computed from it in."""
    root = voices_root()
    if root is None or name not in recorded_voices(root):
        raise WorkerError(f"no recorded voice {name!r}; record one on the Models page", 404)
    return root / name / "sample.wav", root / name / "cache" / key


def fresh(cache: Path, sample: Path) -> bool:
    """Whether ``cache`` was made from the sample as it is now."""
    try:
        return cache.stat().st_mtime >= sample.stat().st_mtime
    except OSError:
        return False


def torch_device() -> str:
    """Where a torch engine runs: ``MODELS_TTS_DEVICE`` when set, else a
    CUDA GPU, else Apple's GPU (MPS), else the CPU."""
    import torch
    want = (os.environ.get("MODELS_TTS_DEVICE") or "auto").strip().lower()
    if want != "auto":
        return want
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# ── engines ──────────────────────────────────────────────────────────────────

class Engine:
    kind = "speech"
    name = ""

    def voices(self) -> List[str]:
        return []

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        raise WorkerError(f"{self.name} models do not read text aloud; this one transcribes", 400)

    def transcribe(self, audio: bytes, language: Optional[str], prompt: Optional[str]) -> Dict[str, Any]:
        raise WorkerError(f"{self.name} models read text aloud; they do not transcribe", 400)

    def convert(self, audio: bytes, voice: Optional[str], source: str, tau: float) -> bytes:
        raise WorkerError(f"{self.name} models do not change the voice of speech", 400)


class Whisper(Engine):
    kind = "transcription"
    name = "whisper"

    def __init__(self, path: Path, threads: int = 0) -> None:
        from faster_whisper import WhisperModel
        device = os.environ.get("MODELS_WHISPER_DEVICE") or "auto"
        compute = os.environ.get("MODELS_WHISPER_COMPUTE") or "auto"
        self.model = WhisperModel(str(path), device=device, compute_type=compute,
                                  cpu_threads=threads or 0)
        self._lock = threading.Lock()

    def transcribe(self, audio: bytes, language: Optional[str], prompt: Optional[str]) -> Dict[str, Any]:
        samples = decode(audio)
        if len(samples) == 0:
            return {"text": "", "language": language or "", "duration": 0.0, "segments": []}
        with self._lock:  # one decode at a time: CPU-bound, and it keeps memory flat
            segments, info = self.model.transcribe(samples, language=language or None,
                                                   initial_prompt=prompt or None)
            segs = [{"id": i, "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
                    for i, s in enumerate(segments)]
        return {"text": "".join(s["text"] for s in segs).strip(), "language": info.language,
                "duration": round(float(info.duration), 2), "segments": segs}


class Piper(Engine):
    name = "piper"

    def __init__(self, path: Path, threads: int = 0) -> None:
        import piper
        from piper import PiperVoice
        model = next(p for p in sorted(path.glob("*.onnx")) if p.with_name(p.name + ".json").is_file())
        data = Path(piper.__file__).resolve().parent / "espeak-ng-data"
        kwargs: Dict[str, Any] = {}
        if data.is_dir():
            kwargs["espeak_data_dir"] = short_dir(data)
        self.voice = PiperVoice.load(str(model), config_path=str(model) + ".json", **kwargs)
        self.speakers: Dict[str, int] = dict(getattr(self.voice.config, "speaker_id_map", None) or {})
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return sorted(self.speakers)

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        from piper import SynthesisConfig
        # A name this voice does not know (the hub's default "alloy") means
        # its own single or first speaker, not an error.
        speaker = self.speakers.get(voice or "") if self.speakers else None
        config = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / max(0.25, min(4.0, speed)))
        buf = io.BytesIO()
        with self._lock, wave.open(buf, "wb") as out:
            self.voice.synthesize_wav(text, out, syn_config=config)
        return buf.getvalue()


class Kokoro(Engine):
    name = "kokoro"

    def __init__(self, path: Path, threads: int = 0) -> None:
        from kokoro_onnx import Kokoro as KokoroModel
        model = _first(path, ".onnx")
        voices = _first(path, ".bin", prefix="voices")
        kwargs: Dict[str, Any] = {}
        try:
            import espeakng_loader
            from kokoro_onnx import EspeakConfig
            kwargs["espeak_config"] = EspeakConfig(
                lib_path=espeakng_loader.get_library_path(),
                data_path=str(short_dir(Path(espeakng_loader.get_data_path()), copy=True)))
        except Exception:  # noqa: BLE001 - kokoro-onnx finds espeak itself when this fails
            log.debug("espeak config left to kokoro-onnx", exc_info=True)
        self.model = KokoroModel(str(model), str(voices), **kwargs)
        self._voices = sorted(self.model.get_voices())
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return list(self._voices)

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        name = voice if voice in self._voices else ("af_heart" if "af_heart" in self._voices else self._voices[0])
        lang = _KOKORO_LANG.get(name[:1], "en-us")
        with self._lock:
            samples, rate = self.model.create(text, voice=name, speed=max(0.5, min(2.0, speed)), lang=lang)
        return wav_bytes(samples, rate)


def espeak_wrapper() -> None:
    """Point phonemizer at the espeak-ng library and data espeakng-loader
    ships, so no system espeak is needed; left to phonemizer when that
    package is missing."""
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(str(short_dir(Path(espeakng_loader.get_data_path()), copy=True)))
    except Exception:  # noqa: BLE001 - a system espeak-ng still works
        log.debug("espeak left to phonemizer", exc_info=True)


def chunk_sentences(text: str, max_len: int = 400) -> List[str]:
    """Kitten's own split: sentences, a long one cut at words, each ending
    in punctuation (a comma when it had none), since the model reads a few
    hundred characters at a time."""
    import re
    chunks: List[str] = []

    def add(piece: str) -> None:
        piece = piece.strip()
        if piece:
            chunks.append(piece if piece[-1] in ".!?,;:" else piece + ",")

    for sentence in re.split(r"[.!?]+", text):
        current = ""
        for word in sentence.split():
            if current and len(current) + len(word) + 1 > max_len:
                add(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        add(current)
    return chunks


class Kitten(Engine):
    """Kitten TTS (StyleTTS 2 in ONNX). ``config.json`` says which version:
    ONNX1 (0.1) takes one style per voice and pads the audio on both ends,
    ONNX2 (0.8) a style per text length and a speed prior per voice."""

    name = "kitten"
    #: Kitten's symbol table: pad, punctuation, Latin letters, IPA.
    SYMBOLS = (["$"] + list(';:,.!?¡¿—…"«»"" ')
               + list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
               + list("ɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɝɞɟʄɡɠɢʛɦɧħɥʜɨɪʝɭɬɫɮʟɱɯɰŋɳɲɴøɵɸθœɶʘɹɺɾɻʀʁɽʂʃʈʧʉʊʋⱱʌɣɤʍχʎʏʑʐʒʔʡʕʢǀǁǂǃˈˌːˑʼʴʰʱʲʷˠˤ˞↓↑→↗↘'̩'ᵻ"))
    RATE = 24000

    def __init__(self, path: Path, threads: int = 0) -> None:
        import json
        import numpy as np
        import onnxruntime as ort
        from phonemizer.backend import EspeakBackend
        config = json.loads((path / "config.json").read_text(encoding="utf-8"))
        model = path / str(config.get("model_file") or "")
        if not model.is_file():
            model = _first(path, ".onnx")
        voices = path / str(config.get("voices") or "")
        if not voices.is_file():
            voices = _first(path, ".npz")
        self.v1 = config.get("type") == "ONNX1"
        self.priors: Dict[str, float] = dict(config.get("speed_priors") or {})
        self.aliases: Dict[str, str] = dict(config.get("voice_aliases") or {})
        self.styles = dict(np.load(voices))
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(model), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        espeak_wrapper()
        self.phonemizer = EspeakBackend(language="en-us", preserve_punctuation=True, with_stress=True)
        self.index = {c: i for i, c in enumerate(self.SYMBOLS)}
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return list(self.aliases) or sorted(self.styles)

    def _voice(self, voice: Optional[str]) -> str:
        """The style key for ``voice``: an alias (Bella), a key
        (expr-voice-2-f), else the first voice, for a name this model does
        not know (the hub's default "alloy")."""
        if voice in self.aliases:
            return self.aliases[voice]
        if voice in self.styles:
            return voice
        first = next(iter(self.aliases.values()), None)
        return first if first in self.styles else sorted(self.styles)[0]

    def _chunk(self, text: str, key: str, speed: float) -> Any:
        import re
        import numpy as np
        phonemes = self.phonemizer.phonemize([text])[0]
        phonemes = " ".join(re.findall(r"\w+|[^\w\s]", phonemes))
        tokens = [0] + [self.index[c] for c in phonemes if c in self.index] + ([0] if self.v1 else [10, 0])
        style = self.styles[key]
        if style.ndim > 1 and style.shape[0] > 1:
            ref = min(len(text), style.shape[0] - 1)
            style = style[ref:ref + 1]
        audio = self.session.run(None, {"input_ids": np.array([tokens], dtype=np.int64),
                                        "style": style.reshape(1, -1).astype(np.float32),
                                        "speed": np.array([speed], dtype=np.float32)})[0]
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        audio = audio[5000:-10000] if self.v1 else audio[:-5000]
        # 0.8 opens each chunk with most of a second of silence: cut both
        # ends to a short margin, so sentences follow at a natural pace.
        loud = np.flatnonzero(np.abs(audio) > 0.01)
        if loud.size == 0:
            return audio[:0]
        margin = self.RATE // 20
        return audio[max(0, loud[0] - margin):loud[-1] + margin]

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        import numpy as np
        key = self._voice(voice)
        speed = max(0.5, min(2.0, speed)) * float(self.priors.get(key, 1.0))
        with self._lock:
            parts = [a for chunk in chunk_sentences(text) if (a := self._chunk(chunk, key, speed)).size]
        if not parts:
            raise WorkerError("nothing in the input can be read aloud")
        pause = np.zeros(self.RATE // 4, dtype=np.float32)
        joined = [x for a in parts for x in (a, pause)][:-1]
        return wav_bytes(np.concatenate(joined), self.RATE)


#: Script ranges -> the Supertonic language code for text written in them.
#: Han characters alone are left out: Chinese is not among its languages,
#: and Japanese text shows its kana soon enough.
_SCRIPTS = (("\u0400", "\u04ff", "ru"), ("\uac00", "\ud7af", "ko"), ("\u1100", "\u11ff", "ko"),
            ("\u3040", "\u30ff", "ja"), ("\u0600", "\u06ff", "ar"), ("\u0900", "\u097f", "hi"),
            ("\u0370", "\u03ff", "el"))
#: Cyrillic letters Ukrainian has and Russian does not.
_UK_LETTERS = set("іїєґІЇЄҐ")


def guess_language(text: str) -> Optional[str]:
    """The language of ``text`` from its script, for a model that needs to
    be told: the first script found decides (Cyrillic is Russian unless a
    letter only Ukrainian has shows up). None for Latin text, which
    Supertonic 3 reads well in any of its languages with its "na" token."""
    for ch in text:
        for lo, hi, lang in _SCRIPTS:
            if lo <= ch <= hi:
                return "uk" if lang == "ru" and _UK_LETTERS & set(text) else lang
    return None


class Supertonic(Engine):
    """Supertonic 1, 2 or 3 through the supertonic package; which one from
    ``config.json`` (the Hub's stub names it), else from the repo the
    download marker records."""

    name = "supertonic"

    def __init__(self, path: Path, threads: int = 0) -> None:
        import json
        from supertonic import TTS
        label = ""
        for meta, key in (("config.json", "model_name"), (".hub-model.json", "repo")):
            try:
                label = str(json.loads((path / meta).read_text(encoding="utf-8")).get(key) or "")
            except (OSError, ValueError):
                continue
            if label:
                break
        digits = "".join(c for c in label.rsplit("/", 1)[-1] if c.isdigit())
        self.version = int(digits) if digits in ("2", "3") else (1 if label else 3)
        model = {1: "supertonic", 2: "supertonic-2", 3: "supertonic-3"}[self.version]
        self.tts = TTS(model=model, model_dir=path, auto_download=False,
                       intra_op_num_threads=threads or None)
        self._voices = sorted(self.tts.voice_style_names)
        self._styles: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return list(self._voices)

    def _language(self, text: str) -> Optional[str]:
        if self.version == 1:
            return None
        lang = guess_language(text)
        if self.version == 2:
            # No "na" token here: Latin text is read as English.
            return lang if lang in _SUPERTONIC2_LANGS else "en"
        return lang or "na"

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        name = voice if voice in self._voices else ("F1" if "F1" in self._voices else self._voices[0])
        processor = self.tts.model.text_processor
        _, unsupported = processor.validate_text(text)
        # A character the model has no symbol for (an emoji, a rare sign)
        # is dropped, not an error for the whole text.
        text = "".join(c for c in text if c not in set(unsupported)).strip()
        if not text:
            raise WorkerError("nothing in the input can be read aloud")
        with self._lock:
            if name not in self._styles:
                self._styles[name] = self.tts.get_voice_style(name)
            samples, _ = self.tts.synthesize(text, voice_style=self._styles[name],
                                             speed=max(0.7, min(2.0, speed * 1.05)),
                                             lang=self._language(text))
        return wav_bytes(samples, self.tts.sample_rate)


#: Common words of the Latin-script languages Chatterbox reads, to tell
#: them apart; a letter only one of them uses counts as well.
_LATIN_WORDS = {
    "en": "the and is are of to you this that with for not have it was what",
    "de": "der die das und ist nicht ich sie mit ein eine zu den auf für sich auch",
    "fr": "le la les et est un une des pas je vous que dans pour avec qui sur",
    "es": "el los las y es una que por para con como pero muy está",
    "it": "il gli e è che di non per con sono una della questo",
    "pt": "os as é um uma que não para com você está muito",
    "nl": "het een en is niet ik je dat van met op voor zijn",
    "pl": "w nie się na jest to że z do jak tak ale",
    "sv": "och är att det som på för med inte jag",
    "tr": "ve bir bu için ile değil ben çok",
}
_LATIN_LETTERS = {"ß": "de", "ñ": "es", "¿": "es", "ł": "pl", "ą": "pl", "ę": "pl", "ś": "pl", "ż": "pl",
                  "ğ": "tr", "ş": "tr", "ı": "tr", "ã": "pt", "õ": "pt", "å": "sv"}
_LATIN_SETS = {lang: set(words.split()) for lang, words in _LATIN_WORDS.items()}


def chatterbox_language(text: str, fallback: Optional[str] = None) -> str:
    """The language Chatterbox is told ``text`` is in: from its script, and
    for Latin text from its common words; else ``fallback`` (the recorded
    voice's language), else English."""
    import re
    lang = guess_language(text)
    if lang == "uk":
        lang = "ru"  # not among its languages; Russian reads it closest
    if lang is None:
        if any("֐" <= ch <= "׿" for ch in text):
            lang = "he"
        elif any("一" <= ch <= "鿿" for ch in text):
            lang = "zh"
    if lang in CHATTERBOX_LANGS:
        return lang
    lower = text.lower()
    words = re.findall(r"[^\W\d_]+", lower)
    scores = {code: sum(w in vocab for w in words) for code, vocab in _LATIN_SETS.items()}
    for ch, code in _LATIN_LETTERS.items():
        if ch in lower:
            scores[code] += 2
    best = max(scores, key=lambda code: scores[code])
    if scores[best] > 0:
        return best
    fallback = str(fallback or "").lower()[:2]
    return fallback if fallback in CHATTERBOX_LANGS else "en"


def sentence_chunks(text: str, max_len: int) -> List[str]:
    """``text`` in pieces of whole sentences, each up to ``max_len``
    characters, with their own punctuation (a question stays a question);
    a sentence longer than that is cut at a word."""
    import re
    pieces: List[str] = []
    for sentence in re.split(r"(?<=[.!?…。！？])\s+", " ".join(text.split())):
        while len(sentence) > max_len:
            cut = sentence.rfind(" ", 0, max_len)
            cut = cut if cut > 0 else max_len
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    chunks: List[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_len:
            chunks[-1] += " " + piece
        else:
            chunks.append(piece)
    return chunks


def _number(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


class ChatterboxBase(Engine):
    """What both Chatterbox engines share: the voices (the recorded ones and
    the model's own ``default``), their conditionals (a voice encoder
    embedding, speech tokens of the sample and a reference for the decoder,
    computed once per voice and kept in its cache), the language and the
    reading in pieces. A subclass loads the model and says how to prepare,
    keep and read with them."""

    #: Characters per generation: its 1000 speech tokens hold about 40 s,
    #: and a shorter piece keeps it from losing its place.
    CHUNK = 250
    #: Output rate (S3Gen's).
    rate = 24000
    #: False for an English-only checkpoint, which takes no language.
    multilingual = True

    def __init__(self, path: Path) -> None:
        # "best10": made from the best 10 s; a cache made from the first 10 s
        # has the name without it and is no longer read.
        self.key = f"{self.name}-{path.name}-best10"
        self.default: Any = None
        self._conds: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return (["default"] if self.default is not None else []) + recorded_voices()

    # The engine's own part.
    def _prepare(self, sample: Path) -> Any:
        raise NotImplementedError

    def _load_cached(self, cache: Path) -> Any:
        raise NotImplementedError

    def _save_cached(self, conds: Any, cache: Path) -> None:
        raise NotImplementedError

    def _generate(self, text: str, conds: Any, lang: str, exaggeration: float, cfg_weight: float) -> Any:
        raise NotImplementedError

    def _prepare_from_best(self, sample: Path) -> Any:
        """Conditionals from the sample turned around to begin at its best
        10 s (:func:`reference_start`): the decoder and the text model's
        prompt take those, the speaker embedding still hears all of it, and
        the one seam sits where the sample ends, not inside the window."""
        import numpy as np
        try:
            audio, rate = read_wav(sample.read_bytes())
        except (WorkerError, wave.Error, EOFError):
            return self._prepare(sample)
        start = int(reference_start(audio, rate) * rate)
        if start <= 0:
            return self._prepare(sample)
        turned = sample.with_name(f".reference-{self.name}.wav")
        turned.write_bytes(wav_bytes(np.concatenate([audio[start:], audio[:start]]), rate))
        try:
            return self._prepare(turned)
        finally:
            turned.unlink(missing_ok=True)

    def _conditionals(self, voice: Optional[str]) -> Any:
        recorded = recorded_voices()
        if voice not in recorded:
            # A name it does not know (the hub's default "alloy") reads in
            # the model's own voice, or the first recorded one.
            if self.default is not None:
                return self.default
            if not recorded:
                raise WorkerError("no voice yet: record one on the Models page")
            voice = recorded[0]
        sample, cache = voice_files(str(voice), self.key)
        stamp = sample.stat().st_mtime
        held = self._conds.get(str(voice))
        if held is not None and held[0] == stamp:
            return held[1]
        conds = None
        if fresh(cache, sample):
            try:
                conds = self._load_cached(cache)
            except Exception:  # noqa: BLE001 - a cache from another version is made again
                log.warning("the cached conditionals of %s do not load; preparing them again", voice)
        if conds is None:
            conds = self._prepare_from_best(sample)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                self._save_cached(conds, cache)
            except OSError:
                log.warning("could not keep the conditionals of %s", voice, exc_info=True)
        self._conds[str(voice)] = (stamp, conds)
        return conds

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        import numpy as np
        options = options or {}
        spoken = str(voice_meta(voice).get("language") or "")
        lang = chatterbox_language(text, spoken) if self.multilingual else "en"
        # Resemble's advice: a sample in another language than the text
        # carries its accent over unless guidance is off.
        cfg_default = 0.0 if spoken and spoken[:2] != lang else 0.5
        exaggeration = _number(options.get("exaggeration"), 0.25, 2.0, 0.5)
        cfg_weight = _number(options.get("cfg_weight"), 0.0, 1.0, cfg_default)
        parts: List[Any] = []
        with self._lock:
            conds = self._conditionals(voice)
            for chunk in sentence_chunks(text, self.CHUNK):
                parts.append(np.asarray(self._generate(chunk, conds, lang, exaggeration, cfg_weight),
                                        dtype=np.float32).reshape(-1))
        parts = [a for a in parts if a.size]
        if not parts:
            raise WorkerError("nothing in the input can be read aloud")
        pause = np.zeros(int(self.rate * 0.2), dtype=np.float32)
        joined = [x for a in parts for x in (a, pause)][:-1]
        return wav_bytes(np.concatenate(joined), self.rate)


class Chatterbox(ChatterboxBase):
    """Chatterbox Multilingual on torch (CUDA, Apple's MPS or the CPU).
    Autoregressive, so slower than the ONNX engines: on a Mac's GPU about
    four times slower than the speech lasts, on a CUDA GPU faster than it.
    On Apple silicon :class:`ChatterboxMLX` runs the same model ten times
    as fast."""

    name = "chatterbox"

    def __init__(self, path: Path, threads: int = 0) -> None:
        super().__init__(path)
        # Its sampler draws a progress bar per sentence into the log.
        os.environ.setdefault("TQDM_DISABLE", "1")
        import torch
        if threads:
            torch.set_num_threads(threads)
        from chatterbox.models.tokenizers import tokenizer as tokenizer_module
        fetch = tokenizer_module.hf_hub_download

        def local_first(repo_id: str, filename: str, **kwargs: Any) -> str:
            # Cangjie's table for Chinese, beside the weights when it was
            # downloaded with them; fetched only when it was not.
            here = path / filename
            return str(here) if here.is_file() else fetch(repo_id=repo_id, filename=filename, **kwargs)

        tokenizer_module.hf_hub_download = local_first
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        self.device = torch_device()
        self.tts = ChatterboxMultilingualTTS.from_local(path, self.device)
        self.default = self.tts.conds
        self.rate = self.tts.sr
        self.key += ".pt"

    def _prepare(self, sample: Path) -> Any:
        self.tts.prepare_conditionals(str(sample), exaggeration=0.5)
        return self.tts.conds

    def _load_cached(self, cache: Path) -> Any:
        from chatterbox.mtl_tts import Conditionals
        return Conditionals.load(cache, map_location="cpu").to(self.device)

    def _save_cached(self, conds: Any, cache: Path) -> None:
        conds.save(cache)

    def _generate(self, text: str, conds: Any, lang: str, exaggeration: float, cfg_weight: float) -> Any:
        self.tts.conds = conds
        wav = self.tts.generate(text, language_id=lang, exaggeration=exaggeration, cfg_weight=cfg_weight)
        return wav.squeeze(0).cpu().numpy()


class ChatterboxMLX(ChatterboxBase):
    """The same Chatterbox on Apple's MLX (mlx-audio's port, a
    mlx-community/chatterbox-* checkpoint): on an M-series Mac about twice
    as fast as the speech lasts, in 4 or 8 bit as close to the recording
    as the original. The port adds no watermark.

    The port fetches its speech tokenizer from mlx-community/S3TokenizerV2
    when it loads; the runtime downloads it into ``s3tokenizer/`` beside
    the weights, and that copy is used. MLX keeps a stream per thread, so
    loading and every generation run on one thread of its own."""

    name = "chatterbox_mlx"

    def __init__(self, path: Path, threads: int = 0) -> None:
        super().__init__(path)
        from concurrent.futures import ThreadPoolExecutor
        self.key += ".safetensors"
        self._mlx = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
        self.model = self._mlx.submit(self._load, path).result()
        self.default = getattr(self.model, "_conds", None)
        self.multilingual = bool(getattr(getattr(self.model, "config", None), "multilingual", True))
        self.rate = int(getattr(self.model, "sample_rate", 24000))

    @staticmethod
    def _load(path: Path) -> Any:
        import huggingface_hub
        fetch = huggingface_hub.snapshot_download
        local = path / "s3tokenizer"

        def local_first(repo_id: str, *args: Any, **kwargs: Any) -> str:
            if repo_id.endswith("/S3TokenizerV2") and (local / "model.safetensors").is_file():
                return str(local)
            return fetch(repo_id, *args, **kwargs)

        huggingface_hub.snapshot_download = local_first
        try:
            from mlx_audio.tts.utils import load_model
            return load_model(path)
        finally:
            huggingface_hub.snapshot_download = fetch

    def _on_mlx(self, fn: Any, *args: Any) -> Any:
        return self._mlx.submit(fn, *args).result()

    def _prepare(self, sample: Path) -> Any:
        return self._on_mlx(self.model.prepare_conditionals, str(sample), self.rate, 0.5)

    def _load_cached(self, cache: Path) -> Any:
        def load() -> Any:
            import mlx.core as mx
            from mlx_audio.tts.models.chatterbox.chatterbox import Conditionals, T3Cond
            data = mx.load(str(cache))
            t3 = T3Cond(speaker_emb=data["t3.speaker_emb"],
                        cond_prompt_speech_tokens=data.get("t3.cond_prompt_speech_tokens"),
                        emotion_adv=data["t3.emotion_adv"])
            return Conditionals(t3, {k[4:]: v for k, v in data.items() if k.startswith("gen.")})
        return self._on_mlx(load)

    def _save_cached(self, conds: Any, cache: Path) -> None:
        def save() -> None:
            import mlx.core as mx
            arrays = {f"t3.{k}": getattr(conds.t3, k) for k in ("speaker_emb", "cond_prompt_speech_tokens",
                                                               "emotion_adv")}
            arrays.update({f"gen.{k}": v for k, v in conds.gen.items()})
            arrays = {k: v for k, v in arrays.items() if isinstance(v, mx.array)}
            # The same layout as a checkpoint's conds.safetensors.
            mx.save_safetensors(str(cache), arrays)
        self._on_mlx(save)

    def _generate(self, text: str, conds: Any, lang: str, exaggeration: float, cfg_weight: float) -> Any:
        import numpy as np

        def run() -> Any:
            out = [np.array(r.audio) for r in self.model.generate(
                text, conds=conds, lang_code=lang, exaggeration=exaggeration, cfg_weight=cfg_weight,
                verbose=False)]
            return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
        return self._on_mlx(run)


class OpenVoice(Engine):
    """OpenVoice's tone color converter (openvoice_vc.py): speech another
    model read goes in, the same speech in a recorded voice's timbre comes
    out. It compares the timbre of the two: the recorded voice's from its
    sample (once per voice, kept in its cache), the reading voice's from
    what it read so far, refined with every request up to
    ``SOURCE_SECONDS``, under the ``source`` key the runtime sends (the
    reading model and voice)."""

    name = "openvoice"
    #: The sample is heard in windows this long, their timbres averaged.
    WINDOW = 4.0
    SOURCE_SECONDS = 120.0

    def __init__(self, path: Path, threads: int = 0) -> None:
        import torch
        if threads:
            torch.set_num_threads(threads)
        from openvoice_vc import ToneColorConverter
        # Small enough that the CPU is the quickest place for it but on CUDA.
        device = torch_device()
        self.vc = ToneColorConverter(path, device if device == "cuda" else "cpu")
        self.key = f"openvoice-{path.name}.pt"
        root = voices_root()
        self.sources_dir = root / ".sources" / path.name if root is not None else None
        self._targets: Dict[str, Tuple[float, Any]] = {}
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def voices(self) -> List[str]:
        return recorded_voices()

    def speak(self, text: str, voice: Optional[str], speed: float,
              options: Optional[Dict[str, Any]] = None) -> bytes:
        raise WorkerError("OpenVoice does not read text: it changes the voice of speech another model "
                          "read. Ask the runtime's /v1/audio/speech, which pairs the two.", 400)

    def _target(self, voice: Optional[str]) -> Any:
        import torch
        recorded = recorded_voices()
        if not recorded:
            raise WorkerError("no voice yet: record one on the Models page")
        name = str(voice) if voice in recorded else recorded[0]
        sample, cache = voice_files(name, self.key)
        stamp = sample.stat().st_mtime
        held = self._targets.get(name)
        if held is not None and held[0] == stamp:
            return held[1]
        if fresh(cache, sample):
            se = torch.load(cache, map_location="cpu", weights_only=True)
        else:
            audio = decode(sample.read_bytes(), self.vc.rate)
            win = int(self.WINDOW * self.vc.rate)
            pieces = [audio[i:i + win] for i in range(0, audio.size, win)]
            pieces = [p for p in pieces if p.size >= self.vc.rate] or [audio]
            se = torch.stack([self.vc.embed(p).cpu() for p in pieces]).mean(0)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                torch.save(se, cache)
            except OSError:
                log.warning("could not keep the tone color of %s", name, exc_info=True)
        self._targets[name] = (stamp, se)
        return se

    def _source(self, key: str, audio: Any) -> Any:
        import torch
        entry = self._sources.get(key)
        file = (self.sources_dir / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.pt"
                if self.sources_dir is not None else None)
        if entry is None and file is not None and file.is_file():
            try:
                entry = torch.load(file, map_location="cpu", weights_only=True)
            except Exception:  # noqa: BLE001 - a broken file is learned again
                entry = None
        seconds = audio.size / self.vc.rate
        if entry is None or float(entry["seconds"]) < self.SOURCE_SECONDS:
            se = self.vc.embed(audio).cpu()
            entry = ({"sum": se * seconds, "seconds": seconds} if entry is None
                     else {"sum": entry["sum"] + se * seconds, "seconds": float(entry["seconds"]) + seconds})
            self._sources[key] = entry
            if file is not None:
                try:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    torch.save(entry, file)
                except OSError:
                    log.debug("could not keep the source timbre %s", key, exc_info=True)
        return entry["sum"] / float(entry["seconds"])

    def convert(self, audio: bytes, voice: Optional[str], source: str, tau: float) -> bytes:
        samples = decode(audio, self.vc.rate)
        if samples.size < self.vc.rate // 10:
            raise WorkerError("the speech to convert is empty")
        with self._lock:
            target = self._target(voice)
            src = self._source(source or "unknown", samples)
            out = self.vc.convert(samples, src, target, tau=max(0.0, min(1.0, tau)))
        return wav_bytes(out, self.vc.rate)


ENGINES = {"whisper": Whisper, "piper": Piper, "kokoro": Kokoro, "kitten": Kitten, "supertonic": Supertonic,
           "chatterbox": Chatterbox, "chatterbox_mlx": ChatterboxMLX, "openvoice": OpenVoice}


def load_engine(engine: str, path: Path, threads: int = 0) -> Engine:
    cls = ENGINES.get(engine)
    if cls is None:
        raise SystemExit(f"unknown engine {engine!r}; one of {', '.join(ENGINES)}")
    return cls(path, threads)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def build_app(engine: Engine, model_name: str) -> FastAPI:
    app = FastAPI(title=f"speech worker: {model_name}")

    def error(message: str, status: int = 400) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {
            "message": message, "type": "invalid_request_error"}})

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"ok": True, "engine": engine.name, "kind": engine.kind, "model": model_name}

    @app.get("/voices")
    async def voices() -> Dict[str, Any]:
        return {"voices": engine.voices()}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return error("body is not JSON")
        text = str((body or {}).get("input") or "").strip()
        if not text:
            return error("input is empty")
        try:
            speed = float(body.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        options = {k: body[k] for k in ("exaggeration", "cfg_weight") if k in body}
        try:
            wav = await run_in_threadpool(engine.speak, text, body.get("voice"), speed, options)
            data, mime = encode(wav, str(body.get("response_format") or "mp3"))
        except WorkerError as exc:
            return error(str(exc), exc.status)
        return Response(content=data, media_type=mime)

    @app.post("/v1/audio/convert")
    async def convert(request: Request) -> Response:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return error("file is missing")
        audio = await upload.read()
        try:
            tau = float(form.get("tau") or 0.3)
        except (TypeError, ValueError):
            tau = 0.3
        try:
            wav = await run_in_threadpool(engine.convert, audio, str(form.get("voice") or "") or None,
                                          str(form.get("source") or ""), tau)
            data, mime = encode(wav, str(form.get("response_format") or "mp3"))
        except WorkerError as exc:
            return error(str(exc), exc.status)
        return Response(content=data, media_type=mime)

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> Response:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return error("file is missing")
        audio = await upload.read()
        fmt = str(form.get("response_format") or "json")
        try:
            result = await run_in_threadpool(engine.transcribe, audio, str(form.get("language") or "") or None,
                                             str(form.get("prompt") or "") or None)
        except WorkerError as exc:
            return error(str(exc), exc.status)
        if fmt == "text":
            return PlainTextResponse(result["text"])
        if fmt == "verbose_json":
            return JSONResponse({"task": "transcribe", **result})
        return JSONResponse({"text": result["text"]})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", required=True, choices=sorted(ENGINES))
    parser.add_argument("--model", required=True, help="the model directory")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--name", default="")
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("MODELS_LOG_LEVEL", "INFO"))
    path = Path(args.model)
    engine = load_engine(args.engine, path, args.threads)
    log.info("%s loaded from %s", args.engine, path)
    import uvicorn
    uvicorn.run(build_app(engine, args.name or path.name), host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()

"""
A recorded voice cleaned before the cloning engines learn from it.

A sample taken with a laptop's microphones carries the room: its echo
(reverberation) and its hum. Chatterbox and OpenVoice copy whatever the
sample sounds like, so the room comes back in every line they read. The
runtime (app.py next to this file) runs this script once per cleanup, in the
environment of the engine that does it, and keeps the original beside the
result so the person can compare and go back. The engines:

deepfilternet
    DeepFilterNet 3 on Apple's MLX through mlx-audio's port, over
    ``config.json`` and ``model.safetensors`` from
    mlx-community/DeepFilterNet-mlx (``v3/``). Noise only, and some of a
    short echo; the voice itself stays as it was (speaker similarity to the
    original about 0.98). About a second for 15 s, Apple silicon only.
resemble_enhance
    Resemble Enhance (MIT, ResembleAI/resemble-enhance, ``enhancer_stage2/``).
    ``denoise``: its denoiser alone, for machines without MLX. ``restore``:
    the denoiser and then the enhancer, a flow matching model that rebuilds
    the speech as if recorded close to the microphone, which takes the
    echo away too. On a MacBook recording it cut the echo's decay from
    about 220 ms to 100 ms, and Chatterbox's lines read in the voice from
    about 300 ms to 85 ms. Runs on CUDA or the CPU (about 30 s for 15 s on an
    M-series CPU): on a Mac's GPU (MPS) its output comes out broken.

    The package's import pulls in its training code (deepspeed,
    matplotlib, pandas) that inference never calls; those are stood in for
    when missing, and the model is loaded here rather than through its
    ``enhancer.inference`` module, which imports the trainer.

    python voice_enhance.py --engine resemble_enhance --mode restore --weights DIR in.wav out.wav

Prints one JSON line: the seconds the cleanup took and the output's rate.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import types
import wave
from pathlib import Path
from typing import Any, Tuple

#: Engine -> what it does: ``denoise`` and, for Resemble Enhance, ``restore``.
ENGINE_MODES = {"deepfilternet": ("denoise",), "resemble_enhance": ("denoise", "restore")}
#: The enhancer's settings: Resemble's defaults, with half its default steps
#: (32 of 64 sound the same and take half the time).
RESTORE_STEPS = 32
RESTORE_SOLVER = "midpoint"
RESTORE_LAMBD = 0.5
RESTORE_TAU = 0.5


def decode(path: Path, rate: int) -> Any:
    """Any audio file to mono float32 at ``rate``, through PyAV."""
    import av
    import numpy as np
    chunks = []
    with av.open(str(path)) as box:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in box.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        raise SystemExit("the recording holds no audio")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def write_wav(path: Path, audio: Any, rate: int) -> None:
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 0:
        audio = audio * (0.89 / peak)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(path)


def deepfilternet(src: Path, weights: Path) -> Tuple[Any, int]:
    # mlx_audio.sts imports every speech-to-speech port, Moshi's among them,
    # which wants sentencepiece; DeepFilterNet does not.
    if "sentencepiece" not in sys.modules:
        try:
            import sentencepiece  # noqa: F401
        except ImportError:
            sys.modules["sentencepiece"] = types.ModuleType("sentencepiece")
    from mlx_audio.sts.models.deepfilternet.model import DeepFilterNetModel
    model = DeepFilterNetModel.from_pretrained(str(weights), subfolder=None)
    rate = int(model.config.sample_rate)
    return model.enhance_array(decode(src, rate)), rate


def _stand_in(*names: str) -> None:
    """Empty modules for packages imported only by code inference never
    runs, when they are not installed."""
    import importlib.util
    from unittest.mock import MagicMock
    for name in names:
        root = name.split(".", 1)[0]
        if root not in sys.modules and importlib.util.find_spec(root) is None:
            sys.modules[root] = MagicMock()
        if root in sys.modules and isinstance(sys.modules[root], MagicMock):
            sys.modules[name] = sys.modules[root]


def resemble_enhance(src: Path, weights: Path, mode: str) -> Tuple[Any, int]:
    _stand_in("deepspeed", "deepspeed.accelerator", "deepspeed.runtime", "deepspeed.runtime.engine",
              "deepspeed.runtime.utils", "matplotlib", "matplotlib.pyplot", "pandas")
    import numpy as np
    import scipy.optimize
    import torch

    # The enhancer's step schedule takes float() of fsolve's one-element
    # array, which NumPy 2 refuses; a scalar works under both.
    fsolve = scipy.optimize.fsolve
    if not getattr(fsolve, "_hub_scalar", False):
        def scalar_fsolve(*args: Any, **kwargs: Any) -> Any:
            return np.asarray(fsolve(*args, **kwargs)).reshape(-1)[0]
        scalar_fsolve._hub_scalar = True  # type: ignore[attr-defined]
        scipy.optimize.fsolve = scalar_fsolve

    from resemble_enhance.enhancer.enhancer import Enhancer
    from resemble_enhance.enhancer.hparams import HParams
    from resemble_enhance.inference import inference

    device = "cuda" if torch.cuda.is_available() else "cpu"
    hp = HParams.load(weights)
    model = Enhancer(hp)
    state = torch.load(weights / "ds" / "G" / "default" / "mp_rank_00_model_states.pt", map_location="cpu")
    model.load_state_dict(state["module"])
    model.eval().to(device)
    rate = int(hp.wav_rate)
    wav = torch.from_numpy(decode(src, rate))
    with torch.inference_mode():
        if mode == "restore":
            model.configurate_(nfe=RESTORE_STEPS, solver=RESTORE_SOLVER, lambd=RESTORE_LAMBD, tau=RESTORE_TAU)
            out, rate = inference(model=model, dwav=wav, sr=rate, device=device)
        else:
            out, rate = inference(model=model.denoiser, dwav=wav, sr=rate, device=device)
    return out.cpu().numpy(), int(rate)


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", required=True, choices=sorted(ENGINE_MODES))
    parser.add_argument("--mode", default="denoise", choices=("denoise", "restore"))
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.mode not in ENGINE_MODES[args.engine]:
        parser.error(f"{args.engine} does not {args.mode}")
    started = time.monotonic()
    if args.engine == "deepfilternet":
        audio, rate = deepfilternet(args.input, args.weights)
    else:
        audio, rate = resemble_enhance(args.input, args.weights, args.mode)
    write_wav(args.output, audio, rate)
    print(json.dumps({"seconds": round(time.monotonic() - started, 2), "rate": rate}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

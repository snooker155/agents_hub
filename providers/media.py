"""
Calls to the special models (providers/special.py): images, video, speech,
transcription and a workspace's own models.

Two API shapes are spoken. The OpenAI one (``/images``, ``/videos``,
``/audio/speech``, ``/audio/transcriptions``) covers OpenAI itself, a local
server that mirrors it (Ollama, LM Studio, LocalAI, a whisper server) and any
custom backend with the openai adapter. Google's Gemini API covers Imagen and
Gemini image models, Veo, Gemini speech and Gemini transcription.

Everything here is synchronous and raises
:class:`providers.special.SpecialModelError` with the provider's own message
when a call fails, so the tool can hand it to the agent as it is.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from providers.special import ANTHROPIC, GOOGLE, Endpoint, SpecialModelError

log = logging.getLogger(__name__)

#: Seconds one HTTP request may take (a picture can take a minute).
REQUEST_TIMEOUT = float(os.environ.get("AGENTS_HUB_MEDIA_TIMEOUT", "300") or 300)
#: Seconds an image request may take: a local model (Qwen-Image in the hub
#: runtime) draws for minutes on a laptop.
IMAGE_TIMEOUT = max(REQUEST_TIMEOUT, float(os.environ.get("AGENTS_HUB_IMAGE_TIMEOUT", "1800") or 1800))
#: Seconds between polls of a video job.
VIDEO_POLL_SECONDS = 5.0

_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/ogg": "ogg", "audio/opus": "opus", "audio/aac": "aac", "audio/flac": "flac",
    "application/pdf": "pdf", "application/json": "json", "text/plain": "txt",
}


@dataclass
class Media:
    data: bytes
    mime_type: str
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def extension(self) -> str:
        return _EXT.get(self.mime_type.split(";")[0].strip().lower(), "bin")


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _client():
    import httpx
    return httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True)


def _headers(ep: Endpoint, *, json_body: bool = True) -> Dict[str, str]:
    headers: Dict[str, str] = dict(ep.headers or {})
    if ep.kind == GOOGLE:
        if ep.api_key:
            headers["x-goog-api-key"] = ep.api_key
    elif ep.api_key:
        headers.setdefault("Authorization", f"Bearer {ep.api_key}")
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _error_text(response: Any) -> str:
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:500]
        if err:
            return str(err)[:500]
        return json.dumps(body)[:500]
    except Exception:  # noqa: BLE001 - not JSON: the raw text
        return (getattr(response, "text", "") or "")[:500]


def _check(response: Any, what: str) -> Any:
    if response.status_code >= 400:
        raise SpecialModelError(f"{what} failed ({response.status_code}): {_error_text(response)}")
    return response


def _need_key(ep: Endpoint, provider_label: str) -> None:
    if not ep.api_key:
        raise SpecialModelError(
            f"No API key for {provider_label}. Set it in Settings (or this workspace's key override).")


def _google_parts(response_json: Dict[str, Any]) -> list:
    try:
        return response_json["candidates"][0]["content"]["parts"] or []
    except (KeyError, IndexError, TypeError):
        feedback = response_json.get("promptFeedback") if isinstance(response_json, dict) else None
        raise SpecialModelError(
            f"The model returned nothing{': ' + json.dumps(feedback)[:300] if feedback else ''}") from None


def _inline(part: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    data = part.get("inlineData") or part.get("inline_data")
    if isinstance(data, dict) and data.get("data"):
        return str(data.get("mimeType") or data.get("mime_type") or ""), str(data["data"])
    return None


# ── model lists ──────────────────────────────────────────────────────────────

def list_models(ep: Endpoint, *, timeout: Optional[float] = None) -> list:
    """Every model ``ep`` lists, as ``{"id", "methods"}`` (``methods`` are
    Gemini's supportedGenerationMethods, empty for the OpenAI and Anthropic
    shapes), for :func:`providers.special.discover` and
    :func:`providers.special.check`. ``timeout`` caps each request."""
    out: list = []
    with _client() as client:
        if timeout:
            client.timeout = timeout
        if ep.kind == ANTHROPIC:
            _need_key(ep, "Anthropic")
            headers = {"x-api-key": ep.api_key, "anthropic-version": "2023-06-01"}
            after = ""
            for _ in range(10):
                params = {"limit": 1000, **({"after_id": after} if after else {})}
                body = _check(client.get(f"{ep.base_url}/models", headers=headers, params=params),
                              "Listing models").json()
                out.extend({"id": str(m["id"]), "methods": []} for m in body.get("data") or [] if m.get("id"))
                after = str(body.get("last_id") or "")
                if not body.get("has_more") or not after:
                    break
            return out
        if ep.kind == GOOGLE:
            _need_key(ep, "Google")
            page = ""
            for _ in range(10):
                params = {"pageSize": 1000, **({"pageToken": page} if page else {})}
                body = _check(client.get(f"{ep.base_url}/models", headers=_headers(ep, json_body=False),
                                         params=params), "Listing models").json()
                for m in body.get("models") or []:
                    name = str(m.get("name") or "").removeprefix("models/")
                    if name:
                        out.append({"id": name, "methods": list(m.get("supportedGenerationMethods") or [])})
                page = str(body.get("nextPageToken") or "")
                if not page:
                    break
            return out
        body = _check(client.get(f"{ep.base_url}/models", headers=_headers(ep, json_body=False)),
                      "Listing models").json()
    items = body.get("data") if isinstance(body, dict) else None
    for m in items or []:
        mid = str((m or {}).get("id") or "").strip() if isinstance(m, dict) else ""
        if mid:
            out.append({"id": mid, "methods": []})
    return out


# ── images ───────────────────────────────────────────────────────────────────

def generate_image(ep: Endpoint, model: str, prompt: str, *, options: Dict[str, str],
                   image: Optional[Tuple[str, bytes, str]] = None) -> Media:
    """One image for ``prompt``; with ``image`` (name, bytes, mime) an edit
    of that image."""
    if ep.kind == GOOGLE:
        return _google_image(ep, model, prompt, options=options, image=image)
    return _openai_image(ep, model, prompt, options=options, image=image)


def _openai_image(ep: Endpoint, model: str, prompt: str, *, options: Dict[str, str],
                  image: Optional[Tuple[str, bytes, str]]) -> Media:
    fields: Dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
    for key in ("size", "quality"):
        if options.get(key):
            fields[key] = options[key]
    if model.startswith("dall-e"):
        fields["response_format"] = "b64_json"
    with _client() as client:
        client.timeout = IMAGE_TIMEOUT  # a picture takes longer than any other call
        if image is not None:
            form = {k: str(v) for k, v in fields.items()}
            resp = client.post(f"{ep.base_url}/images/edits", headers=_headers(ep, json_body=False),
                               data=form, files={"image": image})
        else:
            resp = client.post(f"{ep.base_url}/images/generations", headers=_headers(ep), json=fields)
        body = _check(resp, "Image generation").json()
        item = (body.get("data") or [{}])[0]
        if item.get("b64_json"):
            fmt = str(body.get("output_format") or "png").lower()
            mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}.get(fmt, "image/png")
            return Media(base64.b64decode(item["b64_json"]), mime,
                         {"revised_prompt": item.get("revised_prompt")} if item.get("revised_prompt") else {})
        if item.get("url"):
            got = _check(client.get(item["url"]), "Image download")
            return Media(got.content, got.headers.get("content-type", "image/png").split(";")[0])
    raise SpecialModelError("The image model returned no image.")


def _google_image(ep: Endpoint, model: str, prompt: str, *, options: Dict[str, str],
                  image: Optional[Tuple[str, bytes, str]]) -> Media:
    _need_key(ep, "Google")
    with _client() as client:
        client.timeout = IMAGE_TIMEOUT
        if model.startswith("imagen"):
            if image is not None:
                raise SpecialModelError("Imagen models create images; they do not edit one. "
                                        "Use a Gemini image model to edit.")
            params: Dict[str, Any] = {"sampleCount": 1}
            if options.get("aspect_ratio"):
                params["aspectRatio"] = options["aspect_ratio"]
            resp = client.post(f"{ep.base_url}/models/{model}:predict", headers=_headers(ep),
                               json={"instances": [{"prompt": prompt}], "parameters": params})
            preds = _check(resp, "Image generation").json().get("predictions") or []
            if preds and preds[0].get("bytesBase64Encoded"):
                return Media(base64.b64decode(preds[0]["bytesBase64Encoded"]),
                             str(preds[0].get("mimeType") or "image/png"))
            raise SpecialModelError("The image model returned no image (it may have been filtered).")
        parts: list = [{"text": prompt}]
        if image is not None:
            parts.append({"inline_data": {"mime_type": image[2], "data": base64.b64encode(image[1]).decode()}})
        resp = client.post(f"{ep.base_url}/models/{model}:generateContent", headers=_headers(ep),
                           json={"contents": [{"parts": parts}],
                                 "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}})
        note = ""
        for part in _google_parts(_check(resp, "Image generation").json()):
            got = _inline(part)
            if got:
                return Media(base64.b64decode(got[1]), got[0] or "image/png")
            note = note or str(part.get("text") or "")
    raise SpecialModelError("The model returned no image" + (f": {note[:300]}" if note else "."))


# ── video ────────────────────────────────────────────────────────────────────

def start_video(ep: Endpoint, model: str, prompt: str, *, seconds: Optional[int],
                options: Dict[str, str]) -> str:
    """Start a video job; returns a job id that :func:`poll_video` takes."""
    _need_key(ep, "the video provider")
    with _client() as client:
        if ep.kind == GOOGLE:
            params: Dict[str, Any] = {}
            if options.get("aspect_ratio"):
                params["aspectRatio"] = options["aspect_ratio"]
            if seconds:
                params["durationSeconds"] = int(seconds)
            body: Dict[str, Any] = {"instances": [{"prompt": prompt}]}
            if params:
                body["parameters"] = params
            resp = client.post(f"{ep.base_url}/models/{model}:predictLongRunning",
                               headers=_headers(ep), json=body)
            name = _check(resp, "Video generation").json().get("name")
            if not name:
                raise SpecialModelError("The video provider returned no job.")
            return f"google:{name}"
        payload: Dict[str, Any] = {"model": model, "prompt": prompt}
        if seconds:
            payload["seconds"] = str(int(seconds))
        if options.get("size"):
            payload["size"] = options["size"]
        resp = client.post(f"{ep.base_url}/videos", headers=_headers(ep), json=payload)
        vid = _check(resp, "Video generation").json().get("id")
        if not vid:
            raise SpecialModelError("The video provider returned no job.")
        return f"openai:{vid}"


def poll_video(ep: Endpoint, job_id: str, *, timeout: float,
               sleep: Callable[[float], None] = time.sleep) -> Optional[Media]:
    """Wait up to ``timeout`` seconds for a job; the video, or None when it is
    still rendering."""
    kind, _, ref = str(job_id or "").partition(":")
    if not ref or kind not in ("openai", "google"):
        raise SpecialModelError(f"'{job_id}' is not a video job id.")
    deadline = time.monotonic() + max(0.0, timeout)
    with _client() as client:
        while True:
            if kind == "google":
                body = _check(client.get(f"{ep.base_url}/{ref}", headers=_headers(ep)), "Video status").json()
                if body.get("error"):
                    raise SpecialModelError(f"Video generation failed: {json.dumps(body['error'])[:400]}")
                if body.get("done"):
                    samples = (((body.get("response") or {}).get("generateVideoResponse") or {})
                               .get("generatedSamples") or [])
                    uri = ((samples[0] if samples else {}).get("video") or {}).get("uri")
                    if not uri:
                        raise SpecialModelError("Video generation finished without a video "
                                                "(it may have been filtered).")
                    got = _check(client.get(uri, headers=_headers(ep, json_body=False)), "Video download")
                    return Media(got.content, "video/mp4")
            else:
                body = _check(client.get(f"{ep.base_url}/videos/{ref}", headers=_headers(ep)),
                              "Video status").json()
                status = str(body.get("status") or "")
                if status == "failed":
                    raise SpecialModelError(f"Video generation failed: {_error_text_of(body)}")
                if status == "completed":
                    got = _check(client.get(f"{ep.base_url}/videos/{ref}/content",
                                            headers=_headers(ep, json_body=False)), "Video download")
                    mime = got.headers.get("content-type", "video/mp4").split(";")[0]
                    return Media(got.content, mime if mime.startswith("video/") else "video/mp4",
                                 {"seconds": body.get("seconds"), "size": body.get("size")})
            if time.monotonic() >= deadline:
                return None
            sleep(VIDEO_POLL_SECONDS)


def _error_text_of(body: Dict[str, Any]) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)[:400]
    return str(err or "no reason given")[:400]


# ── speech ───────────────────────────────────────────────────────────────────

def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def synthesize_speech(ep: Endpoint, model: str, text: str, *, voice: Optional[str],
                      instructions: Optional[str], options: Dict[str, str]) -> Media:
    if ep.kind == GOOGLE:
        _need_key(ep, "Google")
        prompt = f"{instructions.strip()}: {text}" if instructions and instructions.strip() else text
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                    "voiceName": voice or options.get("voice") or "Kore"}}},
            },
        }
        with _client() as client:
            resp = client.post(f"{ep.base_url}/models/{model}:generateContent", headers=_headers(ep), json=body)
            for part in _google_parts(_check(resp, "Speech synthesis").json()):
                got = _inline(part)
                if got:
                    raw = base64.b64decode(got[1])
                    rate = re.search(r"rate=(\d+)", got[0] or "")
                    if "wav" in (got[0] or ""):
                        return Media(raw, "audio/wav")
                    return Media(_pcm_to_wav(raw, int(rate.group(1)) if rate else 24000), "audio/wav")
        raise SpecialModelError("The speech model returned no audio.")
    fmt = (options.get("format") or "mp3").lower()
    payload: Dict[str, Any] = {"model": model, "input": text, "voice": voice or options.get("voice") or "alloy",
                               "response_format": fmt}
    if instructions and instructions.strip() and not model.startswith("tts-1"):
        payload["instructions"] = instructions.strip()
    with _client() as client:
        resp = _check(client.post(f"{ep.base_url}/audio/speech", headers=_headers(ep), json=payload),
                      "Speech synthesis")
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus", "aac": "audio/aac",
            "flac": "audio/flac", "pcm": "audio/L16"}.get(fmt, "audio/mpeg")
    data = resp.content
    if fmt == "pcm":
        data, mime = _pcm_to_wav(data), "audio/wav"
    return Media(data, mime)


# ── transcription ────────────────────────────────────────────────────────────

def transcribe(ep: Endpoint, model: str, audio: Tuple[str, bytes, str], *, language: Optional[str],
               prompt: Optional[str]) -> str:
    name, data, mime = audio
    if ep.kind == GOOGLE:
        _need_key(ep, "Google")
        ask = "Transcribe this recording verbatim. Return only the transcript."
        if language:
            ask += f" The language is {language}."
        if prompt:
            ask += f" Context: {prompt}"
        body = {"contents": [{"parts": [
            {"text": ask},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
        ]}]}
        with _client() as client:
            resp = client.post(f"{ep.base_url}/models/{model}:generateContent", headers=_headers(ep), json=body)
            parts = _google_parts(_check(resp, "Transcription").json())
        return "".join(str(p.get("text") or "") for p in parts).strip()
    form: Dict[str, str] = {"model": model, "response_format": "json"}
    if language:
        form["language"] = language
    if prompt:
        form["prompt"] = prompt
    with _client() as client:
        resp = client.post(f"{ep.base_url}/audio/transcriptions", headers=_headers(ep, json_body=False),
                           data=form, files={"file": (name, data, mime)})
        body = _check(resp, "Transcription")
    try:
        return str(body.json().get("text") or "").strip()
    except ValueError:
        return body.text.strip()


# ── a workspace's own models ─────────────────────────────────────────────────

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_header_vars(value: str, env_vars: Dict[str, str]) -> str:
    """``${NAME}`` from the workspace's variables, else the process environment."""
    return _VAR_RE.sub(lambda m: str(env_vars.get(m.group(1)) or os.environ.get(m.group(1)) or ""), value)


def call_http_model(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Media]]:
    """POST ``payload`` as JSON. A JSON or text answer comes back as text, a
    binary one (an image, audio, a file) as :class:`Media`."""
    with _client() as client:
        resp = _check(client.post(url, headers={"Content-Type": "application/json", **headers}, json=payload),
                      "The model call")
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json" or ctype.endswith("+json"):
        try:
            body = resp.json()
        except ValueError:
            return resp.text, None
        if isinstance(body, dict):
            for key in ("output", "text", "result", "answer"):
                if isinstance(body.get(key), str):
                    return body[key], None
        return json.dumps(body, ensure_ascii=False), None
    if ctype.startswith("text/") or not ctype:
        return resp.text, None
    return None, Media(resp.content, ctype)


__all__ = [
    "Media", "generate_image", "start_video", "poll_video", "synthesize_speech", "transcribe",
    "call_http_model", "resolve_header_vars", "REQUEST_TIMEOUT", "IMAGE_TIMEOUT",
]

"""
Agent tools over the workspace's special models (providers/special.py).

* ``generate_image`` makes or edits a picture;
* ``generate_video`` makes a short clip (a job that can outlast the call:
  the tool returns its ``job_id`` and a second call with it picks it up);
* ``synthesize_speech`` reads text aloud;
* ``transcribe_audio`` turns a workspace audio or video file into text;
* ``ask_special_model`` calls one of the workspace's own models by id.

The agent never names a provider or a model: the workspace decides
(``special_models`` in its metadata; only a personal workspace falls back to
``default``'s, see providers/special.py). A tool whose purpose has no model in the run's workspace answers
with a ``model_not_added`` error naming where to add one. What a tool makes is saved as a workspace file
(source ``agent``) and recorded on the run's entity sink, so the chat reply
links it like a file the agent wrote.

Each call is priced at the workspace's ``price_usd`` for the purpose, added
to the run's cost and charged to its money cap (common/aux_usage.py
``record_flat``); a call the cap cannot afford is refused before it is made.

Capabilities (tools/capabilities.py): ``transcribe_audio`` and
``ask_special_model`` return text read from a workspace file, so they grant
``reads_private`` like ``read_workspace_file``. The model behind a tool is the
operator's choice of provider, the same trust as the agent's own model, so
sending a prompt there is not counted as sending data out.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

log = logging.getLogger(__name__)

#: How long ``generate_video`` waits for a clip before handing back its job id.
VIDEO_WAIT_ENV = "AGENTS_HUB_VIDEO_WAIT"
DEFAULT_VIDEO_WAIT = 600.0
MAX_VIDEO_WAIT = 1800.0

#: Largest text ``ask_special_model`` passes on from a file.
MAX_FILE_CHARS = 100_000


def _workspace() -> Optional[str]:
    from common.workspace_context import resolve_active_workspace
    return resolve_active_workspace()


def _entry(purpose: str, workspace: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    from providers import special
    entry = special.effective(workspace).get(purpose)
    if not entry:
        from common import personal_workspace
        if personal_workspace.is_personal(workspace):
            return None, _not_added(f"No {purpose} model is added in this personal workspace, "
                                    f"and the '{personal_workspace.FALLBACK}' workspace it falls "
                                    "back to has none either.")
        return None, _not_added(f"No {purpose} model is added in workspace '{workspace}'.")
    return entry, None


def _not_added(what: str) -> str:
    return json_err(
        f"{what} Add one on the Models page, Special models tab, or in the workspace settings "
        "under Special models. Tell the user the model is not added; do not imitate the result "
        "another way.", code="model_not_added")


def _refused(price: Optional[float]) -> Optional[str]:
    try:
        from agents.callbacks.guards import flat_spend_refusal
        reason = flat_spend_refusal(price)
    except Exception:  # noqa: BLE001 - no guard module: nothing to enforce
        log.debug("special models: budget check unavailable", exc_info=True)
        reason = None
    return json_err(reason, code="budget") if reason else None


def _charge(purpose: str, entry: Dict[str, Any], cost: Optional[float], units: Optional[float] = None) -> None:
    from common.aux_usage import record_flat
    record_flat(f"special:{purpose}", provider=str(entry.get("provider") or entry.get("url") or ""),
                model=str(entry.get("model") or entry.get("id") or ""), cost_usd=cost, units=units)


def _input_file(file_id: str, workspace: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    from common.workspace_scope import check_record
    from files import service
    record = service.get_file(str(file_id or "").strip())
    if record is None or check_record(record["workspace"], what="file", workspace=workspace):
        return None, json_err(f"No file '{file_id}' in workspace '{workspace}'.", code="not_found")
    return record, None


def _file_bytes(record: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    from files import service
    try:
        return service.read_bytes(record["file_id"]), None
    except KeyError:
        return None, json_err(f"The content of '{record['name']}' is no longer available.", code="gone")


def _slug(text: str, fallback: str) -> str:
    words = re.sub(r"[^\w\s-]", "", str(text or ""), flags=re.UNICODE).strip().lower().split()
    slug = "-".join(words[:6])[:48].strip("-")
    return slug or fallback


def _save(workspace: str, media: Any, *, name: Optional[str], hint: str, fallback: str,
          purpose: str, entry: Dict[str, Any]) -> str:
    from common.entity_sink import record_entity
    from files import service
    from tools.workspace_files import _brief, agent_provenance
    base = (name or "").strip() or _slug(hint, fallback)
    if "." not in base.rsplit("/", 1)[-1]:
        base = f"{base}.{media.extension}"
    meta = {**agent_provenance(), "special_model": purpose,
            "model": f"{entry.get('provider', '')}/{entry.get('model', '')}".strip("/")}
    try:
        record = service.create_file(workspace, base, media.data, mime_type=media.mime_type,
                                     source="agent", created_by=meta.get("agent_id"), meta=meta)
    except service.FileError as exc:
        return json_err(f"The result could not be saved: {exc}", code="refused")
    record_entity("workspace_file", record["file_id"], "created", label=record["name"], workspace=workspace)
    body = {**_brief(record), "saved": True}
    body.update({k: v for k, v in (media.meta or {}).items() if v})
    return json_ok(body)


def _fail(what: str, exc: Exception) -> str:
    from providers.special import SpecialModelError
    if isinstance(exc, SpecialModelError):
        return json_err(str(exc), code="provider_error")
    log.warning("%s failed", what, exc_info=True)
    return json_err(f"{what} failed: {exc}", code="error")


# ── generate_image ───────────────────────────────────────────────────────────

class GenerateImageInput(BaseModel):
    prompt: str = Field(..., description="What the picture shows: subject, style, composition, colours, any text in it")
    image_file_id: Optional[str] = Field(
        None, description="A workspace image file id to edit instead of drawing from scratch")
    size: Optional[str] = Field(None, description="Size such as 1024x1024 or 1536x1024; leave empty for the workspace default")
    name: Optional[str] = Field(None, description="File name to save as, e.g. logo.png; derived from the prompt when empty")


@tool("generate_image", args_schema=GenerateImageInput)
def generate_image(prompt: str, image_file_id: Optional[str] = None, size: Optional[str] = None,
                   name: Optional[str] = None) -> str:
    """Create a picture from a description, or edit a workspace image, with the
    image model this workspace chose. The picture is saved as a workspace file;
    the result gives its file id and link.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    if not str(prompt or "").strip():
        return json_err("Describe the picture in `prompt`.", code="bad_request")
    entry, err = _entry("image", workspace)
    if err:
        return err
    price = entry.get("price_usd")
    refused = _refused(price)
    if refused:
        return refused
    image = None
    if image_file_id:
        record, err = _input_file(image_file_id, workspace)
        if err:
            return err
        if not str(record.get("mime_type") or "").startswith("image/"):
            return json_err(f"'{record['name']}' is not an image.", code="bad_request")
        data, err = _file_bytes(record)
        if err:
            return err
        image = (record["name"], data, record["mime_type"])
    options = dict(entry.get("options") or {})
    if size:
        options["size"] = size.strip()
    try:
        from providers import media, special
        ep = special.entry_endpoint(entry, workspace)
        result = media.generate_image(ep, entry["model"], prompt.strip(), options=options, image=image)
    except Exception as exc:  # noqa: BLE001 - the agent gets the provider's reason
        return _fail("Image generation", exc)
    _charge("image", entry, price, 1)
    return _save(workspace, result, name=name, hint=prompt, fallback="image", purpose="image", entry=entry)


# ── generate_video ───────────────────────────────────────────────────────────

class GenerateVideoInput(BaseModel):
    prompt: str = Field("", description="What happens in the clip: subject, action, camera, style. Empty only with job_id")
    seconds: Optional[int] = Field(None, description="Clip length in seconds (the model decides what it supports, usually 4 to 12)")
    job_id: Optional[str] = Field(
        None, description="The job_id a previous call returned while the clip was still rendering, to collect it")
    name: Optional[str] = Field(None, description="File name to save as, e.g. intro.mp4")


def _video_wait() -> float:
    try:
        value = float(os.environ.get(VIDEO_WAIT_ENV, "") or DEFAULT_VIDEO_WAIT)
    except ValueError:
        value = DEFAULT_VIDEO_WAIT
    return max(0.0, min(value, MAX_VIDEO_WAIT))


@tool("generate_video", args_schema=GenerateVideoInput)
def generate_video(prompt: str = "", seconds: Optional[int] = None, job_id: Optional[str] = None,
                   name: Optional[str] = None) -> str:
    """Create a short video clip from a description with the video model this
    workspace chose, and save it as a workspace file. Rendering takes minutes:
    when the clip is not ready in time the result says so and gives a `job_id`;
    call this tool again with only that `job_id` to collect it, never start the
    same clip twice.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    entry, err = _entry("video", workspace)
    if err:
        return err
    options = dict(entry.get("options") or {})
    try:
        from providers import media, special
        ep = special.entry_endpoint(entry, workspace)
        if not job_id:
            if not str(prompt or "").strip():
                return json_err("Describe the clip in `prompt`.", code="bad_request")
            length = int(seconds or options.get("seconds") or 0) or None
            price = entry.get("price_usd")
            cost = None if price is None else float(price) * float(length or 8)
            refused = _refused(cost)
            if refused:
                return refused
            job_id = media.start_video(ep, entry["model"], prompt.strip(), seconds=length, options=options)
            _charge("video", entry, cost, length)
        result = media.poll_video(ep, job_id, timeout=_video_wait())
    except Exception as exc:  # noqa: BLE001 - the agent gets the provider's reason
        return _fail("Video generation", exc)
    if result is None:
        return json_ok({"status": "rendering", "job_id": job_id,
                        "note": "The clip is still rendering. Call generate_video again with this "
                                "job_id (and nothing else) to collect it; do not start it again."})
    return _save(workspace, result, name=name, hint=prompt or "video", fallback="video",
                 purpose="video", entry=entry)


# ── synthesize_speech ────────────────────────────────────────────────────────

class SynthesizeSpeechInput(BaseModel):
    text: str = Field(..., description="The exact text to read aloud")
    voice: Optional[str] = Field(None, description="Voice name; leave empty for the workspace default")
    instructions: Optional[str] = Field(
        None, description="How to read it: tone, pace, accent (models that support it)")
    name: Optional[str] = Field(None, description="File name to save as, e.g. greeting.mp3")


@tool("synthesize_speech", args_schema=SynthesizeSpeechInput)
def synthesize_speech(text: str, voice: Optional[str] = None, instructions: Optional[str] = None,
                      name: Optional[str] = None) -> str:
    """Read text aloud with the speech model this workspace chose and save the
    audio as a workspace file; the result gives its file id and link.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    if not str(text or "").strip():
        return json_err("Give the text to read in `text`.", code="bad_request")
    entry, err = _entry("speech", workspace)
    if err:
        return err
    price = entry.get("price_usd")
    cost = None if price is None else float(price) * len(text) / 1000.0
    refused = _refused(cost)
    if refused:
        return refused
    try:
        from providers import media, special
        ep = special.entry_endpoint(entry, workspace)
        result = media.synthesize_speech(ep, entry["model"], text, voice=voice, instructions=instructions,
                                         options=dict(entry.get("options") or {}))
    except Exception as exc:  # noqa: BLE001 - the agent gets the provider's reason
        return _fail("Speech synthesis", exc)
    _charge("speech", entry, cost, round(len(text) / 1000.0, 3))
    return _save(workspace, result, name=name, hint=text, fallback="speech", purpose="speech", entry=entry)


# ── transcribe_audio ─────────────────────────────────────────────────────────

class TranscribeAudioInput(BaseModel):
    file_id: str = Field(..., description="Workspace file id of the recording (audio or video)")
    language: Optional[str] = Field(None, description="Language code such as en or ru, when known")
    prompt: Optional[str] = Field(None, description="Names, terms or context that help spell the transcript right")
    save: bool = Field(False, description="Also save the transcript as a workspace text file")


@tool("transcribe_audio", args_schema=TranscribeAudioInput)
def transcribe_audio(file_id: str, language: Optional[str] = None, prompt: Optional[str] = None,
                     save: bool = False) -> str:
    """Turn a workspace audio or video file into text with the transcription
    model this workspace chose. Returns the transcript; with `save` it is also
    stored as a workspace text file.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    entry, err = _entry("transcription", workspace)
    if err:
        return err
    record, err = _input_file(file_id, workspace)
    if err:
        return err
    mime = str(record.get("mime_type") or "")
    if not (mime.startswith("audio/") or mime.startswith("video/")):
        return json_err(f"'{record['name']}' is not an audio or video file.", code="bad_request")
    price = entry.get("price_usd")
    refused = _refused(price)
    if refused:
        return refused
    data, err = _file_bytes(record)
    if err:
        return err
    try:
        from providers import media, special
        ep = special.entry_endpoint(entry, workspace)
        language = language or (entry.get("options") or {}).get("language")
        text = media.transcribe(ep, entry["model"], (record["name"], data, mime),
                                language=language, prompt=prompt)
    except Exception as exc:  # noqa: BLE001 - the agent gets the provider's reason
        return _fail("Transcription", exc)
    _charge("transcription", entry, price, 1)
    body: Dict[str, Any] = {"file_id": record["file_id"], "name": record["name"], "text": text}
    if save and text:
        from providers.media import Media
        saved = _save(workspace, Media(text.encode("utf-8"), "text/plain"),
                      name=f"{record['name'].rsplit('.', 1)[0]}.transcript.txt", hint="", fallback="transcript",
                      purpose="transcription", entry=entry)
        body["saved"] = saved
    return json_ok(body)


# ── ask_special_model ────────────────────────────────────────────────────────

class AskSpecialModelInput(BaseModel):
    model: str = Field(..., description="The id of one of the workspace's own models listed in your instructions")
    input: str = Field(..., description="What to send the model: the question, the text or the instruction")
    file_id: Optional[str] = Field(None, description="A workspace file to send along with the input")
    name: Optional[str] = Field(None, description="File name for a result that is a file (an image, audio)")


@tool("ask_special_model", args_schema=AskSpecialModelInput)
def ask_special_model(model: str, input: str, file_id: Optional[str] = None,
                      name: Optional[str] = None) -> str:
    """Call one of this workspace's own models (listed with what each is for in
    your instructions) and get its answer. A text answer comes back in the
    result; a file answer (an image, audio) is saved as a workspace file.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    from providers import special
    custom = {c["id"]: c for c in special.effective(workspace).get("custom") or []}
    entry = custom.get(str(model or "").strip().lower())
    if entry is None:
        known = ", ".join(sorted(custom)) or "none"
        return _not_added(f"Model '{model}' is not added in workspace '{workspace}' "
                          f"(its own models: {known}).")
    price = entry.get("price_usd")
    refused = _refused(price)
    if refused:
        return refused
    record = None
    if file_id:
        record, err = _input_file(file_id, workspace)
        if err:
            return err
    try:
        if entry["kind"] == "chat":
            return _ask_chat(entry, input, record, price)
        return _ask_http(entry, input, record, price, workspace, name)
    except Exception as exc:  # noqa: BLE001 - the agent gets the provider's reason
        return _fail(f"The call to '{entry['id']}'", exc)


def _ask_chat(entry: Dict[str, Any], text: str, record: Optional[Dict[str, Any]],
              price: Optional[float]) -> str:
    from langchain_core.messages import HumanMessage
    from agents.agent_utils import build_chat_model
    from common import aux_usage
    content = str(text or "")
    if record is not None:
        from files import service
        file_text = service.extract_text(record["file_id"])
        if file_text is None:
            return json_err(f"'{record['name']}' is binary; a chat model takes its text only.",
                            code="bad_request")
        content += f"\n\n--- {record['name']} ---\n{file_text[:MAX_FILE_CHARS]}"
    llm = build_chat_model(provider=entry["provider"], model=entry["model"])
    response = llm.invoke([HumanMessage(content=content)])
    aux_usage.record(f"special:{entry['id']}", provider=entry["provider"], model=entry["model"],
                     llm=llm, response=response)
    if price:
        _charge(entry["id"], entry, price, 1)
    answer = response.content if isinstance(response.content, str) else str(response.content)
    return json_ok({"model": entry["id"], "output": answer})


def _ask_http(entry: Dict[str, Any], text: str, record: Optional[Dict[str, Any]], price: Optional[float],
              workspace: str, name: Optional[str]) -> str:
    import base64
    from providers import media
    payload: Dict[str, Any] = {"input": str(text or "")}
    if record is not None:
        data, err = _file_bytes(record)
        if err:
            return err
        payload["file"] = {"name": record["name"], "mime_type": record["mime_type"],
                           "data_base64": base64.b64encode(data).decode()}
    env_vars: Dict[str, str] = {}
    try:
        from workspace import get_workspace_metadata
        raw = (get_workspace_metadata(workspace) or {}).get("env_vars") or {}
        env_vars = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001 - no workspace variables: the process environment decides
        log.debug("special models: no env vars for %s", workspace, exc_info=True)
    headers = {k: media.resolve_header_vars(v, env_vars) for k, v in (entry.get("headers") or {}).items()}
    answer, result = media.call_http_model(entry["url"], headers, payload)
    _charge(entry["id"], entry, price, 1)
    if result is not None:
        return _save(workspace, result, name=name, hint=text, fallback=entry["id"],
                     purpose=entry["id"], entry=entry)
    return json_ok({"model": entry["id"], "output": answer})


SPECIAL_MODEL_TOOL_OBJECTS = [generate_image, generate_video, synthesize_speech, transcribe_audio,
                              ask_special_model]

__all__ = ["generate_image", "generate_video", "synthesize_speech", "transcribe_audio",
           "ask_special_model", "SPECIAL_MODEL_TOOL_OBJECTS"]

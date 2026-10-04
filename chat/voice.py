"""
Voice around the assistant's turn (docs/assistant.md "Voice").

The assistant listens and speaks without a speech-to-speech model: the
browser records, the hub transcribes (``POST /api/assistant/transcribe``),
the transcript goes in as an ordinary turn, and the answer's first paragraph
is read back sentence by sentence (``POST /api/assistant/speak``). The agent
loop in between is the chat's own, so policies, budgets, approvals and the
audit trail stay on the path. The routes live in routes/assistant.py; this
module holds what they share and what is worth testing on its own:

* :func:`speakable` turns Markdown into text to read aloud;
* :func:`consent` reads a short spoken yes or no;
* :func:`approval_phrase` and :func:`progress_phrase` are what the hub says
  for a card or a long step, in the person's language;
* :class:`LiveText` remembers what a turn streamed, so ``speak`` reads only
  the turn's own words while the turn is still running;
* :func:`charge_run` and :func:`record_input_run` put the price of speech
  and transcription on the ledger (``voice_calls`` on a run record, priced by
  ``common.pricing``).
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from collections import OrderedDict
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: Languages the dashboard speaks (dashboard/frontend/src/i18n/locales).
LANGUAGES = ("en", "ru", "de")

#: Run record key with the voice calls charged to the run.
VOICE_CALLS_KEY = "voice_calls"
#: Channel of the run that carries one transcription (it has no turn yet).
INPUT_CHANNEL = "voice"

# ── speakable text ───────────────────────────────────────────────────────────

_ON_SCREEN = {
    "en": "Details are on the screen.",
    "ru": "Подробности на экране.",
    "de": "Details stehen auf dem Bildschirm.",
}

_FENCE_RE = re.compile(r"```.*?(```|$)", re.S)
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"(https?://|www\.)\S+")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.M)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_HTML_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t]+")


def _language(language: Optional[str]) -> str:
    lang = str(language or "").strip().lower()[:2]
    return lang if lang in LANGUAGES else "en"


def speakable(text: str, language: Optional[str] = None) -> str:
    """Markdown as it should sound: link anchors instead of links, no
    emphasis marks, headings or bullets; code and tables are not read, a
    short "details are on the screen" stands in for them, once."""
    raw = str(text or "")
    skipped = False
    if _FENCE_RE.search(raw):
        raw, skipped = _FENCE_RE.sub("\n", raw), True
    lines = []
    for line in raw.splitlines():
        if _TABLE_LINE_RE.match(line):
            skipped = True
            continue
        lines.append(line)
    out = "\n".join(lines)
    out = _IMAGE_RE.sub(lambda m: m.group(1), out)
    out = _LINK_RE.sub(lambda m: m.group(1), out)
    if _INLINE_CODE_RE.search(out):
        out, skipped = _INLINE_CODE_RE.sub("", out), True
    out = _URL_RE.sub("", out)
    out = _HEADING_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _BULLET_RE.sub("", out)
    for _ in range(2):  # nested emphasis: **_x_**
        out = _EMPHASIS_RE.sub(lambda m: m.group(2), out)
    out = _HTML_RE.sub("", out)
    out = " ".join(part.strip() for part in out.splitlines() if part.strip())
    out = _SPACES_RE.sub(" ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    if skipped:
        out = f"{out} {_ON_SCREEN[_language(language)]}".strip()
    return out


def normalize(text: str) -> str:
    """Whitespace collapsed, for comparing a sentence with the turn's text."""
    return " ".join(str(text or "").split())


# ── a spoken yes or no ───────────────────────────────────────────────────────

#: Short answers that settle a waiting card, per interface language. Anything
#: longer or different is a new turn: "yes, but only for the first file" is a
#: message for the agent, not a press of the button.
CONSENT = {
    "en": {
        "approve": ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it", "approve",
                    "approved", "confirm", "confirmed", "yes please", "yes go ahead", "yes do it"),
        "deny": ("no", "nope", "deny", "denied", "cancel", "stop", "dont", "do not", "no thanks",
                 "no thank you", "dont do it", "do not do it"),
    },
    "ru": {
        "approve": ("да", "ага", "угу", "давай", "да давай", "да конечно", "конечно", "подтверждаю",
                    "согласен", "согласна", "запускай", "делай", "да делай", "да запускай", "хорошо",
                    "ок", "окей", "можно", "да можно"),
        "deny": ("нет", "не надо", "не нужно", "отмена", "отмени", "отменить", "стоп", "не делай",
                 "нет не надо", "нет спасибо", "не запускай", "отказываюсь"),
    },
    "de": {
        "approve": ("ja", "jawohl", "genau", "ok", "okay", "klar", "mach", "mach das", "bestätige",
                    "bestätigt", "ja bitte", "ja mach", "ja mach das", "einverstanden", "los"),
        "deny": ("nein", "nö", "abbrechen", "stopp", "stop", "nicht", "nein danke", "lass es",
                 "mach das nicht"),
    },
}

#: Longest transcript still read as a yes or no, in words.
MAX_CONSENT_WORDS = 4

_PUNCT_RE = re.compile(r"[^\w\s]", re.U)


def consent(transcript: str) -> Optional[str]:
    """``"approve"``, ``"deny"`` or None: whether a short spoken answer is a
    yes or a no, in any interface language."""
    words = _PUNCT_RE.sub(" ", str(transcript or "").lower().replace("ё", "е")).split()
    if not words or len(words) > MAX_CONSENT_WORDS:
        return None
    said = " ".join(words)
    for phrases in CONSENT.values():
        for decision in ("deny", "approve"):
            if said in {p.replace("ё", "е") for p in phrases[decision]}:
                return decision
    return None


# ── what the hub says itself ─────────────────────────────────────────────────

_APPROVAL = {
    "en": "I need your approval: {what}. Say yes or no, or answer on the card.",
    "ru": "Нужно ваше подтверждение: {what}. Скажите «да» или «нет», или ответьте на карточке.",
    "de": "Ich brauche Ihre Zustimmung: {what}. Sagen Sie ja oder nein, oder antworten Sie auf der Karte.",
}
_CONNECTION = {
    "en": "To connect {what}, type the details into the card on the screen. Secrets are never taken by voice.",
    "ru": "Чтобы подключить {what}, введите данные в карточке на экране. Секреты голосом не принимаются.",
    "de": "Um {what} zu verbinden, geben Sie die Daten in die Karte auf dem Bildschirm ein. "
          "Geheimnisse werden nie per Sprache angenommen.",
}
_DELEGATE = {
    "en": "Handing this to {what}.",
    "ru": "Передаю задачу агенту {what}.",
    "de": "Ich gebe das an {what} weiter.",
}
_WORKING = {
    "en": "Still working: {what}.",
    "ru": "Ещё работаю: {what}.",
    "de": "Ich arbeite noch: {what}.",
}
_COST = {"en": "estimated cost {cost}", "ru": "примерная цена {cost}", "de": "geschätzte Kosten {cost}"}

#: Tools that hand work to another agent; their input names it.
DELEGATION_TOOLS = frozenset({"delegate_task_tool", "delegate_task", "handoff"})
#: The connection card: answered only on screen.
CONNECTION_TOOL = "propose_connection"

_TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_COST_RE = re.compile(r"\$\s?\d+(?:[.,]\d+)?")


def tool_label(tool: str) -> str:
    """``run_team_tool`` -> ``run team``: a tool name as it can be said."""
    name = str(tool or "").strip()
    for suffix in ("_tool",):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return re.sub(r"[_.:-]+", " ", name).strip() or "a tool"


def _first_sentence(text: str, limit: int = 200) -> str:
    one = normalize(text)
    match = re.search(r"(?<=[.!?])\s", one)
    if match:
        one = one[: match.start()]
    return one[:limit].rstrip(" .")


def approval_phrase(approval: Dict[str, Any], language: Optional[str] = None) -> str:
    """One sentence for a waiting card: what the call does and, when the
    card says it, what it costs. A connection card asks for the screen."""
    lang = _language(language)
    tool = str(approval.get("tool") or "")
    data = approval.get("input") if isinstance(approval.get("input"), dict) else {}
    if tool == CONNECTION_TOOL:
        what = str(data.get("target") or data.get("kind") or "the service").strip()[:80]
        return _CONNECTION[lang].format(what=speakable(what, lang) or "the service")
    reason = speakable(str(approval.get("reason") or ""), lang)
    what = tool_label(tool)
    if reason:
        what = f"{what}, {_first_sentence(reason)}"
    cost = _COST_RE.search(" ".join([str(approval.get("reason") or ""),
                                     str(data.get("estimate") or data.get("cost") or "")]))
    if cost and cost.group(0) not in what:
        what = f"{what}, {_COST[lang].format(cost=cost.group(0).replace(' ', ''))}"
    return _APPROVAL[lang].format(what=what)


def progress_phrase(tool: str, agent_name: str = "", language: Optional[str] = None) -> Optional[str]:
    """What the hub says when a turn has been busy for a while: the step it
    is on. None for a name that is not a tool name."""
    tool = str(tool or "").strip()
    if not _TOOL_RE.match(tool):
        return None
    lang = _language(language)
    if tool in DELEGATION_TOOLS and agent_name:
        return _DELEGATE[lang].format(what=agent_name[:80])
    return _WORKING[lang].format(what=tool_label(tool))


# ── what a turn streamed ─────────────────────────────────────────────────────

class LiveText:
    """The text each recent turn streamed, by run id, in this process.

    ``speak`` reads only the turn's own words. Once a turn is finished its
    reply is on the run record, but the browser reads the first sentences
    while tokens still arrive, and what the model said before a tool call
    is streamed without being part of the final reply. So the stream is kept
    here for a while. Bounded in entries and in age; a turn served by another
    API replica is not here, and ``speak`` answers ``not_ready`` until its
    reply is on the record.
    """

    MAX_RUNS = 256
    MAX_CHARS = 200_000
    TTL_SECONDS = 30 * 60.0

    def __init__(self) -> None:
        self._runs: "OrderedDict[str, list]" = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._runs and (len(self._runs) > self.MAX_RUNS
                              or now - next(iter(self._runs.values()))[1] > self.TTL_SECONDS):
            self._runs.popitem(last=False)

    def start(self, run_id: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._runs[str(run_id)] = ["", now]
            self._prune(now)

    def add(self, run_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            entry = self._runs.get(str(run_id))
            if entry is None or len(entry[0]) >= self.MAX_CHARS:
                return
            entry[0] += str(text)

    def get(self, run_id: str) -> Optional[str]:
        with self._lock:
            self._prune(time.monotonic())
            entry = self._runs.get(str(run_id))
            return entry[0] if entry is not None else None

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()


live_text = LiveText()


def turn_tap(holder: Any):
    """A tap for the turn's event queue (chat/entity_chat_router.py ``tap``):
    remembers the run id the turn announces and the text it streams."""
    def tap(event: Dict[str, Any]):
        kind = event.get("type")
        if kind == "run" and event.get("run_id"):
            holder.run_id = str(event["run_id"])
            live_text.start(holder.run_id)
        elif kind == "token" and getattr(holder, "run_id", None):
            live_text.add(holder.run_id, str(event.get("token") or ""))
        elif kind == "message" and event.get("run_id"):
            # The final reply, in case the stream carried no tokens (a
            # replica, a model without streaming).
            live_text.add(str(event["run_id"]), "\n" + str(event.get("content") or ""))
        return ()
    return tap


def spoken_from_turn(text: str, run: Dict[str, Any]) -> Optional[bool]:
    """True when ``text`` is part of what the turn said, False when it is
    not, None when the turn is still running here elsewhere and its words
    are not known to this process yet."""
    wanted = normalize(text)
    if not wanted:
        return False
    streamed = live_text.get(str(run.get("run_id") or ""))
    if streamed is not None and wanted in normalize(streamed):
        return True
    reply = run.get("response")
    if reply and wanted in normalize(str(reply)):
        return True
    if streamed is None and str(run.get("status") or "") in ("running", "queued", "starting"):
        return None
    return False


# ── audio checks ─────────────────────────────────────────────────────────────

#: Recordings the transcription route takes, by the type the browser sends.
AUDIO_TYPES = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/wave": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/x-m4a": "m4a", "video/webm": "webm",
}


def audio_extension(content_type: str) -> Optional[str]:
    return AUDIO_TYPES.get(str(content_type or "").split(";")[0].strip().lower())


def wav_seconds(data: bytes) -> Optional[float]:
    """Length of a WAV recording, None when it cannot be read."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except Exception:  # noqa: BLE001 - not a readable WAV: the size cap still applies
        return None


# ── the ledger ───────────────────────────────────────────────────────────────

def call_entry(purpose: str, entry: Dict[str, Any], cost: Optional[float],
               units: Optional[float] = None) -> Dict[str, Any]:
    """One voice call as ``common.aux_usage.record_flat`` shapes it."""
    item: Dict[str, Any] = {
        "purpose": f"voice:{purpose}", "provider": str(entry.get("provider") or ""),
        "model": str(entry.get("model") or ""), "input_tokens": 0, "output_tokens": 0,
        "cached_tokens": 0, "cost_usd": round(float(cost or 0.0), 6),
    }
    if units is not None:
        item["units"] = units
    return item


def charge_run(run_id: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Add a voice call to a run's ``voice_calls``. Atomic with any other
    write of the record (one transaction), so two sentences spoken at once
    do not drop each other, and the turn's own final write keeps them: it
    merges its keys into the record. Returns the merged record."""
    from common import db
    from managers.run_manager import get_run_by_id, update_run
    with db.transaction():
        run = get_run_by_id(run_id)
        if run is None:
            return None
        calls = [c for c in (run.get(VOICE_CALLS_KEY) or []) if isinstance(c, dict)]
        calls.append(dict(item))
        return update_run(run_id, {VOICE_CALLS_KEY: calls})


def record_input_run(*, workspace: str, user_id: Optional[str], item: Dict[str, Any],
                     agent_id: str, chars: int) -> Optional[str]:
    """A transcription happens before its turn exists, and a recording may
    never become one, so it is a run of its own: channel ``voice``, the
    person who spoke, their home workspace, the call's price in
    ``voice_calls``. Like an outcome grading (tasks/outcome.py), it is
    bookkeeping: no log, no notifications. Returns the run id."""
    try:
        from managers import run_manager as rm
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        run_id = rm.new_unique_run_id()
        record: Dict[str, Any] = {
            "run_id": run_id, "agent_id": agent_id, "channel": INPUT_CHANNEL,
            "session_type": "voice", "message_origin": "assistant-voice",
            "workspace": workspace, "status": "completed", "exit_code": 0,
            "title": "Voice input", "provider": item.get("provider") or "",
            "model": item.get("model") or "", "created_at": now, "started_at": now,
            "finished_at": now, "output": f"Transcribed {chars} characters.",
            VOICE_CALLS_KEY: [dict(item)],
        }
        if user_id:
            record["launched_by"] = user_id
        rm.upsert_run(record)
        return run_id
    except Exception:  # noqa: BLE001 - the transcript stands; only its cost record is missing
        log.warning("voice: could not record the transcription run", exc_info=True)
        return None


__all__ = [
    "AUDIO_TYPES", "CONNECTION_TOOL", "CONSENT", "INPUT_CHANNEL", "LANGUAGES", "LiveText",
    "VOICE_CALLS_KEY", "approval_phrase", "audio_extension", "call_entry", "charge_run",
    "consent", "live_text", "normalize", "progress_phrase", "record_input_run",
    "speakable", "spoken_from_turn", "tool_label", "turn_tap", "wav_seconds",
]

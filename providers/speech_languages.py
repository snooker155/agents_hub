"""
A voice per language for the speech model (docs/assistant.md, "A voice per
language").

Many speech voices speak one language only: a Piper voice is trained on one
(``piper-ru_RU-irina-medium``), Kokoro's voices are English, Japanese or
Chinese by their prefix. One voice for everything reads a Russian answer with
an English voice. So the workspace's speech entry may carry ``languages``,
one model and voice per language code, besides its own::

    {"provider": "hub-local", "model": "piper-en_US-lessac-medium",
     "languages": {"ru": {"model": "piper-ru_RU-irina-medium"},
                   "de": {"model": "piper-de_DE-thorsten-medium"}}}

A cloud voice speaks any language, so there a language names only another
voice of the same model (``{"ru": {"voice": "alloy"}}``).

Before an answer is read, its language is told from the text itself
(:func:`text_language`: the script first, then common words, else the
page's language) and :func:`pick` swaps in that language's model and voice.
A language with no entry is read with the entry's own model and voice, as
before.

**One answer, one voice.** An assistant turn is read sentence by sentence,
but the voice never changes inside it: :func:`turn_language` tells the
language once, from what the turn has said by its first spoken sentence (the
language most of it is in), and keeps it for the rest of the turn. A Russian
answer quoting an English phrase is read by the Russian voice throughout.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

#: Languages one entry may name, at most.
MAX_LANGUAGES = 12
_CODE_RE = re.compile(r"^[a-z]{2}$")
_VOICE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")
_UKRAINIAN = re.compile(r"[іїєґІЇЄҐ]")
_UMLAUT = re.compile(r"[äöüßÄÖÜ]")
_WORD = re.compile(r"[a-zäöüß]+")
# Common words that are rarely the other language's; "die", "an" and "so" are left out.
_GERMAN = frozenset("der das und ist nicht ich sie es ein eine einen mit für auf zu den dem wir ihr wie "
                    "auch sind haben kann bitte danke hallo ja nein gut sehr oder aber noch schon "
                    "jetzt hier wird werden mein dein ihre ihnen".split())
_ENGLISH = frozenset("the and is not you it with for on to we are have can please thanks hello yes no "
                     "good very or but still already now here will this that your what how my".split())


class SpeechLanguageError(ValueError):
    """A ``languages`` value the hub refuses."""


def normalize(raw: Any) -> Dict[str, Dict[str, str]]:
    """``languages`` as it is stored: ``{code: {"model"?, "voice"?}}``, each
    with at least one of the two. Raises :class:`SpeechLanguageError`."""
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise SpeechLanguageError("speech: languages must be an object of language codes")
    if len(raw) > MAX_LANGUAGES:
        raise SpeechLanguageError(f"speech: at most {MAX_LANGUAGES} languages")
    out: Dict[str, Dict[str, str]] = {}
    for code, value in raw.items():
        code = str(code or "").strip().lower()
        if not _CODE_RE.match(code):
            raise SpeechLanguageError(f"speech: '{code}' is not a two-letter language code")
        if not isinstance(value, dict):
            raise SpeechLanguageError(f"speech: languages.{code} must be an object")
        model = str(value.get("model") or "").strip()[:200]
        voice = str(value.get("voice") or "").strip()
        if voice and not _VOICE_RE.match(voice):
            raise SpeechLanguageError(f"speech: languages.{code}.voice must be a voice name")
        if model or voice:
            out[code] = {**({"model": model} if model else {}), **({"voice": voice} if voice else {})}
    return out


def text_language(text: str, fallback: str = "") -> str:
    """The language ``text`` is in, as far as a short line tells: Cyrillic is
    Russian (Ukrainian with its own letters), Latin is German or English by
    umlauts and common words; a tie is ``fallback`` (the page's language) when
    that is one of the two, else English. Empty when the text has no letters."""
    text = str(text or "")
    fallback = str(fallback or "").strip().lower()[:2]
    cyrillic, latin = len(_CYRILLIC.findall(text)), len(_LATIN.findall(text))
    if not cyrillic and not latin:
        return fallback
    if cyrillic >= latin:
        return "uk" if _UKRAINIAN.search(text) else "ru"
    words = _WORD.findall(text.lower())
    german = sum(1 for w in words if w in _GERMAN) + (2 if _UMLAUT.search(text) else 0)
    english = sum(1 for w in words if w in _ENGLISH)
    if german > english:
        return "de"
    if english > german:
        return "en"
    return fallback if fallback in ("en", "de") else "en"


class _TurnLanguages:
    """The language each recent turn is read in, so all its sentences share
    one voice. Bounded and short lived: a turn is read within minutes."""

    MAX_RUNS = 512
    TTL_SECONDS = 30 * 60.0

    def __init__(self) -> None:
        self._runs: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def get_or_set(self, run_id: str, decide) -> str:
        with self._lock:
            now = time.monotonic()
            while self._runs and (len(self._runs) > self.MAX_RUNS
                                  or now - next(iter(self._runs.values()))[1] > self.TTL_SECONDS):
                self._runs.popitem(last=False)
            hit = self._runs.get(run_id)
            if hit is not None:
                return hit[0]
            lang = decide()
            self._runs[run_id] = (lang, now)
            return lang

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()


turns = _TurnLanguages()


def turn_language(run_id: str, turn_text: str, fallback: str = "") -> str:
    """The language a turn's answer is read in: told once, from ``turn_text``
    (what the turn has said so far), and kept for the rest of the turn."""
    if not run_id:
        return text_language(turn_text, fallback)
    return turns.get_or_set(str(run_id), lambda: text_language(turn_text, fallback))


def pick(entry: Dict[str, Any], language: str, voice: str = "", *,
         given_wins: bool = False) -> Tuple[Dict[str, Any], Optional[str]]:
    """The entry to read a line in ``language`` with, and the voice to ask
    for. With an entry for the language its model and voice replace the
    entry's own (a voice of the old model is dropped with it); ``voice``, the
    caller's, wins only with ``given_wins`` (a sample of that very voice) or
    when the language has no entry."""
    over = (entry.get("languages") or {}).get(str(language or "").strip().lower()[:2])
    if not over:
        return entry, (voice or None)
    model = over.get("model") or entry["model"]
    options = dict(entry.get("options") or {})
    if model != entry["model"]:
        options.pop("voice", None)
    chosen = (voice if given_wins and voice else "") or over.get("voice") or (
        None if model != entry["model"] else (voice or None))
    return {**entry, "model": model, "options": options}, (chosen or None)


__all__ = ["MAX_LANGUAGES", "SpeechLanguageError", "normalize", "pick", "text_language", "turn_language", "turns"]

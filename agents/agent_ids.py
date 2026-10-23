"""Agent ids made from a display name.

People name an agent; the id is derived here so they never type one. The id
stays readable in @mentions and folder names: Cyrillic and German letters are
transliterated, everything else outside ``[a-z0-9]`` becomes ``_``, and a
taken id gets ``_2``, ``_3``... (``agents.templates.free_agent_id``).
"""

from __future__ import annotations

import re
from typing import Callable

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
}

MAX_LEN = 40


def slug_from_name(name: str, fallback: str = "agent") -> str:
    """``"Ревьюер кода"`` → ``"revyuer_koda"``; ``fallback`` when nothing is left."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in (name or "").strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:MAX_LEN].rstrip("_")
    if not slug:
        return fallback
    if slug[0].isdigit():
        slug = f"{fallback}_{slug}"
    return slug


def new_agent_id(name: str, taken: Callable[[str], bool], fallback: str = "agent") -> str:
    """A free id for an agent called ``name``."""
    from agents.templates import free_agent_id
    return free_agent_id(slug_from_name(name, fallback), taken)

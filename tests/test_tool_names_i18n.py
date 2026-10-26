"""Every tool the agent page can show has a name in the interface dictionary.

The Tools tab, the tool picker and the agent cards name a tool through
dashboard/frontend/src/i18n/locales/<lang>/toolNames.js and fall back to the
backend's English name when the id is missing there, so a new tool without an
entry shows up untranslated in Russian and German. The locale parity test keeps
the three languages in step; this one keeps English in step with the catalog
(tools/registry.py) and the tools the factory adds (agents/auto_tools.py).
"""
import re
from pathlib import Path

from agents import auto_tools
from tools.registry import get_all_tools

LOCALE = Path(__file__).resolve().parents[1] / "dashboard/frontend/src/i18n/locales/en/toolNames.js"


def _dictionary_ids() -> set:
    return set(re.findall(r"^  ([a-z0-9_]+): \{ label:", LOCALE.read_text(encoding="utf-8"), re.M))


def _auto_tool_ids() -> set:
    source = Path(auto_tools.__file__).read_text(encoding="utf-8")
    ids = set(re.findall(r'add\("([a-z0-9_]+)"', source))
    for group in re.findall(r"(?:names = \[|for n in \()([^\]\)]*)", source):
        ids.update(re.findall(r'"([a-z0-9_]+)"', group))
    return ids


def test_every_catalog_tool_has_a_name():
    missing = sorted({spec.id for spec in get_all_tools()} - _dictionary_ids())
    assert missing == [], f"add these to i18n/locales/*/toolNames.js: {missing}"


def test_every_auto_tool_has_a_name():
    missing = sorted(_auto_tool_ids() - _dictionary_ids())
    assert missing == [], f"add these to i18n/locales/*/toolNames.js: {missing}"

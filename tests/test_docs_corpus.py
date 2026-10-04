"""The documentation corpus and the tools that read it.

The corpus is what lets an agent answer "how does this service work" from the
product's own documentation instead of from inference. Two things are worth
testing: that the corpus stays internally consistent as it is edited, and that
search actually surfaces the right document for the questions people ask.
"""
from __future__ import annotations

import json

import pytest

from tools.docs_tool import DOCS_DIR, _index, _stem, read_doc, search_docs


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


# ── the corpus ───────────────────────────────────────────────────────────────

def test_index_and_files_agree():
    """A doc listed but missing reads as a broken link; a file present but
    unlisted is invisible to search, which is worse — it looks like the
    documentation simply does not cover the subject."""
    indexed = {e["id"] for e in _index() if not e.get("path")}
    on_disk = {p.stem for p in DOCS_DIR.glob("*.md")}
    assert indexed == on_disk, (
        f"only in index: {sorted(indexed - on_disk)}; "
        f"only on disk: {sorted(on_disk - indexed)}"
    )


def test_every_entry_carries_what_search_ranks_on():
    for entry in _index():
        assert entry.get("title"), f"{entry['id']}: no title"
        assert entry.get("summary"), f"{entry['id']}: no summary"
        assert entry.get("headings"), f"{entry['id']}: no headings to rank on"


def test_related_links_resolve():
    ids = {e["id"] for e in _index()}
    for entry in _index():
        for target in entry.get("related") or []:
            assert target in ids, f"{entry['id']} links to missing doc '{target}'"


def test_no_doc_is_a_dead_end():
    """Every document links somewhere. An unlinked page is one a reader can
    only reach by already knowing it exists."""
    for entry in _index():
        assert entry.get("related"), f"{entry['id']} links to nothing"


def test_the_corpus_covers_every_system_agent_surface():
    """A system agent that cannot find documentation for its own subject cannot
    explain itself, which is the point of shipping the corpus."""
    ids = {e["id"] for e in _index()}
    required = {
        "overview", "system-agents", "tools-and-capabilities", "agents",
        "workspaces", "tasks", "flows", "loops", "teams", "playground",
        "memory", "views", "costs", "service-health",
    }
    assert required <= ids, f"missing: {sorted(required - ids)}"


# ── search ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("how do loops stop", "loops"),
    ("capability guard trifecta", "tools-and-capabilities"),
    ("why did my run fail", "sessions-and-runs"),
    ("how much does a scenario cost", "costs"),
    ("what is a workspace", "workspaces"),
    ("telegram bot", "telegram"),
    ("is the service healthy", "service-health"),
    ("attach a skill to an agent", "skills"),
    ("import an agent from a git repository", "imported-agents"),
    ("what did an agent fetch from the web", "web-logs"),
    ("what is new in the latest release", "changelog"),
    ("recently added features and changes", "changelog"),
])
def test_search_puts_the_right_doc_first(query, expected):
    result = _call(search_docs, query=query, limit=3)
    assert result["ok"] is True
    assert result["matches"], f"no match for {query!r}"
    assert result["matches"][0]["id"] == expected, (
        f"{query!r} ranked {[m['id'] for m in result['matches']]}"
    )


def test_stemming_finds_the_plural_title():
    """Without this, 'capability' misses a page titled 'Tools and capabilities',
    which is the exact question the corpus exists to answer."""
    assert _stem("capabilities") == _stem("capability")
    assert _stem("loops") == "loop"
    assert _stem("tasks") == "task"
    # "es" is the whole plural suffix only after a sibilant. Folding it off
    # everything ending in "es" gave "workspac", which no query ever produces.
    assert _stem("workspaces") == "workspace"
    assert _stem("instances") == "instance"
    assert _stem("matches") == "match"
    # Verb endings are deliberately left alone.
    assert _stem("running") == "running"


def test_empty_query_lists_the_whole_corpus():
    result = _call(search_docs, query="")
    assert result["total"] == len(_index())
    assert all("id" in m and "title" in m for m in result["matches"])


def test_a_miss_says_so_instead_of_returning_noise():
    result = _call(search_docs, query="zzzqqq nonexistent gibberish")
    assert result["ok"] is True
    assert result["matches"] == []
    assert "inventing" in result["note"], "the tool must tell the agent not to guess"


def test_search_results_carry_a_snippet_showing_why():
    result = _call(search_docs, query="exit criterion", limit=1)
    top = result["matches"][0]
    assert top["id"] == "loops"
    assert top["snippet"], "a match with no snippet gives the agent nothing to judge on"


# ── reading ──────────────────────────────────────────────────────────────────

def test_read_doc_returns_the_file():
    result = _call(read_doc, doc_id="loops")
    assert result["ok"] is True
    assert result["title"] == "Loops"
    assert "exit criterion" in result["content"]
    assert result["truncated"] is False


def test_read_doc_serves_an_entry_by_path():
    """CHANGELOG.md lives at the repository root, not in docs/; the index entry
    names its path so "what's new" is answerable from the corpus."""
    result = _call(read_doc, doc_id="changelog")
    assert result["ok"] is True
    assert result["content"].startswith("# Changelog")
    assert "## [Unreleased]" in result["content"]


def _corpus_copy(tmp_path, monkeypatch, entries):
    import tools.docs_tool as docs_tool
    docs = tmp_path / "docs"
    docs.mkdir()
    index = docs / "index.json"
    index.write_text(json.dumps({"docs": entries}), encoding="utf-8")
    monkeypatch.setattr(docs_tool, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(docs_tool, "DOCS_DIR", docs)
    monkeypatch.setattr(docs_tool, "INDEX_FILE", index)
    return docs


def test_read_doc_sees_an_edit_without_a_restart(tmp_path, monkeypatch):
    """A runner replica lives for hours; CHANGELOG.md changes under it."""
    import os
    entry = {"id": "changelog", "path": "CHANGELOG.md", "title": "Changelog", "summary": "s"}
    _corpus_copy(tmp_path, monkeypatch, [entry])
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Changelog\n\nold\n", encoding="utf-8")
    assert "old" in _call(read_doc, doc_id="changelog")["content"]
    log.write_text("# Changelog\n\nnew line\n", encoding="utf-8")
    os.utime(log, (log.stat().st_atime, log.stat().st_mtime + 5))
    assert "new line" in _call(read_doc, doc_id="changelog")["content"]


def test_read_doc_reports_a_missing_file_instead_of_empty_content(tmp_path, monkeypatch):
    entry = {"id": "changelog", "path": "CHANGELOG.md", "title": "Changelog", "summary": "s"}
    _corpus_copy(tmp_path, monkeypatch, [entry])
    result = _call(read_doc, doc_id="changelog")
    assert result["ok"] is False
    assert result["code"] == "unreadable"
    assert "CHANGELOG.md" in result["error"]


def test_read_doc_tolerates_the_extension():
    assert _call(read_doc, doc_id="loops.md")["ok"] is True


def test_unknown_doc_lists_what_exists():
    result = _call(read_doc, doc_id="not-a-doc")
    assert result["ok"] is False
    assert result["code"] == "not_found"
    assert "loops" in result["available"]


# ── the prompt side ──────────────────────────────────────────────────────────

def test_every_system_agent_can_reach_the_corpus():
    """The user-facing half of this feature: any agent someone talks to should
    be able to explain the service rather than guess at it."""
    import json as _json
    from common.bootstrap import BOOTSTRAP_AGENTS_FILE, _is_system_seed

    from agents.inheritance import merge_list

    seed = _json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))["agents"]
    by_id = {ad["id"]: ad for ad in seed}
    for ad in seed:
        if not _is_system_seed(ad):
            continue
        tools = ad.get("tools") or []
        if ad.get("extends"):
            # A shipped child (the assistant) holds its parent's tools.
            parent = by_id[ad["extends"]].get("tools") or []
            tools = merge_list(parent, (ad.get("list_deltas") or {}).get("tools"))
        assert "search_docs" in tools and "read_doc" in tools, (
            f"{ad['id']} cannot answer questions about the service"
        )


@pytest.fixture
def live_registry():
    """A registry mirroring the shipped seed, in the suite's throwaway root."""

    from common.bootstrap import seed_registry_from_bootstrap

    seed_registry_from_bootstrap()
    yield


def test_the_help_block_is_gated_on_the_tool(live_registry):
    """An agent without the tools must not be told to call them."""
    from agents.agent_factory import AgentFactory, HELP_PROMPT

    factory = AgentFactory()
    prompt = factory._build_agent("main-agent").system_prompt
    assert HELP_PROMPT in prompt

    from agents.registry import get_agent
    import dataclasses
    from agents.registry import add_agent

    spec = get_agent("main-agent")
    # The workspace tools are main-agent's alone (common/workspace_scope.py),
    # so the probe, another agent, cannot be saved with them.
    from common.workspace_scope import WORKSPACE_ADMIN_TOOLS
    stripped = [t for t in spec.tools
                if t not in ("search_docs", "read_doc") and t not in WORKSPACE_ADMIN_TOOLS]
    add_agent(dataclasses.replace(spec, id="docsless_probe", tools=stripped,
                                  definition_id="main-agent", system=False))
    try:
        assert HELP_PROMPT not in factory._build_agent("docsless_probe").system_prompt
    finally:
        from agents.registry import remove_agent
        remove_agent("docsless_probe")


def test_docs_route_serves_the_changelog_and_404s_an_unknown_id():
    """The in-app Docs page reads the changelog from the corpus, not a copy."""
    from fastapi import HTTPException
    from routes import docs as docs_routes

    doc = docs_routes.get_doc("changelog")
    assert doc["content"].startswith("# Changelog")
    with pytest.raises(HTTPException) as err:
        docs_routes.get_doc("no-such-doc")
    assert err.value.status_code == 404


def test_no_code_span_wraps_onto_a_line_starting_with_a_tag():
    """The site (VitePress) reads markdown through Vue: when an inline code
    span wraps so that its next line starts with ``<id>``, that line opens an
    HTML element instead of continuing the code, and the unclosed element
    fails the whole site build. Rewrap so the span stays on one line."""
    import re
    bad = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = re.sub(r"^[ \t]*```.*?^[ \t]*```", "", path.read_text(), flags=re.S | re.M)
        for m in re.finditer(r"`[^`\n]*\n[^`]*?`", text):
            if any(line.lstrip().startswith("<") for line in m.group(0).split("\n")[1:]):
                bad.append(f"{path.name}: {m.group(0)!r}")
    assert not bad, "inline code wraps onto a line starting with a tag:\n" + "\n".join(bad)

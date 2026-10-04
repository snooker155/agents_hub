"""Reading declarative files (declarative/parser.py): the two file shapes,
where an agent's markdown parts come from, and validation that names the
file and line of every problem before anything reaches a hub."""
from __future__ import annotations

from pathlib import Path

import pytest

from declarative import ValidationError, load_bundle, load_text


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


AGENT = """---
id: scout
name: Scout
tools: [read_file]
---

Find things.
"""


def _problems(excinfo) -> list:
    return [str(p) for p in excinfo.value.problems]


def test_markdown_agent_body_is_the_instructions_and_siblings_are_its_parts(tmp_path):
    _write(tmp_path, "agents/scout.md", AGENT)
    _write(tmp_path, "agents/scout.capabilities.md", "Reads files.\n")
    _write(tmp_path, "agents/helper/agent.md", "---\nid: helper\n---\nHelp.\n")
    _write(tmp_path, "agents/helper/usage.md", "When stuck.\n")
    bundle = load_bundle(tmp_path)
    scout = bundle.get("agent", "scout")
    assert scout.spec["instructions"].strip() == "Find things."
    assert scout.spec["capabilities"] == "Reads files.\n"
    assert scout.spec["tools"] == ["read_file"]
    assert scout.source == "agents/scout.md" and scout.line == 2
    assert bundle.get("agent", "helper").spec["usage"] == "When stuck.\n"
    assert len(bundle) == 2, "the part files are not agents of their own"


def test_a_part_file_named_in_frontmatter_and_inline_text(tmp_path):
    _write(tmp_path, "a.md", "---\nid: a\nusage_file: docs/a-usage.md\ncapabilities: Inline.\n---\nGo.\n")
    _write(tmp_path, "docs/a-usage.md", "From a file.\n")
    res = load_bundle(tmp_path / "a.md").get("agent", "a")
    assert res.spec["usage"] == "From a file.\n" and res.spec["capabilities"] == "Inline."
    assert "usage_file" not in res.spec


def test_yaml_holds_several_documents_and_unrelated_files_are_skipped(tmp_path):
    _write(tmp_path, "hub.yaml", "kind: environment\nid: one\n---\nkind: memory_pool\nid: two\n")
    _write(tmp_path, ".github/workflows/ci.yml", "kind: environment\nid: hidden\n")
    _write(tmp_path, "compose.yaml", "services:\n  web: {}\n")
    _write(tmp_path, "README.md", "# Not an agent\n")
    bundle = load_bundle(tmp_path)
    assert bundle.addresses() == ["environment/one", "memory_pool/two"]
    assert bundle.get("memory_pool", "two").line == 5  # the line of its id


def test_every_problem_is_reported_with_file_and_line(tmp_path):
    _write(tmp_path, "a.yaml", "\n".join([
        "kind: environment",         # 1
        "id: dup",                   # 2
        "---",                       # 3
        "kind: environment",         # 4
        "id: dup",                   # 5
        "---",                       # 6
        "kind: gadget",              # 7
        "id: g",                     # 8
        "---",                       # 9
        "kind: memory_pool",         # 10
        "name: no id",               # 11
        "---",                       # 12
        "kind: environment",         # 13
        "id: typo",                  # 14
        "colour: blue",              # 15
        "is_default: maybe",         # 16
    ]) + "\n")
    with pytest.raises(ValidationError) as excinfo:
        load_bundle(tmp_path)
    problems = _problems(excinfo)
    assert any(p.startswith("a.yaml:5: environment 'dup' is declared twice (first at a.yaml:2)") for p in problems)
    assert any(p.startswith("a.yaml:7: unknown kind 'gadget'") for p in problems)
    assert "a.yaml:10: memory_pool has no id" in problems
    assert any(p.startswith("a.yaml:15: unknown environment field 'colour'") for p in problems)
    assert "a.yaml:16: is_default must be true or false" in problems


def test_duplicate_yaml_key_and_broken_yaml(tmp_path):
    _write(tmp_path, "a.yaml", "kind: environment\nid: a\nmode: local\nmode: docker\n")
    _write(tmp_path, "b.yaml", "kind: environment\nid: [unclosed\n")
    with pytest.raises(ValidationError) as excinfo:
        load_bundle(tmp_path)
    problems = _problems(excinfo)
    assert any(p.startswith("a.yaml:4:") and "duplicate key 'mode'" in p for p in problems)
    assert any(p.startswith("b.yaml:") and "invalid YAML" in p for p in problems)


def test_agent_rules(tmp_path):
    _write(tmp_path, "empty.md", "---\nid: empty\n---\n\n")
    _write(tmp_path, "model.md", "---\nid: model\nmodel:\n  model: gpt-x\n---\nHi.\n")
    _write(tmp_path, "skills.md", "---\nid: skills\nskills:\n  - name: A\n---\nHi.\n")
    with pytest.raises(ValidationError) as excinfo:
        load_bundle(tmp_path)
    problems = _problems(excinfo)
    assert ("empty.md:2: an agent needs instructions (the markdown body), unless it extends "
            "a parent") in problems
    assert "model.md:3: model: name a provider with the model" in problems
    assert "skills.md:3: skills entry 'A' needs steps or a body" in problems


def test_deployment_rules(tmp_path):
    _write(tmp_path, "d.yaml", "kind: deployment\nid: d\nmessage: hi\n")
    with pytest.raises(ValidationError) as excinfo:
        load_bundle(tmp_path)
    problems = _problems(excinfo)
    assert "d.yaml:2: run_at is required unless cron: sets the schedule" in problems
    assert "d.yaml:2: an agent_task deployment names the agent: it runs" in problems


def test_an_explicit_markdown_file_must_have_frontmatter(tmp_path):
    path = _write(tmp_path, "notes.md", "# Just notes\n")
    with pytest.raises(ValidationError) as excinfo:
        load_bundle(path)
    assert "starts with --- frontmatter" in str(excinfo.value)


def test_missing_path_and_load_text():
    with pytest.raises(ValidationError, match="no such file or folder"):
        load_bundle("/nonexistent/declarative/folder")
    bundle = load_text("kind: memory_pool\nid: kit-notes\nblocks:\n  persona: Hi.\n", source="kit.yaml")
    assert bundle.get("memory_pool", "kit-notes").spec["blocks"] == {"persona": "Hi."}
    agent = load_text(AGENT, source="scout.md", markdown=True).get("agent", "scout")
    assert agent.spec["instructions"].strip() == "Find things."


def test_the_example_repository_parses():
    root = Path(__file__).resolve().parents[1] / "examples" / "apply"
    bundle = load_bundle(root)
    assert sorted(bundle.addresses()) == [
        "agent/team_researcher", "agent/team_writer", "deployment/weekday-digest", "deployment/weekly-report",
        "environment/research-sandbox", "memory_pool/team-notes"]

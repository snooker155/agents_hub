"""Example requests for the empty chat: an agent's examples.yaml by UI
language (prompt_assembly.read_examples), else the "Good fits" bullets of its
usage text (prompt_assembly.example_prompts), served by
GET /api/agents/{id}/examples along the extends chain.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents import prompt_assembly  # noqa: E402
from agents.agent_factory import get_factory  # noqa: E402
from agents.prompt_assembly import example_prompts  # noqa: E402
from agents.registry import AgentSpec, add_agent, replace_all_raw  # noqa: E402

USAGE = (
    "Use this agent for numbers.\n\n"
    "Good fits:\n"
    '- "How many orders came in last month?"\n'
    '- "what changed?", "show me `report.csv`" (two in one bullet)\n'
    '- Any "find me some options" request, described rather than quoted\n'
    "- “Summarise this thread for me”\n"
    '- "ok"\n\n'
    "Poor fits:\n"
    '- "Write the code for me"\n'
)


def test_examples_come_from_the_good_fits_bullets():
    assert example_prompts(USAGE, limit=10) == [
        "How many orders came in last month?",
        "What changed?",
        "Show me report.csv",
        "Summarise this thread for me",
    ]


def test_examples_are_capped_and_fall_back_to_every_bullet():
    assert len(example_prompts(USAGE, limit=2)) == 2
    assert example_prompts('Notes:\n- "Plot the weekly totals"\nplain "not a bullet here"') == [
        "Plot the weekly totals",
    ]
    assert example_prompts("") == []


def test_every_shipped_agent_has_six_examples_in_each_language():
    shipped = prompt_assembly.SYSTEM_DEFINITIONS_DIR
    for folder in shipped.iterdir():
        if not (folder / prompt_assembly.INSTRUCTIONS_FILE).is_file():
            continue
        for lang in ("en", "ru", "de"):
            examples = prompt_assembly.read_examples(folder.name, lang, shipped)
            assert len(examples) == 6, (folder.name, lang)
            assert len(set(examples)) == 6, (folder.name, lang)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    replace_all_raw([])
    app = FastAPI()
    app.include_router(agent_routes.router)
    yield TestClient(app), defs
    replace_all_raw([])


def _spec(agent_id: str, **extra) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.agent_factory:build_agent_executor", **extra)


def test_route_prefers_examples_yaml_by_language(client):
    http, defs = client
    add_agent(_spec("helper"))
    prompt_assembly.write_instructions("helper", "You help.", defs)
    prompt_assembly.write_usage("helper", USAGE, defs)
    (defs / "helper" / "examples.yaml").write_text(
        "en: [Plan my week]\nru: [Спланируй мою неделю]\n", encoding="utf-8")
    add_agent(_spec("kid", extends="helper"))
    prompt_assembly.write_instructions("kid", "You help kids.", defs)

    assert http.get("/api/agents/helper/examples?lang=ru").json()["examples"] == ["Спланируй мою неделю"]
    # A language the file lacks falls back to English, a child to its parent's file.
    assert http.get("/api/agents/helper/examples?lang=de").json()["examples"] == ["Plan my week"]
    assert http.get("/api/agents/kid/examples?lang=ru").json()["examples"] == ["Спланируй мою неделю"]
    (defs / "helper" / "examples.yaml").write_text(": not yaml [", encoding="utf-8")
    assert http.get("/api/agents/helper/examples").json()["examples"][0] == "How many orders came in last month?"


def test_route_serves_own_and_inherited_examples(client):
    http, defs = client
    add_agent(_spec("analyst"))
    prompt_assembly.write_instructions("analyst", "You analyse data.", defs)
    prompt_assembly.write_usage("analyst", USAGE, defs)
    add_agent(_spec("finance", extends="analyst"))
    prompt_assembly.write_instructions("finance", "Finance is your domain.", defs)
    add_agent(_spec("bare"))
    prompt_assembly.write_instructions("bare", "No usage text.", defs)

    own = http.get("/api/agents/analyst/examples").json()
    assert own["examples"][0] == "How many orders came in last month?"
    assert http.get("/api/agents/finance/examples").json()["examples"] == own["examples"]
    assert http.get("/api/agents/bare/examples").json()["examples"] == []
    assert http.get("/api/agents/missing/examples").status_code == 404

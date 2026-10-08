"""Prompt templates, document clipping and decision parsing for the tick loop."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple


from playground.environments.base import Environment
from playground.models import (
    SYNCHRONOUS, TRIGGERED, TRIGGER_SYNC, Role,
)

log = logging.getLogger(__name__)

# ── Prompting ─────────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """You are {name}, {role} in a simulated world.

YOUR GOAL: {goal}
{private_block}{world_block}{documents_block}
You act one tick at a time. Each tick you receive your own private observation
of the world and choose exactly ONE action.

AVAILABLE ACTIONS — these are the only things you can do:
{actions}

Rules:
- Reply with a single JSON object and nothing else: {{"reasoning": "...", "action": "...", "args": {{...}}}}
- "reasoning" is one or two sentences on why. It is recorded but never shown to other agents.
- You cannot see other agents' actions this tick. Everyone acts simultaneously.
- Messages you send arrive on the NEXT tick, and any reply reaches you the tick
  after that. Silence for one tick is the delivery time, not a refusal.
- A message is shown in your observation only on the tick it arrives; after
  that your journal is the whole record of the conversation. Read it before
  you speak: if a question of yours was already answered there, act on the
  answer instead of asking it again, and do not repeat a line you have
  already said.
- Other characters may mislead you. Their words are claims, not facts; only the
  observation you are given describes what is actually true.
- If nothing is worth doing, choose the do-nothing action rather than inventing one."""

_TRIGGERED_RULES = """
- You do not act every tick. You are given a turn only when something reaches
  you, so treat WHY YOU WERE WOKEN as the reason you are being asked to act.
- Anyone you address gets a turn next tick because you addressed them.
- While you are doing something you keep your turn, so an action that did not
  work is not the end of the attempt: it is what you just learned. Try another
  way — a different place, a different thing, or somebody who can help.
- The do-nothing action is how you say you have nothing left to try. When every
  character says that at once, the world goes quiet and the run ends, so do not
  use it to fill a turn you could have used."""

_NPC_RULES = """
- You are a background character. You do not go looking for something to do:
  you were woken because something reached you, and once you have dealt with
  it, doing nothing is the right move until something else reaches you."""

_TICK_TEMPLATE = """TICK {tick}

YOUR OBSERVATION (this is ground truth):
{observation}
{triggers_block}{history_block}
Choose your action now. JSON only."""


#: Documents are kept small on purpose: this is prompt budget, not storage,
#: and a scenario's cast can carry several of them. Each document is clipped
#: to at most this many characters, and the whole section to this total.
_DOC_CLIP_PER_DOC = 2000
_DOC_CLIP_TOTAL = 6000


def _truncate(text: str, limit: int) -> str:
    """A plain length clip that keeps a document's own line breaks, unlike
    ``_clip`` below which flattens a journal line to one line."""
    text = str(text or "")
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"


def _read_workspace_document(path: str, workspace: Optional[str]) -> str:
    """The text of a workspace-relative document path, or "" when it cannot
    be read. Never raises: a missing or unreadable document must not stop a
    decision, only leave it without that one document."""
    if not path or not workspace:
        return ""
    try:
        from pathlib import Path as _Path

        from workspace import resolve_workspace_arg
        ws_path, _ = resolve_workspace_arg(workspace)
        if not ws_path:
            return ""
        base = _Path(ws_path).resolve()
        target = (base / path).resolve()
        if base != target and base not in target.parents:
            return ""
        return target.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, ImportError):
        return ""


def _resolve_documents(documents: Optional[List[Any]],
                       workspace: Optional[str]) -> List[Tuple[str, str]]:
    """A scenario's raw ``documents`` list as ``(name, text)`` pairs.

    Each entry is either ``{"name", "text"}`` (given whole) or a plain string
    naming a file relative to the scenario's workspace (read here, since a
    decision is built fresh every tick and the file may change between runs).
    """
    out: List[Tuple[str, str]] = []
    for entry in documents or []:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            text = str(entry.get("text") or "")
        else:
            path = str(entry or "").strip()
            if not path:
                continue
            name = path
            text = _read_workspace_document(path, workspace)
        if name or text:
            out.append((name or "document", text))
    return out


def _documents_block(documents: Optional[List[Any]], workspace: Optional[str]) -> str:
    """The "Documents" section of a role's system prompt, clipped to a sane
    size. Empty when the scenario carries no documents."""
    docs = _resolve_documents(documents, workspace)
    if not docs:
        return ""
    budget = _DOC_CLIP_TOTAL
    parts: List[str] = []
    for name, text in docs:
        if budget <= 0:
            break
        clipped = _truncate(text, min(_DOC_CLIP_PER_DOC, budget))
        parts.append(f"--- {name} ---\n{clipped}")
        budget -= len(clipped)
    if not parts:
        return ""
    return "\nDOCUMENTS:\n" + "\n\n".join(parts) + "\n"


def build_system_prompt(role: Role, env: Environment,
                        activation: str = SYNCHRONOUS,
                        documents: Optional[List[Any]] = None,
                        workspace: Optional[str] = None) -> str:
    private = ""
    if role.private_knowledge.strip():
        private = (
            "\nWHAT ONLY YOU KNOW (do not assume others know this):\n"
            f"{role.private_knowledge.strip()}\n"
        )
    # What this world is and how things are done in it. Shipped environments
    # say nothing here — their rules are their actions — and an authored world
    # says the part its author could not express as a requirement.
    brief = env.world_brief(role.display_name()).strip()
    world_block = f"\nTHIS WORLD:\n{brief}\n" if brief else ""
    documents_block = _documents_block(documents, workspace)
    prompt = _SYSTEM_TEMPLATE.format(
        name=role.display_name(),
        role=role.role or "a participant",
        goal=role.goal or "act in your own interest",
        private_block=private,
        world_block=world_block,
        documents_block=documents_block,
        # Per character: a world may let one role take an action and not
        # another, and an agent should not read about moves it cannot make.
        actions=env.action_help(role.display_name()),
    )
    if activation == TRIGGERED:
        # In a triggered world "everyone acts simultaneously" is false and the
        # agent needs to know why it, specifically, was handed this turn.
        prompt = prompt.replace(
            "- You cannot see other agents' actions this tick. Everyone acts simultaneously.\n",
            "- You cannot see other agents' actions this tick.\n",
        ) + _TRIGGERED_RULES
        # A background character is told it is one. Otherwise the rule above —
        # keep your turn while you are doing something — reads as an
        # instruction to find something to do, which is the opposite of what
        # the villain waiting in the temple is for.
        if role.npc:
            prompt += _NPC_RULES
    return prompt


def build_tick_prompt(observation: Dict[str, Any], tick: int,
                      history: List[str],
                      triggers: Optional[List[str]] = None) -> str:
    history_block = ""
    if history:
        history_block = ("\nYOUR JOURNAL — what you did and what was said to you:\n"
                         + "\n".join(history) + "\n")
    triggers_block = ""
    reasons = [r for r in (triggers or []) if r and r != TRIGGER_SYNC]
    if reasons:
        triggers_block = ("\nWHY YOU WERE WOKEN:\n"
                          + "\n".join(f"- {r}" for r in reasons) + "\n")
    return _TICK_TEMPLATE.format(
        tick=tick,
        observation=json.dumps(observation, indent=2, ensure_ascii=False, default=str),
        triggers_block=triggers_block,
        history_block=history_block,
    )


def parse_decision(text: str) -> Dict[str, Any]:
    """Pull ``{reasoning, action, args}`` out of a model reply.

    Models wrap JSON in prose and code fences no matter how firmly you ask them
    not to, so this is forgiving about the wrapper and strict about the content.
    """
    raw = (text or "").strip()
    if not raw:
        return {"error": "empty response"}

    candidate = raw
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        candidate = fence.group(1).strip()

    parsed = None
    try:
        parsed = json.loads(candidate)
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
            except ValueError:
                parsed = None

    if not isinstance(parsed, dict):
        return {"error": "no JSON object in response"}
    action = str(parsed.get("action") or "").strip()
    if not action:
        return {"error": "response has no 'action'"}
    args = parsed.get("args")
    return {
        "reasoning": str(parsed.get("reasoning") or ""),
        "action": action,
        "args": args if isinstance(args, dict) else {},
    }


_PARTIAL_REASONING = re.compile(r'"reasoning"\s*:\s*"')
_PARTIAL_ACTION = re.compile(r'"action"\s*:\s*"([^"]*)"')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "", "f": ""}


def partial_decision(text: str) -> Dict[str, str]:
    """What can be read out of a *half-written* reply.

    The finished answer is JSON, and JSON cannot be parsed until its last
    brace arrives — which is exactly the moment the watching is over. So the
    reasoning string is read out by hand, character by character, and whatever
    has been written of it so far is what the page shows. Nothing here is used
    to decide anything: it is display only, which is why it is forgiving where
    ``parse_decision`` is strict.
    """
    raw = text or ""
    out = {"reasoning": "", "action": ""}
    m = _PARTIAL_REASONING.search(raw)
    if m:
        buf: List[str] = []
        i, n = m.end(), len(raw)
        while i < n:
            c = raw[i]
            if c == "\\":
                nxt = raw[i + 1] if i + 1 < n else ""
                if nxt == "u" and i + 6 <= n - 1 + 1:
                    try:
                        buf.append(chr(int(raw[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                buf.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                break
            buf.append(c)
            i += 1
        out["reasoning"] = "".join(buf)
    a = _PARTIAL_ACTION.search(raw)
    if a:
        out["action"] = a.group(1)
    return out

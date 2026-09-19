"""
What each member of a team is told, and how its answer is read back.

Three things are layered onto an agent when it takes a seat on a team, none of
which belong in its own ``instructions.md`` because all three are per-team:

1. the **charter** — what this team is and what it is for;
2. the **roster** — every teammate's name, role and manifest, so the agent knows
   who exists and what each is for before it decides to hand anything over;
3. the **protocol** — how to address a teammate, and how to say it is finished.

The roster is the reason a team is not just "several agents in a workspace".
An agent that can see the whole registry can only guess who to ask; an agent
that carries four colleagues and their manifests can address one by name.

Parsing is deliberately forgiving. Members reply in prose (they are ordinary
agents doing ordinary work, and forcing JSON on them would cost their tool use),
so addressing is read from ``@Name`` mentions. Only the *leader* is held to a
JSON contract, because deciding who acts next is a structured decision and a
mis-parse there stalls the team.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from teams.models import (
    BROADCAST, DONE_TOKEN, HANDOFF_MODES, Team, TeamMember, TeamMessage,
)


# ── Blocks ───────────────────────────────────────────────────────────────────

def roster_block(team: Team, viewer: Optional[TeamMember] = None) -> str:
    """The team roster as every member sees it."""
    lines: List[str] = []
    if team.mode == "centralized" and team.leader_agent_id:
        lines.append(
            f"- **{team.leader_display_name()}** — team lead. Assigns the work "
            f"and decides when the team is finished."
        )
    entry = team.entry_member() if team.mode in HANDOFF_MODES else None
    for m in team.members:
        you = "  ← this is you" if viewer and m.agent_id == viewer.agent_id else ""
        if entry is not None and m.agent_id == entry.agent_id:
            you += "  (takes the incoming request)"
        manifest = (m.manifest or "").strip() or "(no manifest recorded)"
        role = (m.role or "").strip()
        head = f"- **{m.display_name()}**" + (f" — {role}" if role else "")
        lines.append(f"{head}{you}\n    {manifest}")
    return "\n".join(lines) or "(no members)"


#: The one line every member is held to, whatever the mode.
_COLLEAGUES = (
    "- You are talking to colleagues, not to a user. Do not open with pleasantries or\n"
    "  close by offering further help — say what you did, what you found, and what you need.\n"
)


def _protocol_block(team: Team, *, is_leader: bool) -> str:
    """How this member is told to speak — which differs by mode, because in
    each mode a different thing decides who acts next."""
    if is_leader:
        return (
            "## How this team communicates\n"
            "- Everything the members write is posted to a shared board that you read each round.\n"
            "- You do not do the work yourself. You decide who does it and what exactly they must do.\n"
            "- Address members by the names in the roster; those are the only agents you can reach.\n"
        )

    if team.mode in HANDOFF_MODES:
        # Handoff mode: addressing a teammate is not a courtesy, it is the only
        # thing that gives anyone a turn. Said plainly, because a member that
        # writes a broadcast here has quietly ended its branch of the work.
        return (
            "## How this team works\n"
            "- There is no coordinator. Nobody on this team acts unless a colleague asked them to.\n"
            "- You have this turn because work was addressed to you.\n"
            "- Do the part that is yours under your manifest, and say what came of it.\n"
            "- For anything another member's manifest covers, hand it to them: start a line\n"
            "  with `@Name:` and say exactly what you need from them. **A teammate only acts\n"
            "  if you address them by name** — describing what someone else ought to do,\n"
            "  without addressing them, means it never happens.\n"
            "- You may address several teammates in one turn; each of them gets your whole message.\n"
            "- Hand off only what is genuinely theirs. Each handoff costs the team a round.\n"
            f"{_COLLEAGUES}"
            f"- When your part is done and you need nothing from anyone, write `{DONE_TOKEN}`\n"
            "  on its own line. Never write it in the same turn as a handoff.\n"
        )

    direct = (
        "- To address one teammate, start a line with their name: "
        "`@Name: ...`. Only they and you will see that message.\n"
        if team.allow_direct_messages else
        "- Everything you write is seen by the whole team; there are no private messages.\n"
    )
    return (
        "## How this team communicates\n"
        "- Everything you write is posted to a shared board the team reads next round.\n"
        f"{direct}"
        f"{_COLLEAGUES}"
        "- Ask a teammate directly when their manifest says the work is theirs. Do not\n"
        "  attempt work that another member is on the team to do.\n"
        f"- When the team's goal is met and you have nothing further, write `{DONE_TOKEN}`\n"
        "  on its own line. Do not write it while anything is still open on your side.\n"
    )


def team_system_prompt(team: Team, member: Optional[TeamMember], *,
                       is_leader: bool = False) -> str:
    """The team block appended to a member's own system prompt.

    Appended, never substituted: the agent keeps its own instructions, expertise
    and tools, and gains a seat on this team on top of them.
    """
    parts = [
        f"# You are on the team: {team.name}",
        (team.description or "").strip(),
        (f"## What this team is for\n{team.charter.strip()}" if team.charter.strip() else ""),
        f"## The team\n{roster_block(team, member)}",
    ]
    if member is not None:
        seat = [f"## Your seat\nYou are **{member.display_name()}**"]
        if member.role.strip():
            seat.append(f", the team's {member.role.strip()}")
        seat.append(".")
        if member.manifest.strip():
            seat.append(f"\n\nWhat the team expects of you:\n{member.manifest.strip()}")
        if member.goal.strip():
            seat.append(f"\n\nYour own objective on this team:\n{member.goal.strip()}")
        parts.append("".join(seat))
    parts.append(_protocol_block(team, is_leader=is_leader))
    return "\n\n".join(p for p in parts if p and p.strip())


def board_block(
    messages: List[TeamMessage], viewer_name: str, *, see_all: bool = False,
    limit: int = 40, char_budget: int = 20000,
) -> str:
    """The part of the board this member may read, newest-last.

    ``see_all`` is for the coordinator: a lead that cannot read the work it
    assigned cannot lead. Ordinary members never get it — a team where everyone
    reads everyone's mail is one long prompt, not a team.

    Bounded twice — by count and by characters — because a team's transcript is
    the thing that grows without limit, and the oldest exchanges are the ones
    the members have already acted on.
    """
    visible = [m for m in messages if see_all or m.visible_to(viewer_name)]
    visible = visible[-limit:]
    rendered: List[str] = []
    total = 0
    for m in reversed(visible):
        to = "" if m.is_broadcast() else f" → {', '.join(m.recipients)}"
        body = (m.content or "").strip()
        entry = f"[round {m.round}] {m.sender}{to}:\n{body}"
        total += len(entry)
        if total > char_budget and rendered:
            rendered.append("[... earlier messages omitted ...]")
            break
        rendered.append(entry)
    return "\n\n".join(reversed(rendered)) or "(nothing has been said yet)"


# ── Member turn ──────────────────────────────────────────────────────────────

_MEMBER_TURN = """## The team's goal
{goal}

## The team board — what has been said so far
{board}

## Your turn (round {round} of at most {max_rounds})
{instruction}

Reply with what you want the team to have. Keep it to the substance."""

_NO_INSTRUCTION = (
    "No one has assigned you anything this round. Read the board, do the part of "
    "the goal that is yours under your manifest, and post the result. If your part "
    "is genuinely finished and nothing on the board is waiting on you, say so."
)

_ENTRY_INSTRUCTION = """This request has just reached the team, and you are the member it lands on.
Nobody has looked at it yet. Decide what happens to it:

- if it falls under your own manifest, do it now and post the result;
- if it belongs to a teammate under theirs, hand it over — `@Name:` followed by
  exactly what you need from them and by when it is enough;
- if it needs several of them, address each one in the same turn;
- if it needs you *and* them, do your part now and hand the rest on.

Do not restate the request back to the team, and do not write a plan for someone
to approve. Either act on it or route it."""


def handoff_instruction(sender: str, text: str) -> str:
    """What a member is told when a colleague handed it work.

    The colleague's message is quoted whole rather than summarised: it is the
    assignment, and a paraphrase of an assignment is a different assignment.
    """
    return (
        f"{sender} has handed this to you:\n\n{(text or '').strip()}\n\n"
        "Do your part of it now and post the result. If some of it belongs to "
        "another member under their manifest, hand that part on by name in the "
        "same turn."
    )


def entry_instruction() -> str:
    return _ENTRY_INSTRUCTION


def member_turn_prompt(
    *, team: Team, member: TeamMember, goal: str, board: str, round_no: int,
    instruction: str = "",
) -> str:
    return _MEMBER_TURN.format(
        goal=(goal or "").strip() or "(no goal stated)",
        board=board,
        round=round_no,
        max_rounds=team.max_rounds,
        instruction=(instruction or "").strip() or _NO_INSTRUCTION,
    )


def parse_member_reply(
    text: str, team: Team, *, allow_direct: bool = True,
) -> Tuple[str, List[str], bool]:
    """Read a member's free-text reply into ``(content, recipients, done)``.

    Addressing is taken from ``@Name`` mentions that resolve against the roster;
    an ``@`` that matches nobody is left alone (it is prose, or an email address,
    not an attempt to address a teammate). The text is never split apart — the
    whole reply is posted, because a message cut into fragments loses the
    reasoning that held it together.
    """
    raw = (text or "").strip()
    done = bool(re.search(rf"(?m)^\s*{re.escape(DONE_TOKEN)}\s*$", raw))
    if done:
        raw = re.sub(rf"(?m)^\s*{re.escape(DONE_TOKEN)}\s*$", "", raw).strip()

    recipients: List[str] = []
    if allow_direct:
        for mention in re.findall(r"(?m)^\s*@([\w .\-]+?)\s*[:,]", raw):
            member = team.member_by_name(mention)
            if member and member.display_name() not in recipients:
                recipients.append(member.display_name())
    return raw, (recipients or [BROADCAST]), done


# ── Leader turn ──────────────────────────────────────────────────────────────

_LEADER_CONTRACT = """Reply with a single JSON object and nothing else:

{"assignments": [{"agent": "<name from the roster>", "instruction": "<exactly what they must do now>"}], "done": false, "final": ""}

- "assignments" is who acts this round. Assign only members whose contribution
  you actually need now; assigning everyone every round wastes the team.
- Set "done" to true and put the team's finished answer in "final" when the goal
  is met. When "done" is true, "assignments" must be empty.
- An instruction is a task, not a topic: say what to produce and against what."""

_LEADER_TURN = """## The team's goal
{goal}

## The team board — what has been said so far
{board}

## Your decision for round {round} of at most {max_rounds}
Decide who acts now and what exactly each of them must do. If the goal is met,
finish the run instead and write the team's answer yourself from what is on the
board — do not assign another round of work to confirm what is already done.

{contract}"""


def leader_turn_prompt(*, team: Team, goal: str, board: str, round_no: int) -> str:
    return _LEADER_TURN.format(
        goal=(goal or "").strip() or "(no goal stated)",
        board=board, round=round_no, max_rounds=team.max_rounds,
        contract=_LEADER_CONTRACT,
    )


@dataclass
class LeaderPlan:
    """The leader's decision for one round."""
    assignments: List[Tuple[str, str]] = field(default_factory=list)  # (member name, instruction)
    done: bool = False
    final: str = ""
    raw: str = ""
    error: str = ""


def parse_leader_plan(text: str, team: Team) -> LeaderPlan:
    """Read the leader's JSON decision, with a fallback that keeps the team moving.

    A leader that answers in prose has still said something useful, so rather
    than stalling the round we treat its text as an instruction — addressed to
    the members it named, or to the whole team when it named none. A stalled
    team is a worse failure than a slightly over-broad assignment.
    """
    raw = (text or "").strip()
    plan = LeaderPlan(raw=raw)
    if not raw:
        plan.error = "the team lead returned nothing"
        return plan

    candidate = raw
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        candidate = fence.group(1).strip()
    parsed: Any = None
    try:
        parsed = json.loads(candidate)
    except Exception:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
            except Exception:
                parsed = None

    if isinstance(parsed, dict):
        plan.done = bool(parsed.get("done"))
        plan.final = str(parsed.get("final") or "").strip()
        for a in parsed.get("assignments") or []:
            if not isinstance(a, dict):
                continue
            member = team.member_by_name(str(a.get("agent") or ""))
            instruction = str(a.get("instruction") or "").strip()
            if member and instruction:
                plan.assignments.append((member.display_name(), instruction))
        if plan.done and not plan.final:
            # "done" with nothing to show is not an answer; the board is the
            # only place the result can be, so let the synthesis pass write it.
            plan.final = ""
        return plan

    plan.error = "the team lead did not return the required JSON object"
    named = [
        m.display_name() for m in team.members
        if re.search(rf"\b{re.escape(m.display_name())}\b", raw, re.I)
        or re.search(rf"\b{re.escape(m.agent_id)}\b", raw, re.I)
    ]
    targets = named or [m.display_name() for m in team.members]
    plan.assignments = [(name, raw) for name in targets]
    return plan


# ── Closing synthesis ────────────────────────────────────────────────────────

_SYNTHESIS = """## The team's goal
{goal}

## Everything the team produced
{board}

## Your closing task
Write the team's answer to the goal above, as one deliverable for whoever asked.
Use what is on the board; do not start new work and do not describe the process.
If the team did not finish, say plainly what is done, what is not, and what is
blocking it. No preamble."""


def synthesis_prompt(*, goal: str, board: str) -> str:
    return _SYNTHESIS.format(goal=(goal or "").strip() or "(no goal stated)", board=board)


# ── Manifests ────────────────────────────────────────────────────────────────

def suggest_manifest(agent_id: str) -> Dict[str, str]:
    """A starting manifest for an agent being added to a team.

    Drawn from what the agent already declares about itself (its registry
    description and ``capabilities.md``) so a roster is never empty by default —
    but it is a *suggestion*: the manifest is a per-team commitment, and the
    operator is expected to sharpen it.
    """
    from agents.prompt_assembly import read_capabilities
    from agents.registry import get_agent

    spec = get_agent(agent_id)
    if spec is None:
        return {"agent_id": agent_id, "name": agent_id, "role": "", "manifest": ""}

    capabilities = (read_capabilities(spec.def_id()) or "").strip()
    if capabilities:
        # The first few bullets/lines say what it does; the rest is detail that
        # belongs in the agent's own prompt, not on a roster line.
        lines = [l.strip() for l in capabilities.splitlines() if l.strip()]
        lines = [l for l in lines if not l.startswith("#")][:4]
        capabilities = " ".join(lines)
    manifest = (spec.description or "").strip()
    if capabilities:
        manifest = f"{manifest}\n{capabilities}".strip()
    return {
        "agent_id": spec.id,
        "name": spec.name or spec.id,
        "role": spec.domain or "",
        "manifest": manifest[:1200],
    }


__all__ = [
    "roster_block", "team_system_prompt", "board_block", "member_turn_prompt",
    "entry_instruction", "handoff_instruction",
    "parse_member_reply", "leader_turn_prompt", "parse_leader_plan", "LeaderPlan",
    "synthesis_prompt", "suggest_manifest",
]

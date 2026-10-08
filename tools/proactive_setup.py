"""
``schedule_pulse``: the assistant sets up a proactive agent from a phrase
(proactive/from_phrase.py, docs/proactive.md "From a phrase").

"Every morning at 8 tell me the weather and my calendar" becomes a pulse: the
schedule, the instruction, where the result goes and which agent runs it. The
call waits for the person's yes on a card that shows the schedule in plain
words ("Every day at 08:00 Europe/Berlin", tools/approval.py ``ALWAYS_GATED``),
and nothing is saved before it. Runs as the person, who needs the editor role
in the workspace.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok


class SchedulePulseInput(BaseModel):
    brief: str = Field(..., description="What to do at each run, as an instruction: 'Tell me the weather in "
                                        "Berlin and my calendar for the day.'")
    when: Optional[str] = Field(None, description="The person's own words for the schedule, kept verbatim: "
                                                  "'every morning at 8', 'по будням в 9', 'jeden Montag um 7 Uhr'. "
                                                  "The hub reads these itself.")
    cron: Optional[str] = Field(None, description="Only when 'when' is not a simple phrase: a five-field cron "
                                                  "expression (minute hour day month weekday), at most every 5 minutes")
    timezone: Optional[str] = Field(None, description="IANA name such as Europe/Berlin; leave out for the server's")
    notify: Optional[List[str]] = Field(None, description="Where the result goes besides the inbox: telegram, "
                                                          "slack, discord, teams, mail or webhook. The assistant's "
                                                          "notifications are the inbox, the default.")
    agent_id: Optional[str] = Field(None, description="An existing agent that runs it, from hub_lookup. Leave out "
                                                      "and the pulse gets an agent of its own.")
    name: Optional[str] = Field(None, description="A short name for a new pulse agent")


def _where() -> dict:
    from common.attribution import launching_user
    from common.workspace_context import resolve_active_workspace
    return {"user_id": launching_user(), "workspace": resolve_active_workspace() or "default"}


def _plan(args: dict) -> dict:
    from proactive import from_phrase
    w = _where()
    return from_phrase.plan(
        brief=str(args.get("brief") or ""), when=str(args.get("when") or ""), cron=str(args.get("cron") or ""),
        timezone=str(args.get("timezone") or ""), notify=args.get("notify"), agent_id=str(args.get("agent_id") or ""),
        name=str(args.get("name") or ""), workspace=w["workspace"], user_id=w["user_id"])


@tool("schedule_pulse", args_schema=SchedulePulseInput)
def schedule_pulse(brief: str, when: Optional[str] = None, cron: Optional[str] = None,
                   timezone: Optional[str] = None, notify: Optional[List[str]] = None,
                   agent_id: Optional[str] = None, name: Optional[str] = None) -> str:
    """Turn a phrase like "every morning at 8 tell me the weather and my calendar"
    into a proactive agent that runs on that schedule. Pass the instruction in
    `brief` and the person's words for the time in `when`; the hub reads common
    English, Russian and German phrases itself, so only write a `cron` when it
    cannot. Say in one sentence what will be set up, then call it: the person
    answers a card showing the schedule in plain words and only then it is saved.
    Results go to the inbox unless `notify` adds telegram or another connected
    channel. Pausing, resuming or changing a pulse later is hub_action or its tab.
    """
    from proactive import from_phrase
    args = {"brief": brief, "when": when, "cron": cron, "timezone": timezone, "notify": notify,
            "agent_id": agent_id, "name": name}
    try:
        return json_ok(from_phrase.apply(_plan(args), user_id=_where()["user_id"]), default=str)
    except from_phrase.PulsePlanError as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"The pulse could not be set up: {exc}", code="internal")


def describe_call(tool_input: Any) -> str:
    """The approval card's sentence for one ``schedule_pulse`` call, or "" when
    the call cannot be planned (the call itself then says why)."""
    from proactive import from_phrase
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return ""
    if not isinstance(tool_input, dict):
        return ""
    try:
        return from_phrase.describe(_plan(tool_input))
    except Exception:  # noqa: BLE001 - the card falls back to the generic sentence
        return ""


PROACTIVE_SETUP_TOOLS = [schedule_pulse]

__all__ = ["PROACTIVE_SETUP_TOOLS", "describe_call", "schedule_pulse"]

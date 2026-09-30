# Handoffs

A handoff gives a conversation to another agent. The first agent ends its turn
with a short message, the agent it names answers the user directly in the same
turn, and the conversation stays with that agent afterwards: the next message
the user sends goes to it.

## Handoff or delegation

Both involve a second agent. The difference is who keeps the conversation.

| | Delegation | Handoff |
| --- | --- | --- |
| Tool | `run_agent_tool` (chat), `delegate_task_tool` (task) | `handoff_to_agent` (chat only) |
| Who answers the user | The first agent, using the other agent's result | The other agent, directly |
| Who the next turn goes to | The first agent | The other agent |
| What the other agent sees | Only the input it is given | The conversation, through a history filter |

Delegate when you need a piece of work done and want to keep the conversation:
"look up the invoice", "draft the summary". Hand over when the rest of the
conversation belongs with another role: a front desk agent that routes billing
questions to a billing agent, a triage agent that passes a bug report to the
engineer agent.

## Setting it up

On the agent's page, **Tools** tab, the **Handoffs** card:

- **May hand over to.** The agents this agent may give the conversation to.
  With none selected the agent has no handoff tool at all. Unlike the
  delegation allowlist, an empty list means nobody, not everybody.
- **History the receiving agent sees.** The default filter (below). A single
  handoff may choose less than this, never more.

The same settings are the `handoffs` and `handoff_history` fields of the agent
record, saved through `GET` / `POST /api/agents/{id}/handoffs` (and accepted by
`POST /api/agents/create`). They are part of the agent's version history like
its tools.

An agent that has targets gets the `handoff_to_agent` tool and a short section
in its system prompt. The tool is not in the tool catalog and is never picked
by hand: the Tools tab lists it under "Added automatically" once targets exist. The tool's description lists each target with its name and
description, so the model can choose without looking anything up. The tool
takes `agent_id`, a `reason` the user sees, an optional `note` only the
receiving agent sees, and an optional narrower `history`.

## History filters

| Filter | The receiving agent gets |
| --- | --- |
| `full` | Every earlier turn, plus what the agents before it said in this turn |
| `summary` | One summary message of the conversation |
| `last_n:<N>` | The last N messages (N from 1 to 200) |
| `none` | Only the user's latest message |

From widest to narrowest: `full`, `summary`, `last_n`, `none`; a smaller N is
narrower than a larger one. Every filter comes with a handoff note in front of
the user's message: who handed over and why, the handing agent's note, and what
history was passed on. The user's attachments, referenced entities and project
scope reach the receiving agent exactly as they reached the first one.

The summary is written by the compaction summariser on the handing agent's
model, while its run is still going, so its tokens are counted on that run
(`loop.aux_calls`, purpose `handoff_summary`) and against its money cap. Only
`full` also reads the summary compaction keeps for a long conversation; the
narrower filters would otherwise get the whole conversation back through it.

## Limits

- A handoff happens only in a conversation: the web chat, the blocking
  `/api/chat/message` endpoint, the terminal client, Telegram, an instance
  inbox, the widget and agents on `/v1`. In a task run, a scheduled run or an
  evaluation the tool answers that a handoff needs a conversation and points at
  `delegate_task_tool`.
- One turn hands over at most `AGENTS_HUB_HANDOFF_MAX_DEPTH` times (3 by
  default).
- The conversation never goes back to an agent that already held the turn, so
  two agents cannot pass the user back and forth. A refused handoff is an
  ordinary tool answer: the agent answers itself or picks another target.
- The target must exist and be available in the workspace, and it must be on the
  agent's list.
- The workspace's hard budget cap is checked before the receiving agent starts,
  as for any run.
- An imported (remote) agent has no handoff tool, though it can receive one.

## What happens in a turn

1. The first agent calls `handoff_to_agent`. Its turn ends at once: the run's
   answer is what it told the user alongside the call, or the reason when it said
   nothing. The run completes normally and records `handoff` (target, reason,
   note, filter, the next run's id).
2. The receiving agent gets a run of its own: same conversation and session,
   `parent_run_id` set to the first run, `handoff_from` saying where it came
   from. Each run keeps its own cost.
3. The receiving agent may hand over again, within the limits above.

The run page links both ways: "Handed over to" on the first run, "Received by
handoff from" on the second.

## Events

The streaming endpoints (`/api/chat/stream`, `/api/chat/stream-sse`) keep one
stream per turn:

```text
meta           {run_id, session_id, agent_id}          first agent
token, tool_start, tool_end, ...                        first agent
handoff        {from_agent_id, from_agent_name, to_agent_id, to_agent_name,
                reason, history_filter, run_id, next_run_id, from_response,
                usage, tool_calls, duration_ms}
meta           {run_id, session_id, agent_id}          receiving agent
token, tool_start, tool_end, ...                        receiving agent
done           {ok, response, run_id, agent_id, handoff, handoffs, ...}
```

There is exactly one `done` per turn. It carries `agent_id` (the agent that
answered), `handoff` (the last handoff's fields) and `handoffs` (all of them).
A surface that ignores `handoff` still gets a well-formed turn: the final
`done` holds the answer.

The blocking `POST /api/chat/message` returns `{response, ok, run_id}` as
before, plus `agent_id`, `handoff` and `handoffs` (each with the handing
agent's `from_response`) when the conversation changed hands.

## Surfaces

- **Web chat.** The first agent's bubble ends with its message, a divider says
  "Handed over to Billing: invoice question", and the receiving agent's bubble
  streams below with its name. After the turn the top bar shows the new agent
  and the conversation is saved with it. A second tab following the
  conversation shows the same.
- **Terminal client.** `ah chat` prints each handing reply and who took
  over, and the session continues with the new agent.
- **Telegram.** The handing agent's reply goes out first with a line naming
  who took over, then the answer; the chat's binding follows the handoff.

Related: [chat](chat.md), [agents](agents.md), [agent loop](agent-loop.md).

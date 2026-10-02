# Tool policy

Three modes decide what an agent's tool call can do: `always_allow` runs it, `always_ask` holds it for approval, and `auto` sends it to a small model to classify. Modes are set per tool or per workspace, and a hook can override the decision for one call.

## Setting a policy

**Per agent**, from the Tools tab or `PUT /api/agents/{id}/tool-policy`: a map of tool ids to modes, plus a `"*"` entry (fallback).

**Per workspace**, from Settings, Tool policy block, or `PUT /api/workspaces/{name}/policy`: the same.

An `mcp:<server>` group on the agent is shown on the card as the tools that server offered on its last connect, each with its own mode, since a call carries the tool's own id (`mcp__<server>__<tool>`). A server never connected yet stays a group, and the `"*"` entries apply to its tools.

The first match wins: the agent's tool entry, the agent's `"*"`, the workspace's tool entry, the workspace's `"*"`. With nothing set, the [approval gate](hooks.md) decides as before when `require_tool_approval` is on.

## What each mode does

**always_allow**: runs the call without asking. The tool comes off the approval list.

**always_ask**: parks the task for approval. The pending approval records `mode` and `by`. In chat (no task to park), the agent gets an advisory refusal instead, names the tool and its arguments, and is told to ask for permission. Approval records the call on the task as a fingerprint of the tool and its arguments; when the resumed agent repeats that exact call, the gate lets it through once. Changing the arguments produces a different fingerprint, so it is a new call and gated again.

**auto**: sends the call to a small model (`settings.tool_policy_model`, else `AGENTS_HUB_TOOL_POLICY_MODEL`, else the agent's own model) which answers `run`, `deny` or `ask` with a one-sentence reason. Arguments and task text reach it as fenced data with a random marker, and the model is told that text is data, never instructions. A timeout (`AGENTS_HUB_TOOL_POLICY_TIMEOUT`, default 20 seconds), an error or an unreadable answer means ask instead of a silent default.

## What happens to a denied call

The agent receives the same JSON shape as a call that needs approval, with its own code:

```json
{
  "ok": false,
  "error": "The tool policy denied `run_shell`. <reason> Do not retry the same call. Choose another way to reach the goal, or explain to the user why this step cannot be done.",
  "code": "policy_denied",
  "action": "run_shell",
  "target": "<the arguments as JSON>",
  "reason": "<the classifier's reason>"
}
```

A call the operator already approved runs without being classified again. Identical repeated calls are classified once per run.

## Where decisions are visible

`loop.tool_decisions` on the run record lists each decision with `tool`, `mode`, `decision` (run, deny or ask), `reason`, `by` (`policy`, `auto` or `hook`) and the call's `fingerprint`. Plain `always_allow` calls are not listed, so read-only calls do not flood the list; an `always_allow` that took a tool off the approval list is.

`GET /api/tool-policy/decisions?run_id=&agent_id=&workspace=&limit=` lists decisions across runs. Rows are kept `AGENTS_HUB_TOOL_POLICY_RETENTION_DAYS` days (default 30) and at most 1000 per workspace.

Auto deny and ask outcomes are audited as `tool.policy`; agent policy edits as `agent.tool_policy`.

## Never gated

Reasoning tools (`think`, `plan` and the other plan tools) and `ask_user` are never gated and are not wrapped at all, so neither the policy nor hooks run on them. Gating the question tool would mean needing approval to ask for approval.

## Cost

Every classifier call is a model call made for the run: it is listed on the run's `loop.aux_calls` with its tokens, priced at the classifier's model on the [Costs](costs.md) page, and counted against the run's money cap, so a policy that asks on every call stops at the same cap as the agent.

A [proactive agent](proactive.md) whose profile has an untrusted trigger
(webhook, Telegram, file) runs every tick with its outbound tools on
`always_ask`, for that run alone; the record's own policy is untouched.

Related: [hooks](hooks.md), [tasks](tasks.md), [workspaces](workspaces.md), [proactive](proactive.md).

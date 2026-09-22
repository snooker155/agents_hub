# Hooks and tool approval

Two ways to put a human, or the operator's own code, in front of a single tool
call: **hooks** run around every call, and the **approval gate** holds a call
until a person says yes.

Both are off until you configure them. An installation that configures neither
behaves exactly as it did before: the only approval check is the advisory one
some tools have always carried (see the end of this page).

## In the dashboard

**Settings → Tool policy** is where you configure both. The approval gate toggle (`require_tool_approval`) switches the gate on for a workspace, and the hooks editor below it holds the JSON-formatted hook configuration. Edits reach the agent process immediately, so a change applies to the next run without a restart.

## Hooks

Hooks are configured per workspace, in `<workspace>/.hooks.json` or under a
`hooks` key in the workspace metadata. The metadata wins when both exist, so a
centrally managed hook set is not overridden by a file inside a workspace an
agent can write to.

```json
{
  "PreToolUse": [
    {"matcher": "run_shell|delete_file", "type": "command",
     "command": "./scripts/check_tool_call.py", "timeout": 10},
    {"matcher": "apply_unified_diff", "type": "http",
     "url": "http://localhost:9000/review", "fail_closed": true}
  ],
  "PostToolUse": [
    {"matcher": ".*", "type": "command", "command": "./scripts/audit.sh"}
  ]
}
```

- `matcher` is a regular expression matched against the whole tool id, so
  `run_shell|delete_file` means those two tools and not every tool whose name
  contains them. Leave it out (or use `*`) to match every tool. An invalid
  pattern matches nothing, and is logged.
- `type` is `command` (default) or `http`.
- `timeout` is in seconds, 10 by default.
- `fail_closed` decides what happens when the hook itself breaks, see below.

Hooks apply to action tools. The reasoning scratchpad (`think`, `plan`, and
friends) and `ask_user` are never wrapped: they have no effect outside the run,
and gating the question tool would mean needing approval to ask for approval.

The hook configuration is read once per agent build, so a change applies to the
next run, the same as every other agent setting.

### The payload

Every hook receives the same JSON object, on stdin for a command hook and as the
body of a POST for an HTTP one:

```json
{
  "hook": "PreToolUse",
  "agent_id": "swe_agent",
  "run_id": "3f0c…",
  "task_id": "9b21…",
  "workspace": "acme",
  "tool": "run_shell",
  "input": {"command": "rm -rf build"}
}
```

`task_id` is empty in chat, where there is no task. `PostToolUse` adds an
`output` field holding what the tool returned.

### What a command hook's exit code means

| Exit code | Meaning |
| --- | --- |
| `0` | Allow the call. |
| `2` | Deny it. The hook's **stderr** goes back to the agent as the tool's output, so write the reason there. |
| anything else | Logged, and the call is allowed. |

Failing open on an unexpected exit code (or a timeout, or a hook that cannot be
spawned at all) is deliberate: a hook that cannot run is an infrastructure
problem, and an unreachable linter must not silently freeze every agent in the
workspace. Set `"fail_closed": true` on a hook whose failure should stop the
call instead.

A command hook runs through the shell, with the workspace folder as its working
directory, so `./scripts/check.sh` means the same thing to the hook as it does
to the agent.

### What an HTTP hook answers

```json
{"decision": "allow", "reason": "..."}
```

`decision` is `allow`, `deny` or `ask`. `ask` requires approval for this one
call even when the tool is not on the approval list, which is how a hook says
"this particular command looks dangerous" rather than a blanket rule about the
tool. `reason` is what the agent (or the person approving) reads.

A `PostToolUse` HTTP hook may also return `{"output": "..."}` to replace the text
the agent reads, which is how a redaction or summarisation hook earns its place.
Command hooks cannot rewrite output: their stdout is a log line, and treating it
as tool output would make every `echo` in an audit script silently overwrite a
result. A `PostToolUse` hook can never stop the call, because it has already
happened; a deny there is only logged.

### Security

Hook processes run with the same scrubbed environment an agent's shell command
gets: every `*_API_KEY`, `*_SECRET`, `*_TOKEN` and `*_PASSWORD` variable is
removed before the hook starts. A hook is operator code, but it is spawned
inside an agent run, and it gets what anything else spawned inside an agent run
gets. A hook that needs a credential should read it from a file it owns.

Hooks are run by the agent process, so they execute with that process's
permissions. Treat the hook configuration as code: anyone who can write
`.hooks.json` in a workspace can run commands as the agent.

## The approval gate

The gate holds a tool call until a person decides. It is opt-in per workspace:

```json
{"settings": {"require_tool_approval": true}}
```

With it off (the default), only a hook's `ask` decision can require approval.
With it on, these tools need a yes every time: `run_shell`, `delete_file`,
`apply_unified_diff`, the `delete_*` entity tools, `remove_eval_case_tool`,
`stop_run`, `stop_node`, `restart_node`, `stop_container` and `prune_run_logs`.
Every one of them ends something, drops something, or writes over something a
person may not get back.

An agent record can adjust the list for itself: `approval_tools` adds tool ids,
`approval_exempt` removes them, and the exemption wins. That lets one agent be
trusted with a gated tool without turning the gate off for everybody.

### In a task

The agent stops. The task moves to `awaiting_approval` with the pending call on
it (tool, arguments, reason, the run and agent it came from), the run closes
normally, and a notification goes to the dashboard. Nothing is finalized: a
session continuation waiting on the task stays pending.

The task page then shows the call, pretty printed, with **Approve** and **Deny**
and a field for a note. Both answers re-run the agent with a resume instruction,
the same way answering a question does.

Approving also records the call on the task as a fingerprint of the tool *and
its arguments*. When the resumed agent repeats exactly that call, the gate spends
the entry and lets it through, once. This is why approving is not just a message
in the prompt: told only that it may proceed, the agent would be stopped again;
and an approval that covered the whole tool would let the resumed run do more
than you agreed to. Changing the arguments produces a different fingerprint, so
it is a new call and it is gated again.

Denying resumes the agent with the refusal, your note, and an instruction to
continue another way or explain why it cannot.

### In chat

There is no task to park and nobody to answer a parked call, so the gate refuses
the call and tells the agent to say exactly what it wanted to run and wait. That
is advisory: it stops the call, but the conversation, not the system, carries the
decision.

## What stays advisory

`tools/service_ops.py` and the entity run tools have always refused destructive
calls with an `approval_required` error that names what would be destroyed, and
let the agent proceed once it calls again with `user_approved=True`. That
convention is unchanged and is what agents are prompted for; the gate reuses the
same message shape rather than inventing a second one. The difference is that
the gate enforces, and that convention asks.

Run *assignment* approval (a task waiting for you to approve which agent should
work it) is a different thing with a similar name: it is about who runs, not
about what a running agent may call.

Related: [tools-and-capabilities](tools-and-capabilities.md),
[tasks](tasks.md), [workspaces](workspaces.md), [settings](settings.md).

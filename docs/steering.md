# Steering

A person can talk to a run while it works: inject a message the agent reads before its next model step, interrupt the run and start again with the message, or, as the run's operator, add to the agent's instructions for the rest of the run. In chat a message can also wait for the turn to end.

## The API

`POST /api/runs/{run_id}/steer` with `{message, mode}`, mode `inject` (default), `interrupt` or `system`. Accepted only while the run's status is `running`, otherwise 409 with the current status. From the command line: `ah api post /api/runs/<run_id>/steer --json '{"message": "...", "mode": "system"}'`.

`run_id` may also be a team run id (see In the chat composer). `GET /api/runs/{run_id}/steer` lists messages with status `pending`, `delivered`, `expired`, `interrupted` or `failed`.

## Inject

Stored in the `run_steering` table. The agent loop checks before every model call and places the message after the tool results the run had at that moment, headed "[Message from the user, sent while you were working]". It stays there on every later model call in that run.

A message that arrives while a task run writes its final answer is not lost: when the loop has answered, the run takes it and makes one more pass, in which the model sees its own answer and then the message, up to two such passes. A chat turn leaves it to the chat, which sends it as the next turn.

A message written on the Instances page to a copy that is busy with a task run goes to that run the same way, and waits in the copy's inbox as well. Whichever takes it first answers it: the run before its next model step, or the copy once it is free if the run ended first (see [instances](instances.md)).

Remote agents cannot take injects (409).

## System message

Mode `system` is a privileged addition to the agent's instructions. The loop takes it before the next model call like an inject, but appends it to the system prompt, under "Operator instructions added during this run", for every later model call of the run, where it carries the authority of the rest of the instructions. The append form is used for every provider: Anthropic and Gemini accept one system instruction ahead of the conversation and no system message in the middle of it, OpenAI and the local servers accept either. With a cached system prompt (Anthropic), the addition is one more text block after the cached ones, so the cached prefix stays valid.

Only an operator may send one: the run's owner (the owner of the chat, or the person who filed the task) or an admin; outside `multi` mode, the one operator. The service credential that a run's own process and every delegated run carry is always refused (403), so no agent can raise its own instructions. A run with no known owner (a delegated run, a flow node) takes system messages from admins only. Teams and remote agents take none (400 and 409).

It is stored with the other steering messages, so a run that picks up again under its own id (a checkpoint resume, a chat retry) puts it back in the system prompt. It shows on the run page under "Instructions added during the run" (`loop.system_messages`), and in the stream as `steer_delivered` with `mode: "system"`. One that arrives while a task run writes its final answer gets one more pass, in which the model checks its answer against it. One that no run took is marked expired and is never sent as a chat turn.

## Interrupt

Stops the run like the Stop button. A task is relaunched on the same agent with the message at the end of the run's instruction, pending orchestrator continuations move to the new run, and a `steer_interrupt` activity entry names both runs. The API response carries `next: "relaunched"` and `next_run_id`. In chat, the turn answers `next: "send"` and the chat sends the message as its next turn; with `send: true` (what the run page asks for, since it does not own the conversation) the server starts that next turn itself and answers `next: "sent"` with the conversation id. Everyone with the conversation open sees the new turn arrive.

## In the chat composer

Open during a turn, offering **Steer**, **Interrupt**, **Queue** and **Instruction** (a system message; not for teams). The choice is remembered per user (preference `steer_mode`), except Instruction, which is picked each time:

- Steered messages show as user bubbles with "waiting for the next step", "delivered at step N", or "queued".
- A message the turn did not take comes back in the done event (`undelivered`) and is sent as the next turn together with the queue.
- Pressing Stop puts waiting text back in the input box.

**Flows** are steered through the node running now: each node is a run of its own, a message it did not take goes on to the node that starts next, and whatever no node took comes back as the next turn. Interrupting a node stops the flow. **Teams** are steered as a whole, by their team run id: the message goes on the team's board as a message from `user` to everyone, and the next member whose prompt is built reads it. Interrupting a team stops it. For both, the done event lists which messages were `delivered` and which were not, so none is sent twice.

## On pages

**Task and run pages**: the live output panel has a Steer/Interrupt/Instruction box for a running run, with a list of what was sent and each message's state. Interrupting a chat turn from the run page stops the turn and sends the message as the conversation's next turn.

## Access and audit

Access: see the run's workspace; in multi mode also the editor role. Each message is audited as `run.steer`.

## Containers and remote agents

A run in a container claims its messages over the run-state API (`POST /api/run-state/runs/{id}/steering/claim`) with a 3 second timeout, and stops checking after three failed calls in a row. Remote agents have no loop to read an inject, so an inject to one is refused with 409; an interrupt stops it and, for a task, relaunches the task's agent.

Related: [tasks](tasks.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md).

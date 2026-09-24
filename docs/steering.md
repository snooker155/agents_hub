# Steering

A person can talk to a run while it works: inject a message the agent reads before its next model step, or interrupt the run and start again with the message. In chat a message can also wait for the turn to end.

## The API

`POST /api/runs/{run_id}/steer` with `{message, mode}`, mode `inject` (default) or `interrupt`. Accepted only while the run's status is `running`, otherwise 409 with the current status.

`GET /api/runs/{run_id}/steer` lists messages with status `pending`, `delivered`, `expired`, `interrupted` or `failed`.

## Inject

Stored in the `run_steering` table. The agent loop checks before every model call and places the message after the tool results the run had at that moment, headed "[Message from the user, sent while you were working]". It stays there on every later model call in that run.

Remote agents cannot take injects (409).

## Interrupt

Stops the run like the Stop button. A task is relaunched on the same agent with the message at the end of the run's instruction, pending orchestrator continuations move to the new run, and a `steer_interrupt` activity entry names both runs. The API response carries `next: "relaunched"` and `next_run_id`. In chat, the turn answers `next: "send"` and the chat sends the message as its next turn.

## In the chat composer

Open during a turn, offering **Steer**, **Interrupt** and **Queue**. Remembered per user (preference `steer_mode`):

- Steered messages show as user bubbles with "waiting for the next step", "delivered at step N", or "queued".
- A message the turn did not take comes back in the done event (`undelivered`) and is sent as the next turn together with the queue.
- Pressing Stop puts waiting text back in the input box.

Flows and teams in chat can only queue.

## On pages

**Task and run pages**: the live output panel has a Steer/Interrupt box for a running run, with a list of what was sent and each message's state. Interrupting a chat turn from the run page stops the turn; send the message from the chat itself.

## Access and audit

Access: see the run's workspace; in multi mode also the editor role. Each message is audited as `run.steer`.

## Containers and remote agents

A run in a container claims its messages over the run-state API (`POST /api/run-state/runs/{id}/steering/claim`) with a 3 second timeout, and stops checking after three failed calls in a row. Remote agents have no loop to read an inject, so an inject to one is refused with 409; an interrupt stops it and, for a task, relaunches the task's agent.

## Gaps

An inject that arrives while a task run writes its final answer expires ("Not delivered: the run ended first").

Related: [tasks](tasks.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md).

# Steering

A person can talk to a run while it works: inject a message the agent reads before its next model step, interrupt the run and start again with the message, as the run's operator add to the agent's instructions for the rest of the run, or move the run to another model. In chat a message can also wait for the turn to end.

## The API

`POST /api/runs/{run_id}/steer` with `{message, mode}`, mode `inject` (default), `interrupt`, `system` or `switch_model`. Accepted only while the run's status is `running`, otherwise 409 with the current status. From the command line: `ah api post /api/runs/<run_id>/steer --json '{"message": "...", "mode": "system"}'`.

`run_id` may also be a team run id (see In the chat composer). `GET /api/runs/{run_id}/steer` lists messages with status `pending`, `delivered`, `expired`, `interrupted` or `failed`.

## Inject

Stored in the `run_steering` table. The agent loop checks before every model call and places the message after the tool results the run had at that moment, headed "[Message from the user, sent while you were working]". It stays there on every later model call in that run.

A message that arrives while a task run writes its final answer is not lost: when the loop has answered, the run takes it and makes one more pass, in which the model sees its own answer and then the message, up to two such passes. A chat turn leaves it to the chat, which sends it as the next turn.

A message written on the Instances page to a copy that is busy with a task run goes to that run the same way, and waits in the copy's inbox as well. Whichever takes it first answers it: the run before its next model step, or the copy once it is free if the run ended first (see [instances](instances.md)).

Remote agents cannot take injects (409).

## System message

Mode `system` is a privileged addition to the agent's instructions. The loop takes it before the next model call like an inject, but appends it to the system prompt, under "Operator instructions added during this run", for every later model call of the run, where it carries the authority of the rest of the instructions. The append form is used for every provider: Anthropic and Gemini accept one system instruction ahead of the conversation and no system message in the middle of it, OpenAI and the local servers accept either. With a cached system prompt (Anthropic), the addition is one more text block after the cached ones, so the cached prefix stays valid.

Only an operator may send one: the run's owner (the owner of the chat, or the person who filed the task) or an admin; outside `multi` mode, the one operator. A run's own credential (the run token its process and every delegated run carry, which reaches only the relay routes) and the service credential are always refused (403), so no agent can raise its own instructions. A run with no known owner (a delegated run, a flow node) takes system messages from admins only. Teams and remote agents take none (400 and 409).

It is stored with the other steering messages, so a run that picks up again under its own id (a checkpoint resume, a chat retry) puts it back in the system prompt. It shows on the run page under "Instructions added during the run" (`loop.system_messages`), and in the stream as `steer_delivered` with `mode: "system"`. One that arrives while a task run writes its final answer gets one more pass, in which the model checks its answer against it. One that no run took is marked expired and is never sent as a chat turn.

## Model switch

Mode `switch_model` moves a running run to another model. The message is a catalog model id, `provider/model` (a bare model id is accepted when one enabled provider serves it); the route checks it against the enabled models of the Models page, answers 400 otherwise, and stores it in its `provider/model` form. From the command line: `ah api post /api/runs/<run_id>/steer --json '{"message": "openai/gpt-5", "mode": "switch_model"}'`.

The loop takes it before the next model call like an inject and, from that call on, runs the agent on the new model: the same tools bound the same way (as a fallback model is bound), the same sampling settings (temperature, output limit, streaming, reasoning level; not the agent's own key or base URL, which belong to its own provider), and the whole trail so far. The history is `langchain_core` messages, which every model driver converts to its own shape, so a run can move between providers: an Anthropic tool call becomes an OpenAI one and back. What one provider adds and another refuses is taken out of the prompt for the new model (Anthropic's cache markers on the system prompt). The latest switch wins; a fallback model list, if the agent has one, still stands behind whichever model is current.

A switch that cannot be honoured when the loop takes it (the model was disabled meanwhile, or it does not build) is recorded with its error and the run stays on the model it had. One that arrives while a task run writes its final answer is recorded and makes no extra pass of its own; one no run took expires and is never sent as a chat turn.

The run records each switch on `loop.model_switches` (step, from, to, who, error), and each call the new model answered on `loop.answered_by` with its own tokens and catalog model, the way a fallback's call is recorded, so the run's cost prices those calls at the new model's rates and the rest at the run's own model. The run page lists the switches and marks those calls "switched". A run that picks up again under its own id (a checkpoint resume) reads the switch back and stays on the new model.

Anyone who may steer the run may switch it; a run's own token and the service credential (an agent's own process, a delegated run) are refused with 403, so no agent moves its own run to another model. Teams take none (400), remote agents none (409).

## Interrupt

Stops the run like the Stop button. A task is relaunched on the same agent with the message at the end of the run's instruction, pending orchestrator continuations move to the new run, and a `steer_interrupt` activity entry names both runs. The API response carries `next: "relaunched"` and `next_run_id`. In chat, the turn answers `next: "send"` and the chat sends the message as its next turn; with `send: true` (what the run page asks for, since it does not own the conversation) the server starts that next turn itself and answers `next: "sent"` with the conversation id. Everyone with the conversation open sees the new turn arrive.

## In the chat composer

Open during a turn, offering **Steer**, **Interrupt**, **Queue** and **Instruction** (a system message; not for teams). The choice is remembered per user (preference `steer_mode`), except Instruction, which is picked each time:

- Steered messages show as user bubbles with "waiting for the next step", "delivered at step N", or "queued".
- A message the turn did not take comes back in the done event (`undelivered`) and is sent as the next turn together with the queue.
- Pressing Stop puts waiting text back in the input box.

**Flows** are steered through the node running now: each node is a run of its own, a message it did not take goes on to the node that starts next, and whatever no node took comes back as the next turn. Interrupting a node stops the flow. **Teams** are steered as a whole, by their team run id: the message goes on the team's board as a message from `user` to everyone, and the next member whose prompt is built reads it. Interrupting a team stops it. For both, the done event lists which messages were `delivered` and which were not, so none is sent twice.

## On pages

**Task and run pages**: the live output panel has a Steer/Interrupt/Instruction/Model box for a running run, with a list of what was sent and each message's state. Model shows a picker of the enabled catalog models instead of a text box. Interrupting a chat turn from the run page stops the turn and sends the message as the conversation's next turn.

## Access and audit

Access: see the run's workspace; in multi mode also the editor role. Each message is audited as `run.steer`.

## Containers and remote agents

A run in a container claims its messages over the run-state API (`POST /api/run-state/runs/{id}/steering/claim`) with a 3 second timeout, and stops checking after three failed calls in a row. Remote agents have no loop to read an inject, so an inject to one is refused with 409; an interrupt stops it and, for a task, relaunches the task's agent.

Related: [tasks](tasks.md), [sessions-and-runs](sessions-and-runs.md), [containers](containers.md).

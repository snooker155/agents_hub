# Tasks

A task is tracked work: it has a status, an owner, subtasks, dependencies, and a
result that outlives the conversation that produced it.

Use a task when the work matters beyond this turn. Use [chat](chat.md) when it
does not.

## Lifecycle

```
created → assigned → running → done
                  ↘ blocked ↗
                  ↘ failed
```

- **Assign** picks the agent. The orchestrator can do this for you, and its
  reasoning is recorded in the routing log.
- **Start** launches the run. Assignment alone does nothing.
- **Block** records why work cannot proceed, with a reason, rather than failing
  silently.
- **Dependencies** hold a task until the ones it names are done. A sequence is
  just dependencies laid out in a line.
- **Pausing** is not failing. A task waits in `awaiting_input` when its agent
  asked you a question, and in `awaiting_approval` when it stopped in front of a
  tool call that needs your yes (see [hooks](hooks.md)). Both are answered from
  the task page, and both resume the agent where it left off.

## Subtasks and decomposition

The **Decomposer** breaks a complex task into actionable subtasks, optionally in
a fixed order. It only acts on tasks a person created, so an agent cannot spawn
unbounded work by decomposing its own output.

## Results

`get_task_result` returns what the agent produced. That is the handoff point
between agents: a reviewer reads the developer's result rather than re-reading
the conversation.

## Gotchas

- A task with an agent assigned but never started sits forever. Assignment is
  not a launch.
- Taskless delegation (`run_agent_tool`) is refused inside a task context on
  purpose: tracked work goes through assign and start, so it stays visible.
- A task's workspace is fixed at creation and decides where its files land.

Related: [projects](projects.md), [agents](agents.md), [hooks](hooks.md), [sessions-and-runs](sessions-and-runs.md).

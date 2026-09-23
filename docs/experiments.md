# Experiments

An experiment runs two stored versions of one agent's definition side by
side, each on its share of the agent's runs, so you can compare them on real
traffic instead of on a handful of hand-picked prompts.

## What an experiment is

Every change to an agent's tools, model settings or markdown is snapshotted in
its version history (Config tab, **Version History**). An experiment names two
or more of those stored versions, called arms, and the share of runs each
gets:

```json
{
  "enabled": true,
  "arms": [{"version": 4, "share": 0.8}, {"version": "current", "share": 0.2}],
  "note": "shorter instructions"
}
```

`PUT /api/agents/{agent_id}/experiment` starts it. Shares must sum to 1, every
arm must name a different version, and every version must exist. `"current"`
means the definition that runs now: when history does not hold it yet (history
records a state just before it is replaced), it is snapshotted first, so the
arm always points at a fixed row.

An agent has at most one open experiment. Sending the same arms again only
changes `enabled` (pause and resume) and `note`. Sending different arms ends
the open experiment and starts a new one, so a report never mixes two
layouts. `GET` returns the open experiment, or the last ended one, and
`DELETE` ends it.

On the agent's Config tab, the **Experiment** card below the version history
does the same: pick version A, its share, version B, start, pause, stop.

## How routing works

The arm is chosen when the run's record is opened (`open_run`), which every
run path does before it builds the agent: task runs, chat turns, entity chats,
flow nodes, node runs and delegated runs. The choice is a weighted,
deterministic pick on a routing key:

- a chat turn uses its conversation id, so a whole conversation stays on one
  arm;
- every other run uses its run id.

The same key always lands on the same arm for a given experiment, on every
replica. Eval and replay runs are never routed, and neither is any run while
the experiment is paused.

The factory then builds the agent from the arm's snapshot: its instructions,
capabilities and usage markdown, its tools, model and provider settings,
temperature, reasoning, response format and the other behaviour fields stored
with the version. The version is part of the build cache key, so two arms
never share a cached agent. The workspace's own instructions, model override
and memory pool binding still apply the way they do to any run.

When the factory builds from an arm it records the run in
`experiment_assignments` and stamps the arm's definition hash on the run, so
the run's history says what actually ran. If the arm's version row has been
removed, the run builds from the current definition, the fallback is logged
and no assignment is recorded.

A run container that reaches its run record over HTTP
(`AGENT_RUN_STATE_TRANSPORT=http`) cannot read the experiment. It builds the
current definition and, because nothing is recorded, it is left out of the
report rather than counted in an arm it did not use.

## Reading the report

`GET /api/agents/{agent_id}/experiment/report`, and the table on the card,
give one row per arm:

- **Runs**, **Completed**, **Failed**: runs assigned to the arm and how they
  ended. Runs still going count in Runs only.
- **Mean cost**, **Mean tokens**, **Mean duration**: from the run records,
  priced the way the Costs page prices them.
- **Mean score**, **Pass rate**: from [online evals](evals.md#online-evals)
  of the arm's runs. With no `online_eval` rule on the agent these stay empty,
  so add one before starting an experiment whose point is quality.

Shares are targets, not guarantees: with few runs, or a few long chat
conversations, the split can drift. Wait until each arm has enough graded runs
before reading much into a difference in mean score.

## Rollback and new versions during an experiment

Versions are never rewritten. Saving the agent while an experiment runs adds a
new version to the history but does not change the arms: both arms keep
building from their own snapshots, and the edit only affects runs outside the
experiment once it ends. Rolling back works the same way: it changes the
current definition, not the arms. To test the edit, stop the experiment and
start a new one that includes the new version.

Ending an experiment sends every run back to the current definition. Nothing
about the winning arm is applied automatically: roll back to it from the
version history if it is not already current.

Related: [evals](evals.md), [agents](agents.md), [notifications](notifications.md).

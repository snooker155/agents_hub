# Playground: worlds and scenarios

The playground drops agents into a simulated world and runs it tick by tick, so
you can watch behaviour rather than read about it.

Two objects, deliberately separate:

- A **world** is the place: its environment, objects, rules and starting state.
- A **scenario** is the cast and the plot: which agents play which roles in that
  world, and what they are trying to do.

The World Builder never casts an agent; the Scenario Creator never invents a
room. Each has its own chat on its own page.

## How a run works

Every tick, every role acts. That is the cost model: a scenario run is roles
times ticks of model calls, which is why `run_scenario_tool` refuses until you
have approved and shows the estimate first.

`POST /api/playground/scenarios/{id}/run` returns at once with a `pending`
record; the ticks run in their own process, polling and stamping a heartbeat
every 15 seconds. After every tick a checkpoint is written with the tick number,
a snapshot of the environment, history, carry-forward state, streaks, and spend.

Environment snapshots are a generic default (pickled snapshot of the environment
state), or custom per environment class; the checkpoint is JSON. Decisions have
deterministic run ids (uuid5 of scenario run id, tick and agent) and carry
`parent_run_id`.

`POST /api/playground/runs/{id}/resume` relaunches a `stopped` or `failed` run
from its checkpoint (400 when nothing to resume). Ticks already recorded in
`sim_ticks` are never re-run.

Triggers (`POST /runs/{id}/trigger`) are now durable: kept on the run record
and drained at the top of every tick, polled every few seconds while the world
is idle, so they reach a run in any process.

Runs stream as they go, and produce a replayable record: what each role did on
each tick, and why. Resume button on the scenario page shows host, heartbeat age
and resume attempts.

## Designing one

1. Build or pick the world. Validate it — a world can be checked before anything
   runs.
2. Create the scenario against that world: roles, the agents behind them, the
   goal, and the tick limit.
3. Validate, then run with approval.

## Roles: personas or agents

A scenario's `mode` decides who plays each role, and it is a scenario-wide
setting, not a per-role one.

- **personas** (the default) plays every role with a bare chat model: a
  system prompt built from the role's goal, private knowledge and the
  environment's action API, and nothing else. No hub tools at all, only the
  environment's own actions. This is the original behaviour and the safe
  default for a scenario you have not reviewed.
- **agents** plays every role with the real agent behind its `role.agent_id`,
  built through `agents.agent_factory.create_agent` exactly as any agent
  build is, capability guard included. The agent's own persona
  (`instructions.md`) stays intact; the scenario's system and tick prompt are
  handed to it as the turn's instruction, and its final answer still has to
  parse as an action (`parse_decision`), the same contract personas mode
  holds.

Three things keep agents mode from being "give every role a shell":

- **The tool set is small and environment-declared.** Each `Environment`
  class carries its own `TOOL_ALLOWLIST`, empty by default: a role gets no
  hub tools unless the environment says so, and the shipped environments
  declare only a handful (`calculator`, and `read_file` for the environments
  whose characters plausibly consult a document). A role never gets
  filesystem writes, the web, or another agent this way.
- **A per-tick call limit.** `Scenario.max_tool_calls_per_tick` (default 8)
  caps how many tool calls one role's decision may make in a single tick; a
  decision that exceeds it is cut off and recorded with an error, not left to
  run away.
- **Docker is required.** A scenario in agents mode refuses to run at all
  unless its workspace's execution mode resolves to docker
  (`runtime.entity_launch.execution_mode_for`). Even a tiny tool allowlist is
  a real capability grant, and it is only safe inside the same container
  isolation every other agent build relies on.

## The task this scenario works on

`Scenario.task_id` binds a scenario to a task: when set, every run the
scenario launches is pointed at that task (`playground.launcher.start_scenario_run`
assigns the run to it, `tasks.service.assign_executor`, the same way
`loops.launcher.start_loop_run` does), and when the run ends
`playground.runner._finalize_task` writes a short summary as the task's
result (`tasks.context.persist_task_result`) and moves the task on through
`managers.runs.task_finalize.finalize_task`, the same state machine every
other kind of run advances the task through.

## Documents

`Scenario.documents` is reference material for the cast: a list where each
entry is either `{"name", "text"}` or a plain string naming a file relative to
the scenario's workspace. Every role's system prompt gets a "Documents"
section built from this list, clipped to a sane size so a large file does not
crowd out the rest of the prompt. In agents mode, a role whose environment
grants `read_file` can still read a document's full content from the
workspace when the clipped excerpt is not enough.

## The lab environment

The `lab` environment is a research group working on one question, set in
`env_params.question`. It is a world like the market: the bookkeeping is the
environment's, the prose is the agents'.

**State.** Hypotheses form a tree (a hypothesis may refine a parent), each
with a status: proposed, testing (set when an experiment is designed for it),
confirmed, refuted or needs_repeat. Experiments belong to a hypothesis and
carry their design, their Python code, params, seed, status (designed,
running, done, failed), the parsed result, stdout and stderr tails, the
analysis, metrics, critiques and a history of every run. Finished experiments
build a dataset, one row per experiment with every numeric metric. The report
is a set of sections (`report_sections`, by default Abstract, Method, Results,
Discussion) and a list of LaTeX formulas. The budget counts experiment runs
against `max_experiments`.

**Actions.** `propose_hypothesis`, `design_experiment`, `run_experiment`,
`analyze`, `critique`, `decide` (confirmed, refuted or needs_repeat),
`write_up`, `add_formula`, `speak_to` and `observe` (the idle action). In
agents mode a role may also use `calculator`.

**The experiment contract.** `run_experiment` runs the experiment's code with
the same sandbox `run_code` uses. The program reads one JSON object from
stdin, `{"params": {...}, "seed": 42}`, and must print one JSON object of
numeric metrics as its last line of stdout. With `seed_experiments` on (the
default) every run gets a seed, so a repeat is reproducible; a seed passed to
`run_experiment` wins. The sandbox has no network and a timeout
(`experiment_timeout`, capped by `CODE_RUNNER_MAX_TIMEOUT`), so a long
computation is split into several experiments. Only the standard library is
guaranteed. When the sandbox is unavailable (no docker and no
`CODE_RUNNER_FALLBACK=local`) the experiment fails with that error and the
budget is not charged. Once the budget is spent, `run_experiment` refuses.

**Roles.** The lab names five: lead (decides), theorist (proposes and writes
formulas), experimentalist (designs and runs), critic (critiques, may ask for
a repeat) and scribe (writes the report). They shape the prompts only: any
role may take any action.

**Casting by team.** A scenario may set `team_id` instead of listing roles.
When its own roles are empty, each team member becomes a role at run time
(agent, name, role, and the member's goal or else its manifest), and the
team's leader opens the scene. Roles written on the scenario win over the
team.

**Template.** `GET /api/playground/scenarios/templates` lists the ready made
scenarios; `POST /api/playground/scenarios/from-template` with
`{"template": "lab", "agent_id": ...}` or `{"template": "lab", "team_id": ...}`
creates a complete lab (a sample question, five roles, triggered activation,
30 ticks). The Playground page offers it under "New from template", and the
Scenario Creator has `create_scenario_from_template_tool`.

**Views.** While the run goes, the lab publishes views owned by the scenario
run: a results table, a bar chart of the first numeric metric per experiment
coloured by hypothesis, the formulas (once there are any) and the report as a
document. Each is created on first sight and updated in place on later ticks,
so a run ends with one of each. They are listed on the run view and in the
Views gallery.

**Stop reasons.** The run ends with `hypotheses_decided` when every top level
hypothesis is confirmed or refuted, or `budget_exhausted` when the budget is
spent and nothing is running. Both finish the run as completed. Otherwise the
usual limits apply (`max_ticks`, cost, wall clock).

**Reproducibility.** "Reproducibility run" on the scenario page (or
`POST /api/playground/scenarios/{id}/repeat-eval` with `{"repeats": N}`)
creates an eval set named `Reproducibility: <scenario name>` whose one case is
the research question and whose config targets the scenario with N repeats,
then starts the sweep. For a lab, each repeat passes when at least one
hypothesis was decided and at least one experiment ran; the Evals page shows
how the repeats agree.

## Gotchas

- A scenario is bound to one world and one workspace; it cannot borrow a world
  from elsewhere.
- Tick limits are the only thing standing between a curious experiment and a
  large bill. Set them before the first run, not after.
- Personas mode gives a role no tools at all: it can only take the actions the
  environment defines. Agents mode gives a role real tools, however small the
  allowlist, and requires docker for exactly that reason.

## Turning the playground off

The playground (worlds, scenarios, simulation runs) is a self-contained
package, about 17 percent of the backend by line count. A deployment that does
not run simulations can turn it off entirely with:

```
PLAYGROUND_ENABLED=false
```

in `.env` (read live, like other feature flags: no restart needed). With it
off:

- `/api/playground/*` is not registered; the world and scenario pages hide
  themselves once `GET /api/health` reports `features.playground: false`.
- The agent editor's tool catalog no longer offers the world-building,
  scenario-building or scenario-run tools, so new agents cannot be given them.
  An existing agent that already lists one of those tool ids still builds; the
  id is silently dropped from its resolved tool set, the same way any unknown
  tool id is.
- The `playground` package itself is never imported, so its ~6,800 lines add
  nothing to process startup.

The default is `true`: an existing install sees no change unless it sets the
variable. Install its extra with `pip install -e ".[playground]"` (currently a
marker — the package needs nothing beyond what `backend`/`agents` already
install; the flag above is what actually makes it optional).

Related: [scenarios in chat](chat.md), [costs](costs.md), [system-agents](system-agents.md).

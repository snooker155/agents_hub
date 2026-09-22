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

Runs stream as they go, and produce a replayable record: what each role did on
each tick, and why.

## Designing one

1. Build or pick the world. Validate it — a world can be checked before anything
   runs.
2. Create the scenario against that world: roles, the agents behind them, the
   goal, and the tick limit.
3. Validate, then run with approval.

## Gotchas

- A scenario is bound to one world and one workspace; it cannot borrow a world
  from elsewhere.
- Tick limits are the only thing standing between a curious experiment and a
  large bill. Set them before the first run, not after.
- Agents in a simulation still have their real tools. A role played by an agent
  that can write files will write files.

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

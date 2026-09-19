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

Related: [scenarios in chat](chat.md), [costs](costs.md), [system-agents](system-agents.md).

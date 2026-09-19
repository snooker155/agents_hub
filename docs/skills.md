# Skills

A skill is a reusable procedure an agent can follow: written once, surfaced when
it is relevant to the task at hand.

## The part that trips everyone up

A skill sitting in your workspace does nothing on its own. **Attaching it to an
agent** is what puts it in that agent's prompt, and attaching turns on that
agent's skills setting. Without that, the skill tools are never wired up and the
skill is silently inert.

## How they surface

Skills are the procedural memory layer: the catalog is injected into the system
prompt, and the agent fetches a skill's content with `get_skill` when it decides
one applies. `list_skills` is deliberately not a tool — the catalog is already
in the prompt.

## Writing one

Keep it to a single procedure with a clear trigger. The catalog line is what the
agent matches against, so it should say when to use the skill, not summarise its
contents.

Related: [memory](memory.md), [agents](agents.md), [marketplace](marketplace.md).

export default {
  added: 'added',
  // The fold-away label on a finished thought in a chat feed.
  thought: 'Thought',
  // "and 4 more changes" — the tail of a change list too long to list in full.
  moreChanges: 'and {{count}} more changes',
  // What the agent did to the thing under edit, as the chat reports it.
  change: {
    added: 'added',
    removed: 'removed',
    updated: 'updated',
    set: 'set',
  },
  // What it did it to. Singular: each line names one thing.
  kinds: {
    locations: 'location',
    items: 'item',
    entities: 'entity',
    globals: 'world value',
    stats: 'stat',
    roles: 'role',
    actions: 'action',
    objectives: 'objective',
    rules: 'rules',
    base_actions: 'built-in actions',
    end_when: 'ending',
    name: 'name',
    description: 'description',
    starting_location: 'starting location',
    time_of_day: 'time of day',
    hours_per_tick: 'hours per tick',
    environment: 'environment',
    activation: 'activation',
    env_params: 'parameter',
    limits: 'limit',
  },
};

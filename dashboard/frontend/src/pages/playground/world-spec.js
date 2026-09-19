/**
 * The constants a world editor and its pages share.
 *
 * Separate from ``world-editor.jsx`` because that file exports components and
 * this one does not: mixing the two costs fast refresh, and these are read by
 * the catalogue page as well as the editor.
 *
 * Everything here mirrors ``playground/worlds.py``. The server is the authority
 * — it coerces and validates whatever arrives — so these lists exist to *offer*
 * the right choices, never to be the check.
 */

export const inputClass =
  'w-full text-sm border border-gray-300 rounded-md px-2 py-1.5 focus:outline-none '
  + 'focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400';

/** Comparisons a condition may make. No expressions — a condition is a triple. */
export const OPERATORS = ['==', '!=', '>', '>=', '<', '<=', 'contains'];

/** Everything an action can do to the world, in the order an author scans for. */
export const EFFECT_TYPES = [
  'set_global', 'add_global', 'set_stat', 'add_stat', 'move_actor', 'move_agent',
  'give_item', 'take_item', 'drop_item', 'create_item', 'destroy_item',
  'set_entity', 'move_entity', 'message', 'log', 'end_world',
];

/**
 * What an effect's three fields mean, per effect type.
 *
 * An effect is one row with a type and three boxes, and what those boxes hold
 * changes completely with the type: ``target`` is a destination for
 * ``move_actor``, a recipient for ``message``, and nothing at all for ``log``.
 * Labelling all of them "Target / Which value / Value" and leaving the author
 * to guess is how worlds end up with effects that quietly do nothing — so the
 * table the engine reads (``playground/environments/custom.py``) is mirrored
 * here, and each row relabels, offers and disables its boxes from it.
 *
 * Each field is a *role*: one of the pools in ``EFFECT_FIELD_POOL``, a free
 * text role (``value``, ``amount``, ``text``), or ``null`` when the effect
 * does not use that field at all.
 */
export const EFFECT_FIELDS = {
  set_global:   { target: null,       name: 'global',   value: 'value' },
  add_global:   { target: null,       name: 'global',   value: 'amount' },
  set_stat:     { target: 'who',      name: 'stat',     value: 'value' },
  add_stat:     { target: 'who',      name: 'stat',     value: 'amount' },
  move_actor:   { target: 'where',    name: null,       value: null },
  move_agent:   { target: 'who',      name: null,       value: 'where' },
  give_item:    { target: 'who',      name: 'item',     value: null },
  take_item:    { target: null,       name: 'item',     value: null },
  drop_item:    { target: null,       name: 'item',     value: null },
  create_item:  { target: 'who',      name: 'item',     value: 'text' },
  destroy_item: { target: null,       name: 'item',     value: null },
  set_entity:   { target: 'entity',   name: 'stateKey', value: 'value' },
  move_entity:  { target: 'entity',   name: null,       value: 'where' },
  message:      { target: 'whoAll',   name: null,       value: 'text' },
  log:          { target: null,       name: null,       value: 'text' },
  end_world:    { target: null,       name: null,       value: 'text' },
};

/** Which pool of names a field role picks from. Roles absent here take prose. */
export const EFFECT_FIELD_POOL = {
  who: 'who', whoAll: 'whoAll', where: 'locations', entity: 'entities',
  global: 'globals', stat: 'stats', item: 'items', stateKey: 'stateKeys',
};

/** The role one field of one effect plays, or null when the effect ignores it. */
export function effectFieldRole(type, key) {
  return (EFFECT_FIELDS[type] || {})[key] || null;
}

/**
 * The row with every field the new type does not use cleared.
 *
 * Switching ``message`` to ``log`` leaves a recipient behind that the engine
 * will never read, and a field holding a name that does nothing is worse than
 * an empty one.
 */
export function effectForType(row, type) {
  const next = { ...row, type };
  for (const key of ['target', 'name', 'value']) {
    if (!effectFieldRole(type, key)) next[key] = '';
  }
  return next;
}


/** The shipped moves a world switches on rather than re-declaring. */
export const BASE_ACTIONS = [
  'move_to', 'speak_to', 'announce', 'give_item', 'take_item', 'drop_item',
  'inspect', 'observe',
];

export const VALUE_TYPES = ['number', 'integer', 'string', 'boolean'];

export const ARG_TYPES = [
  'string', 'integer', 'number', 'boolean', 'agent', 'item', 'location', 'entity',
];

/** The fields the server stores — everything else in a payload it computes. */
export const WORLD_FIELDS = [
  'world_id', 'name', 'description', 'workspace', 'rules', 'locations', 'items',
  'entities', 'globals', 'stats', 'roles', 'actions', 'objectives',
  'base_actions', 'starting_location', 'end_when', 'time_of_day',
  'hours_per_tick', 'created_at', 'updated_at',
];

/**
 * A world as it is sent back — the stored fields only.
 *
 * Also what "has this changed" is asked of: comparing the whole response would
 * see the server's own validation output as an edit, and the page would claim
 * unsaved changes the moment it loaded.
 */
export function worldPayload(world) {
  if (!world) return world;
  const out = {};
  for (const key of WORLD_FIELDS) {
    if (world[key] !== undefined) out[key] = world[key];
  }
  return out;
}

/**
 * What a character this world has no opinion about gets.
 *
 * Mirrors ``describe_generic_role`` in ``playground/environments/custom.py``,
 * but over the draft in the editor: the world being typed has not been saved,
 * so there is no catalogue entry to read the server's answer from. The
 * scenario form does read that one — this exists so the author sees the shape
 * while they are still writing the world that decides it.
 *
 * Generic in both directions, which is the part worth seeing: every action the
 * world leaves unreserved (including the built-ins a role's own list would
 * have narrowed away) and none of the ones a role reserves.
 */
export function genericRole(spec) {
  const on = spec?.base_actions || [];
  return {
    start_location: spec?.starting_location || '',
    stats: Object.fromEntries(
      (spec?.stats || []).filter((s) => s?.name).map((s) => [s.name, s.default]),
    ),
    actions: [
      ...BASE_ACTIONS.filter((name) => on.includes(name)),
      ...(spec?.actions || [])
        .filter((a) => a?.name && !(a.roles || []).length)
        .map((a) => a.name),
    ],
  };
}


/**
 * The generic role as a list of phrases: where it starts, what it holds, how
 * much it may do.
 *
 * Shared by the world editor and the scenario form on purpose. The same
 * character is described in both, and two descriptions that drift read as two
 * different facts — so there is one function and one format.
 */
export function genericGrants(generic, t) {
  return [
    // A blank default start scatters the cast, and "starts in <a random room>"
    // reads as a room by that name. Its own phrase, then.
    generic?.start_location
      ? t('playground.worldRole.startsAt', { location: generic.start_location })
      : t('playground.worldRole.anywhere'),
    ...Object.entries(generic?.stats || {}).map(([name, value]) => `${name} ${value}`),
    t('playground.worldRole.actionCount', { count: (generic?.actions || []).length }),
  ];
}


export function blankAction() {
  return {
    name: '', description: '', args: [], roles: [], at_locations: [],
    target_present: true, requires_held_item: false, requires_entity_present: true,
    conditions: [], refusal: '', effects: [], log: '', success: '',
  };
}


/**
 * One validation problem, in the reader's language.
 *
 * The server reports a code and what it is about; the sentence is written
 * here, once per language, because the person reading it is the one building
 * the world and their browser — not the API — knows what language that is. A
 * code with no string yet falls back to the server's English rather than to a
 * blank line or a raw key.
 */
export function problemText(problem, t) {
  if (!problem) return '';
  if (typeof problem === 'string') return problem;      // pre-codes payloads
  return t(`worlds.problems.${problem.code}`, {
    ...(problem.params || {}),
    defaultValue: problem.message || problem.code,
  });
}

/**
 * The message behind a failed request.
 *
 * Worlds endpoints refuse with ``{code, params, message}`` for the same reason
 * validation does. Anything else — a proxy error, an older endpoint — still
 * arrives as a plain string and is shown as it came.
 */
export function apiMessage(error, t, fallback = '') {
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === 'object' && detail.code) {
    return t(`worlds.errors.${detail.code}`, {
      ...(detail.params || {}),
      defaultValue: detail.message || fallback,
    });
  }
  return (typeof detail === 'string' && detail) || fallback;
}

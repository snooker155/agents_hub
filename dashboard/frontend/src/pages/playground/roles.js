import { genericGrants } from './world-spec';

/**
 * The pure questions a character's binding to a world answers: which role it
 * is cast in, what that role lets it do, and what a freshly added character
 * starts out as. Split out of the scenario page so the setup form, the
 * character card and the character dialog can share one answer to each rather
 * than three that could drift.
 */

/**
 * The world role a character is cast in, or null when nothing binds.
 *
 * Case-insensitive, mirroring ``WorldSpec.role`` on the server: a form that
 * matched case-sensitively would warn about a binding the run then honours.
 */
export function worldRoleFor(world, name) {
  const wanted = (name || '').trim().toLowerCase();
  if (!wanted) return null;
  return (world?.role_specs || [])
    .find((r) => (r.name || '').trim().toLowerCase() === wanted) || null;
}


/**
 * Which of the world's actions a role may take.
 *
 * The same two filters ``CustomEnvironment.allowed_actions`` applies: the
 * role's own list narrows the whole action set (empty = all of it), and an
 * action's own ``roles`` list narrows who may take it. Built-ins declare no
 * ``roles``, so only the first filter reaches them.
 */
export function roleActions(world, spec) {
  const permitted = spec?.actions?.length ? new Set(spec.actions) : null;
  return (world?.actions || [])
    .filter((a) => (!permitted || permitted.has(a.name))
      && !((a.roles || []).length && !(a.roles || []).includes(spec?.name)))
    .map((a) => a.name);
}


/**
 * What a character actually starts with, in a few words.
 *
 * Worth saying on the card and in the dialog, because it is otherwise
 * invisible until the run: the role is picked here and its consequences —
 * where you wake up, what you hold, what you may do — live on the world page.
 *
 * ``spec`` null means nothing bound, and then this describes the generic role
 * the world falls back to. That answer comes from the server
 * (``describe_generic_role``) rather than being worked out again here: a
 * promise about what an uncast character can do is worth nothing if the run
 * disagrees with it.
 */
export function roleGrants(world, spec, t) {
  const generic = world?.generic_role || null;
  if (!spec) return generic ? genericGrants(generic, t) : [];

  // A role is the generic role with its own overrides on top — the order
  // ``register_cast`` applies them in — so it is described by the same
  // function rather than by a second one that formats it slightly differently.
  const out = genericGrants({
    start_location: spec.start_location || generic?.start_location || '',
    stats: { ...(generic?.stats || {}), ...(spec.stats || {}) },
    actions: roleActions(world, spec),
  }, t);
  if ((spec.start_items || []).length) {
    out.splice(1, 0, t('playground.worldRole.carries', {
      items: spec.start_items.join(', '),
    }));
  }
  return out;
}


/** A blank character, seeded with the first agent so the common case is one click. */
export function emptyCharacter(agents) {
  return {
    agent_id: agents[0]?.id || '', name: '', role: '', goal: '',
    private_knowledge: '', objective: null, provider: null, model: null,
    memory_horizon: 16, wake_every: 0, starts: false, npc: false,
  };
}


/** What a role falls back to when it picks no model of its own. */
export function roleInherits(agent, t) {
  return agent?.model
    ? t('playground.inheritFromAgent', { model: agent.model })
    : t('playground.inheritFromScenario');
}

import { describe, it, expect } from 'vitest';
import { translate } from '../../../i18n/core';
import {
  worldRoleFor, roleActions, roleGrants, emptyCharacter, roleInherits,
} from '../roles';

const t = translate.bind(null, 'en');

// A small world with one declared role and one generic fallback, enough to
// exercise binding, action filtering and the grants a character reads.
const WORLD = {
  role_specs: [
    {
      name: 'innkeeper', description: 'Runs the inn.',
      start_location: 'tavern', start_items: ['ledger'], stats: { mood: 2 },
      actions: ['serve'],
    },
  ],
  roles: ['innkeeper'],
  actions: [
    { name: 'serve', roles: ['innkeeper'] },
    { name: 'fine', roles: ['warden'] },
    { name: 'move_to', roles: [] },
  ],
  generic_role: { start_location: 'road', stats: { coin: 0 }, actions: ['move_to'] },
};

describe('worldRoleFor', () => {
  it('finds the declared role, case-insensitively', () => {
    expect(worldRoleFor(WORLD, 'Innkeeper')).toBe(WORLD.role_specs[0]);
    expect(worldRoleFor(WORLD, ' innkeeper ')).toBe(WORLD.role_specs[0]);
  });

  it('is null for nothing typed, or a name the world never declared', () => {
    expect(worldRoleFor(WORLD, '')).toBeNull();
    expect(worldRoleFor(WORLD, 'warden')).toBeNull();
    expect(worldRoleFor(undefined, 'innkeeper')).toBeNull();
  });
});

describe('roleActions', () => {
  it('narrows to the role\'s own list, further narrowed by who each action names', () => {
    // The innkeeper's own list only offers 'serve'; 'move_to' is open to
    // everyone but is not on the innkeeper's own list, so it drops out too.
    expect(roleActions(WORLD, WORLD.role_specs[0])).toEqual(['serve']);
  });

  it('with no role of its own, offers everything not reserved for someone else', () => {
    expect(roleActions(WORLD, { name: 'traveller', actions: [] })).toEqual(['move_to']);
  });
});

describe('roleGrants', () => {
  it('describes a bound character: where it starts, what it carries, what it may do', () => {
    const grants = roleGrants(WORLD, WORLD.role_specs[0], t);
    expect(grants[0]).toBe(t('playground.worldRole.startsAt', { location: 'tavern' }));
    expect(grants[1]).toBe(t('playground.worldRole.carries', { items: 'ledger' }));
    expect(grants).toContain('mood 2');
    expect(grants[grants.length - 1]).toBe(t('playground.worldRole.actionCount', { count: 1 }));
  });

  it('falls back to the generic role when nothing is bound', () => {
    const grants = roleGrants(WORLD, null, t);
    expect(grants[0]).toBe(t('playground.worldRole.startsAt', { location: 'road' }));
    expect(grants).toContain('coin 0');
  });

  it('is empty when the world declares no generic role either', () => {
    expect(roleGrants({}, null, t)).toEqual([]);
  });
});

describe('emptyCharacter', () => {
  it('seeds the first agent, so adding one is a single click', () => {
    const agents = [{ id: 'agt_1' }, { id: 'agt_2' }];
    const blank = emptyCharacter(agents);
    expect(blank.agent_id).toBe('agt_1');
    expect(blank.name).toBe('');
    expect(blank.memory_horizon).toBe(16);
    expect(blank.starts).toBe(false);
    expect(blank.npc).toBe(false);
  });

  it('is still a valid character with no agents to seed from', () => {
    expect(emptyCharacter([]).agent_id).toBe('');
  });
});

describe('roleInherits', () => {
  it('names the agent\'s own model, or falls back to the scenario', () => {
    expect(roleInherits({ model: 'gpt-5' }, t)).toBe(
      t('playground.inheritFromAgent', { model: 'gpt-5' }),
    );
    expect(roleInherits({}, t)).toBe(t('playground.inheritFromScenario'));
    expect(roleInherits(undefined, t)).toBe(t('playground.inheritFromScenario'));
  });
});

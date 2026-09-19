import { describe, it, expect } from 'vitest';
import { translate } from '../../../i18n/core';
import {
  apiMessage, genericGrants, genericRole, problemText, worldPayload,
} from '../world-spec';

// The world editor's messages come from the server as codes, so this is where
// they stop being English: these two helpers are the whole translation seam.
const t = (lang) => (key, vars) => translate(lang, key, vars);

describe('problemText', () => {
  it('writes the sentence in the reader’s language', () => {
    const problem = {
      code: 'unknown_exit',
      params: { location: 'hall', exit: 'attic' },
      message: "Location 'hall' opens onto 'attic', which is not a location in this world.",
    };
    expect(problemText(problem, t('en'))).toContain('opens onto');
    expect(problemText(problem, t('ru'))).toContain('такой локации в этом мире нет');
    expect(problemText(problem, t('de'))).toContain('das es in dieser Welt nicht gibt');
    // Whichever language, it still names what it is about.
    for (const lang of ['en', 'ru', 'de']) {
      expect(problemText(problem, t(lang))).toContain('attic');
    }
  });

  it('falls back to the server’s English for a code with no string yet', () => {
    expect(problemText({ code: 'not_a_code', message: 'Something is off.' }, t('ru')))
      .toBe('Something is off.');
  });

  it('shows a plain string as it came', () => {
    expect(problemText('already a sentence', t('ru'))).toBe('already a sentence');
  });
});

describe('apiMessage', () => {
  const refusal = {
    response: {
      data: {
        detail: {
          code: 'world_in_use',
          params: { count: 2, names: 'Tonight, Later' },
          message: '2 scenario(s) run in this world: Tonight, Later.',
        },
      },
    },
  };

  it('translates a refusal and keeps its details', () => {
    const ru = apiMessage(refusal, t('ru'), 'fallback');
    expect(ru).toContain('Tonight, Later');
    expect(ru).toContain('2');
    expect(ru).not.toBe('fallback');
    expect(apiMessage(refusal, t('de'), 'fallback')).toContain('Szenario');
  });

  it('passes a plain-string detail through and falls back when there is none', () => {
    expect(apiMessage({ response: { data: { detail: 'gateway said no' } } }, t('en'), 'x'))
      .toBe('gateway said no');
    expect(apiMessage(new Error('offline'), t('en'), 'x')).toBe('x');
  });
});

describe('worldPayload', () => {
  it('keeps the stored fields and drops what the server computes', () => {
    const payload = worldPayload({
      world_id: 'wld_1', name: 'Inn', locations: [{ name: 'hall' }],
      errors: [{ code: 'no_actions' }], warnings: [], catalog: {}, env_id: 'custom:wld_1',
      scenarios: [{ scenario_id: 's' }],
    });
    expect(payload).toEqual({
      world_id: 'wld_1', name: 'Inn', locations: [{ name: 'hall' }],
    });
  });
});


describe('genericRole', () => {
  // The world being edited, in the shape the form holds it.
  const spec = {
    starting_location: 'hall',
    base_actions: ['move_to', 'speak_to', 'observe'],
    stats: [{ name: 'coin', default: 5 }, { name: '', default: 0 }],
    actions: [
      { name: 'ring', roles: [] },
      { name: 'fine', roles: ['warden'] },
    ],
  };

  it('hands an uncast character everything the world does not reserve', () => {
    const generic = genericRole(spec);
    expect(generic.start_location).toBe('hall');
    expect(generic.stats).toEqual({ coin: 5 });
    // Every built-in that is switched on, plus the action no role reserves.
    expect(generic.actions).toEqual(['move_to', 'speak_to', 'observe', 'ring']);
    expect(generic.actions).not.toContain('fine');
  });

  it('says a blank default start scatters the cast rather than guessing a room', () => {
    expect(genericRole({ ...spec, starting_location: '' }).start_location).toBe('');
  });

  it('describes a world that is barely started without throwing', () => {
    expect(genericRole(undefined)).toEqual({
      start_location: '', stats: {}, actions: [],
    });
  });
});


describe('genericGrants', () => {
  // The world editor and the scenario form both print this, and a reader who
  // sees the same character described two ways reads it as two facts. So the
  // exact line is the contract, not just its contents.
  const generic = { start_location: 'hall', stats: { coin: 5 }, actions: Array(9).fill('x') };
  const line = (lang) => [
    t(lang)('playground.worldRole.unboundChip'), ...genericGrants(generic, t(lang)),
  ].join(' · ');

  it('reads as one line in every language', () => {
    expect(line('ru')).toBe('generic-роль · старт в «hall» · coin 5 · 9 действий');
    expect(line('en')).toBe('generic role · starts in hall · coin 5 · 9 actions');
    expect(line('de')).toBe('generische Rolle · startet in hall · coin 5 · 9 Aktionen');
  });

  it('says the room is not fixed rather than naming one the world never set', () => {
    expect(genericGrants({ ...generic, start_location: '' }, t('ru'))[0])
      .toBe('старт в случайной комнате');
    expect(genericGrants({ ...generic, start_location: '' }, t('en'))[0])
      .toBe('starts anywhere');
  });
});

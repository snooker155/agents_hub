import { describe, it, expect } from 'vitest';
import { DEFAULT_LANGUAGE, LANGUAGES, statusLabel, translate } from '../core';

describe('translate', () => {
  it('resolves a dotted key through namespace and nesting', () => {
    expect(translate('en', 'status.running')).toBe('running');
    expect(translate('en', 'teams.modes.centralized.label')).toBe('Centralized');
  });

  it('translates the same key per language', () => {
    expect(translate('ru', 'components.notifications.empty')).toBe('Нет уведомлений');
    expect(translate('de', 'components.notifications.empty')).toBe('Keine Benachrichtigungen');
  });

  it('interpolates {{vars}}', () => {
    expect(translate('en', 'common.minutesAgo', { count: 5 })).toBe('5 minutes ago');
  });

  it('leaves a placeholder alone when no such variable was passed', () => {
    expect(translate('en', 'components.notifications.minutesAgo', { other: 1 }))
      .toBe('{{count}}m ago');
  });

  it('shows the key for a plural-only entry asked for without a count', () => {
    // `common.minutesAgo` exists only as _one/_other, so there is nothing to
    // resolve without a count — the key is the deliberate last resort.
    expect(translate('en', 'common.minutesAgo')).toBe('common.minutesAgo');
  });

  it('picks the English singular and plural forms', () => {
    expect(translate('en', 'common.secondsAgo', { count: 1 })).toBe('1 second ago');
    expect(translate('en', 'common.secondsAgo', { count: 3 })).toBe('3 seconds ago');
  });

  it('picks all four Russian plural forms', () => {
    // one / few / many are genuinely different words in Russian — the reason
    // the plural category comes from Intl rather than a count === 1 check.
    expect(translate('ru', 'common.secondsAgo', { count: 1 })).toBe('1 секунду назад');
    expect(translate('ru', 'common.secondsAgo', { count: 2 })).toBe('2 секунды назад');
    expect(translate('ru', 'common.secondsAgo', { count: 5 })).toBe('5 секунд назад');
    expect(translate('ru', 'common.secondsAgo', { count: 21 })).toBe('21 секунду назад');
  });

  it('falls back to the default language for an unsupported one', () => {
    expect(translate('fr', 'status.running')).toBe(translate(DEFAULT_LANGUAGE, 'status.running'));
  });

  it('shows the key itself rather than an empty label when nothing matches', () => {
    expect(translate('en', 'nope.not.a.key')).toBe('nope.not.a.key');
  });

  it('prefers an explicit defaultValue over the key', () => {
    expect(translate('en', 'nope.not.a.key', { defaultValue: 'Fallback' })).toBe('Fallback');
  });

  it('does not walk past a string into a missing sub-key', () => {
    expect(translate('en', 'status.running.deeper')).toBe('status.running.deeper');
  });

  it('returns an empty string for an empty key', () => {
    expect(translate('en', '')).toBe('');
    expect(translate('en', null)).toBe('');
  });

  it('has the empty-slot label the memory tree renders', () => {
    // SlotValue renders `t('slotValue.empty')` for an empty container.
    LANGUAGES.forEach(({ code }) => {
      expect(translate(code, 'slotValue.empty')).not.toBe('slotValue.empty');
    });
  });
});

describe('statusLabel', () => {
  const t = (key, vars) => translate('en', key, vars);

  it('translates a known backend status', () => {
    expect(statusLabel('running', t)).toBe('running');
    expect(statusLabel('awaiting_approval', t)).toBe('awaiting approval');
  });

  it('renders an untranslated status readably instead of as a raw enum', () => {
    expect(statusLabel('some_new_state', t)).toBe('some new state');
  });

  it('renders no status as an empty string', () => {
    expect(statusLabel(null, t)).toBe('');
    expect(statusLabel('', t)).toBe('');
  });
});

describe('LANGUAGES', () => {
  it('offers the three shipped locales, English first', () => {
    expect(LANGUAGES.map((l) => l.code)).toEqual(['en', 'ru', 'de']);
    expect(DEFAULT_LANGUAGE).toBe('en');
  });

  it('describes each language completely enough for the switcher', () => {
    LANGUAGES.forEach((l) => {
      expect(l).toMatchObject({
        code: expect.any(String),
        label: expect.any(String),
        short: expect.any(String),
        flag: expect.any(String),
      });
    });
  });
});

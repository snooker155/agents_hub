import { describe, it, expect } from 'vitest';

import { translate } from '../../i18n/core';
import { notificationText } from '../notificationText';

const tr = (lang) => (key, vars) => translate(lang, key, vars);

describe('notificationText', () => {
  it('builds an orchestrator approval in the reader\'s language', () => {
    const n = {
      title: 'Approval needed: About page',
      body: "'universal_agent' was chosen for the task: fits.",
      source: { message: { key: 'approvalNeeded', params: { title: 'About page', agent: 'universal_agent', reason: 'пишет тексты' } } },
    };
    const text = notificationText(n, tr('ru'));
    expect(text.title).toBe('Нужно одобрение: About page');
    expect(text.body).toMatch(/агент «universal_agent» начал работу/);
    expect(text.body).toMatch(/Почему этот агент: пишет тексты$/);
    expect(text.body).not.toMatch(/Approve/);
  });

  it('picks the body without a missing value', () => {
    const n = { source: { message: { key: 'taskFailed', params: { title: 'T', agent: 'a' } } } };
    expect(notificationText(n, tr('en')).body).toBe("Agent 'a' failed.");
  });

  it('keeps the stored text for a notification without a message', () => {
    const n = { title: 'Reminder', body: 'Call Bob' };
    expect(notificationText(n, tr('ru'))).toEqual({ title: 'Reminder', body: 'Call Bob' });
  });

  it('falls back to the stored text for a key this build does not know', () => {
    const n = { title: 'Old', body: 'text', source: { message: { key: 'somethingNew', params: {} } } };
    expect(notificationText(n, tr('de'))).toEqual({ title: 'Old', body: 'text' });
  });
});

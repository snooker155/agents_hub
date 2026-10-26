import { describe, it, expect } from 'vitest';
import en from '../../../i18n/locales/en/toolActivity';
import { toolActivity } from '../toolActivity';

// The English phrases, looked up and filled the way the app's t() does.
const t = (key, values = {}) => {
  const path = key.replace(/^toolActivity\./, '').split('.');
  const raw = path.reduce((o, k) => (o ? o[k] : undefined), en);
  return String(raw ?? key).replace(/\{\{(\w+)\}\}/g, (_, k) => values[k] ?? '');
};

describe('toolActivity', () => {
  it('says what a known tool does, with its main argument', () => {
    expect(toolActivity(t, 'web_search', '{"query": "flights to Lisbon"}')).toBe('Searching the web: flights to Lisbon');
    expect(toolActivity(t, 'read_file', "{'path': '/w/src/app.py'}")).toBe('Reading a file: app.py');
    expect(toolActivity(t, 'run_shell', '{"command": "ls -la"}')).toBe('Running a command: ls -la');
  });

  it('gives the phrase alone when the call has no argument to show', () => {
    expect(toolActivity(t, 'list_files', '')).toBe('Looking through the files');
    expect(toolActivity(t, 'view_snapshot', null)).toBe('Building a view');
  });

  it('reads any other tool from the verb in its name', () => {
    expect(toolActivity(t, 'mcp__github__create_issue', '{"title": "Bug"}')).toBe('Creating github issue: Bug');
    expect(toolActivity(t, 'jira_get_ticket', '')).toBe('Looking up jira ticket');
    expect(toolActivity(t, 'weather', '')).toBe('Using weather');
  });
});

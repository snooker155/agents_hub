import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildMenu, visibleGroups, hiddenCount, defaultMenuMode, readMenuMode, writeMenuMode,
  isItemActive, SIMPLE, FULL, MENU_MODE_KEY,
} from '../navMenu';

const t = (k) => k;
const single = { mode: 'single', features: {} };
const multiAdmin = { mode: 'multi', user: { username: 'a', role: 'admin' }, features: { audit: true } };
const multiUser = { mode: 'multi', user: { username: 'u', role: 'member' }, features: { audit: true } };
const paths = (groups) => groups.flatMap((g) => g.items.map((i) => i.path));

describe('navMenu', () => {
  beforeEach(() => { localStorage.clear(); });

  it('has five groups in object tree order', () => {
    const groups = buildMenu({ t, auth: single });
    expect(groups.map((g) => g.key)).toEqual(['conversation', 'work', 'agents', 'integrations', 'records']);
    expect(groups[0].label).toBe('nav.groups.conversation');
  });

  it('puts every page in exactly one place', () => {
    const all = paths(buildMenu({ t, auth: multiAdmin, features: { cluster: true } }));
    expect(new Set(all).size).toBe(all.length);
    for (const p of ['/connectors', '/connections', '/mcp', '/watchers', '/widgets']) {
      expect(buildMenu({ t, auth: single }).find((g) => g.key === 'integrations').items.map((i) => i.path)).toContain(p);
    }
  });

  it('simple menu keeps a short list with settings and docs', () => {
    const groups = buildMenu({ t, auth: single });
    const shown = paths(visibleGroups(groups, SIMPLE, '/chat'));
    expect(shown.length).toBeLessThanOrEqual(16);
    expect(shown).toEqual(expect.arrayContaining(['/assistant', '/chat', '/agents', '/models', '/settings', '/docs']));
    expect(shown).not.toContain('/connectors');
    expect(visibleGroups(groups, SIMPLE, '/chat').map((g) => g.key)).not.toContain('integrations');
    expect(hiddenCount(groups, '/chat')).toBe(paths(groups).length - shown.length);
  });

  it('simple menu still shows the row of the page on screen', () => {
    const groups = buildMenu({ t, auth: single });
    const shown = visibleGroups(groups, SIMPLE, '/connectors/slack');
    expect(paths(shown)).toContain('/connectors');
    expect(shown.find((g) => g.key === 'integrations').items).toHaveLength(1);
  });

  it('full menu shows everything', () => {
    const groups = buildMenu({ t, auth: single });
    expect(visibleGroups(groups, FULL, '/chat')).toBe(groups);
  });

  it('cluster row only for an operator when the hub is a cluster', () => {
    expect(paths(buildMenu({ t, auth: single }))).not.toContain('/cluster');
    expect(paths(buildMenu({ t, auth: single, features: { cluster: true } }))).toContain('/cluster');
    expect(paths(buildMenu({ t, auth: multiUser, features: { cluster: true } }))).not.toContain('/cluster');
    expect(paths(buildMenu({ t, auth: multiUser }))).not.toContain('/health');
    expect(paths(buildMenu({ t, auth: multiAdmin }))).toEqual(expect.arrayContaining(['/health', '/users', '/audit']));
  });

  it('account stays in the simple menu for a signed in user', () => {
    expect(paths(visibleGroups(buildMenu({ t, auth: multiUser }), SIMPLE, '/'))).toContain('/account');
  });

  it('playground row follows the feature flag', () => {
    expect(paths(buildMenu({ t, auth: single, features: { playground: false } }))).not.toContain('/playground');
    expect(paths(buildMenu({ t, auth: single, features: {} }))).toContain('/playground');
  });

  it('defaults to full only for a multi user administrator', () => {
    expect(defaultMenuMode(single)).toBe(SIMPLE);
    expect(defaultMenuMode(multiUser)).toBe(SIMPLE);
    expect(defaultMenuMode(multiAdmin)).toBe(FULL);
  });

  it('remembers the choice and ignores junk', () => {
    expect(readMenuMode()).toBeNull();
    writeMenuMode(FULL);
    expect(readMenuMode()).toBe(FULL);
    localStorage.setItem(MENU_MODE_KEY, 'weird');
    expect(readMenuMode()).toBeNull();
  });

  it('matches the active row like the sidebar did', () => {
    expect(isItemActive({ path: '/dashboard' }, '/dashboard/x')).toBe(false);
    expect(isItemActive({ path: '/agents' }, '/agents/a1')).toBe(true);
    expect(isItemActive({ path: '/artifacts', also: ['/studio'] }, '/studio')).toBe(true);
  });
});

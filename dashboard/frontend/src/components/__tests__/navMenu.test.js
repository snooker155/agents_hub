import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildMenu, visibleGroups, hiddenCount, defaultMenuMode, readMenuMode, writeMenuMode,
  isItemActive, SIMPLE, FULL, MENU_MODE_KEY,
} from '../navMenu';

const t = (k) => k;
const paths = (groups) => groups.flatMap((g) => g.items.map((i) => i.path));

describe('navMenu', () => {
  beforeEach(() => { localStorage.clear(); });

  it('has five groups in object tree order', () => {
    const groups = buildMenu({ t });
    expect(groups.map((g) => g.key)).toEqual(['conversation', 'work', 'agents', 'integrations', 'records']);
    expect(groups[0].label).toBe('nav.groups.conversation');
  });

  it('puts every page in exactly one place', () => {
    const all = paths(buildMenu({ t }));
    expect(new Set(all).size).toBe(all.length);
    expect(buildMenu({ t }).find((g) => g.key === 'integrations').items.map((i) => i.path)).toContain('/connectors');
  });

  it('simple menu keeps a short list with workspaces, settings and docs', () => {
    const groups = buildMenu({ t });
    const shown = paths(visibleGroups(groups, SIMPLE));
    expect(shown.length).toBeLessThanOrEqual(16);
    expect(shown).toEqual(expect.arrayContaining(['/chat', '/workspaces', '/agents', '/models', '/settings', '/docs']));
    expect(shown).not.toContain('/connectors');
    expect(shown).not.toContain('/dashboard');
    expect(visibleGroups(groups, SIMPLE).map((g) => g.key)).not.toContain('integrations');
    expect(hiddenCount(groups)).toBe(paths(groups).length - shown.length);
  });

  it('simple menu lights up the home row of a hidden page instead of adding one', () => {
    const shown = visibleGroups(buildMenu({ t }), SIMPLE);
    expect(paths(shown)).not.toContain('/deployments');
    const lit = (pathname) => shown.flatMap((g) => g.items).filter((it) => isItemActive(it, pathname)).map((it) => it.path);
    expect(lit('/deployments')).toEqual(['/plan']);
    expect(lit('/loops/l1')).toEqual(['/flows']);
    expect(lit('/connectors/git')).toEqual(['/settings']);
    expect(lit('/sessions')).toEqual(['/settings']);
    expect(lit('/plan')).toEqual(['/plan']);
  });

  it('every page is in the simple menu or names a home row that is', () => {
    const groups = buildMenu({ t });
    const simple = new Set(groups.flatMap((g) => g.items).filter((it) => it.simple).map((it) => it.path));
    for (const it of groups.flatMap((g) => g.items)) {
      if (!it.simple) expect(simple.has(it.home)).toBe(true);
    }
  });

  it('dashboard sits with the administration under settings', () => {
    const groups = buildMenu({ t });
    const lit = (pathname) => visibleGroups(groups, SIMPLE).flatMap((g) => g.items).filter((it) => isItemActive(it, pathname)).map((it) => it.path);
    expect(lit('/dashboard')).toEqual(['/settings']);
  });

  it('full menu shows everything', () => {
    const groups = buildMenu({ t });
    expect(visibleGroups(groups, FULL)).toBe(groups);
  });

  it('has no row for a page the single-user build dropped', () => {
    const all = paths(buildMenu({ t }));
    for (const p of ['/cluster', '/users', '/account', '/audit', '/registry', '/agent-registry', '/services', '/vault', '/nodes', '/widgets']) {
      expect(all).not.toContain(p);
    }
    expect(all).toContain('/health');
  });

  it('has no row for the evals or the playground', () => {
    const all = paths(buildMenu({ t }));
    expect(all).not.toContain('/evals');
    expect(all).not.toContain('/playground');
  });

  it('starts in the simple menu', () => {
    expect(defaultMenuMode()).toBe(SIMPLE);
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

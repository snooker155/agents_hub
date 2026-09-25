import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SkillHistoryModal from '../SkillHistoryModal';
import { I18nProvider } from '../../../i18n';

const listSkillVersions = vi.fn();
const getSkillVersion = vi.fn();
const restoreSkillVersion = vi.fn(() => Promise.resolve({ data: { id: 's1', version: 3 } }));
const pinSkillVersion = vi.fn(() => Promise.resolve({ data: { id: 's1', pinned_version: 1 } }));

vi.mock('../../../api/skillVersions', () => ({
  listSkillVersions: (...args) => listSkillVersions(...args),
  getSkillVersion: (...args) => getSkillVersion(...args),
  restoreSkillVersion: (...args) => restoreSkillVersion(...args),
  pinSkillVersion: (...args) => pinSkillVersion(...args),
}));

const SNAPSHOTS = {
  1: { name: 'Deploy', description: 'When deploying', steps: ['build', 'ship'], body: '', tags: [], resources: [] },
  2: { name: 'Deploy', description: 'When deploying', steps: ['build', 'test', 'ship'], body: '', tags: [], resources: [] },
};

const VERSIONS = [
  { skill_id: 's1', version: 2, op: 'update', actor_kind: 'user', actor_id: 'alice', note: 'add tests', at: '2026-09-25T10:00:00Z' },
  { skill_id: 's1', version: 1, op: 'create', actor_kind: 'user', actor_id: 'alice', note: '', at: '2026-09-25T09:00:00Z' },
];

const show = (skill) => render(
  <I18nProvider>
    <SkillHistoryModal skill={{ id: 's1', name: 'Deploy', version: 2, ...skill }} onClose={() => {}} onChanged={() => {}} />
  </I18nProvider>,
);

beforeEach(() => {
  listSkillVersions.mockReset();
  getSkillVersion.mockReset();
  restoreSkillVersion.mockClear();
  pinSkillVersion.mockClear();
  listSkillVersions.mockResolvedValue({ data: { current: 2, pinned: null, versions: VERSIONS } });
  getSkillVersion.mockImplementation((id, v) => Promise.resolve({ data: { version: v, snapshot: SNAPSHOTS[v] } }));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('SkillHistoryModal', () => {
  it('lists versions and diffs the newest against the one before it', async () => {
    show({ agent_id: '' });
    await waitFor(() => expect(screen.getByTestId('skill-diff').textContent).toContain('+ 2. test'));
    expect(screen.getByText('v2')).toBeTruthy();
    expect(screen.getByText('add tests')).toBeTruthy();
  });

  it('restores an older version', async () => {
    show({ agent_id: '' });
    await waitFor(() => screen.getByText('v1'));
    fireEvent.click(screen.getByText('v1'));
    await waitFor(() => screen.getByText('Restore'));
    fireEvent.click(screen.getByText('Restore'));
    await waitFor(() => expect(restoreSkillVersion).toHaveBeenCalledWith('s1', 1));
  });

  it('offers pinning only on an attached skill', async () => {
    const { unmount } = show({ agent_id: '' });
    await waitFor(() => screen.getByText('v1'));
    fireEvent.click(screen.getByText('v1'));
    expect(screen.queryByText('Pin v1')).toBeNull();
    unmount();

    show({ agent_id: 'helper' });
    await waitFor(() => screen.getByText('v1'));
    fireEvent.click(screen.getByText('v1'));
    fireEvent.click(await screen.findByText('Pin v1'));
    await waitFor(() => expect(pinSkillVersion).toHaveBeenCalledWith('s1', 1));
  });

  it('keeps a repo catalog entry read only', async () => {
    show({ agent_id: '', source: 'repo' });
    await waitFor(() => screen.getByText('v1'));
    fireEvent.click(screen.getByText('v1'));
    expect(screen.queryByText('Restore')).toBeNull();
    expect(screen.getByText(/sync changes it/)).toBeTruthy();
  });
});

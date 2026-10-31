import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import MemoryManager from '../MemoryManager';
import { I18nProvider } from '../../i18n';

// Personal memory is set on its own tab of the Memory page, for the workspace
// picked in the header. The agent card and Settings link to it with
// ?tab=personal&workspace=<ws>: the page opens that tab and switches the
// header to that workspace.

const ok = (data) => Promise.resolve({ data });
const setSelectedWorkspace = vi.fn();

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'default', workspaceFilter: undefined, setSelectedWorkspace }),
}));

vi.mock('../../components/pageChat/pageChat', () => ({
  usePageChat: () => {},
  usePageChatPanel: () => ({ inlineSuppressed: false, setOpen: () => {} }),
}));

vi.mock('../../components/EntityChat', () => ({ default: () => null }));
vi.mock('../../components/memoryManager/PoolsTab', () => ({ PoolsTab: () => <section>POOLS TAB</section> }));
vi.mock('../../components/memoryManager/PersonalMemoryTab', () => ({
  PersonalMemoryTab: ({ workspaceFilter }) => <section>PERSONAL TAB {workspaceFilter || 'default'}</section>,
}));

vi.mock('../../components/memoryManager/useMemoryChatDescriptor', () => ({ useMemoryChatDescriptor: () => ({}) }));

vi.mock('../../api', () => ({
  getSharedMemories: () => ok([]),
}));

const show = (url) => render(
  <MemoryRouter initialEntries={[url]}><I18nProvider><MemoryManager /></I18nProvider></MemoryRouter>,
);

describe('Memory page personal memory tab', () => {
  beforeEach(() => { localStorage.clear(); setSelectedWorkspace.mockClear(); });

  it('opens on the tab a link names and switches to its workspace', async () => {
    show('/memory?tab=personal&workspace=alpha');
    expect(await screen.findByText(/PERSONAL TAB/)).toBeTruthy();
    await waitFor(() => expect(setSelectedWorkspace).toHaveBeenCalledWith('alpha'));
  });

  it('is one of the tabs, next to the pools', async () => {
    show('/memory');
    expect(await screen.findByText('POOLS TAB')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Personal memory/ }));
    expect(await screen.findByText('PERSONAL TAB default')).toBeTruthy();
    expect(setSelectedWorkspace).not.toHaveBeenCalled();
  });
});

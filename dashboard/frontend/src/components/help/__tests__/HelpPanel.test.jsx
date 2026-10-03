import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { WorkspaceContext } from '../../workspace';
import { PageChatProvider } from '../../pageChat/PageChatContext';
import PageChatPanel from '../../pageChat/PageChatPanel';
import HelpPanel from '../HelpPanel';

const tourStart = vi.fn();
vi.mock('../../docs/WelcomeTour', () => ({ useWelcomeTour: () => ({ start: tourStart }) }));

const helpApi = vi.hoisted(() => ({
  getHelpChat: vi.fn(),
  clearHelpChat: vi.fn(() => Promise.resolve({ data: {} })),
  stopHelpChat: vi.fn(() => Promise.resolve({ data: {} })),
  helpChatUrl: () => '/help-chat',
}));
vi.mock('../../../api/help', () => helpApi);

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}</div>;
}

const show = (path = '/tasks') => render(
  <MemoryRouter initialEntries={[path]}>
    <I18nProvider>
      <WorkspaceContext.Provider value={{ selectedWorkspace: 'default' }}>
        <PageChatProvider>
          <HelpPanel />
          <PageChatPanel />
          <Where />
        </PageChatProvider>
      </WorkspaceContext.Provider>
    </I18nProvider>
  </MemoryRouter>,
);

const helpButton = () => screen.getByRole('button', { name: /open help/i });

describe('the Help button and panel', () => {
  beforeEach(() => {
    tourStart.mockReset();
    helpApi.getHelpChat.mockReset();
    helpApi.getHelpChat.mockResolvedValue({ data: { messages: [], trace: [], chat_ref: null } });
  });

  it('is a labelled button that opens a dialog with starter prompts', async () => {
    show();
    const button = helpButton();
    expect(button).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('dialog', { name: 'Help' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'What can I do here?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'What should I set up next?' })).toBeInTheDocument();
  });

  it('keeps the page chat button out of the way while open', async () => {
    show();
    expect(screen.getByRole('button', { name: /open the page chat/i })).toBeInTheDocument();
    fireEvent.click(helpButton());
    await screen.findByRole('dialog', { name: 'Help' });
    expect(screen.queryByRole('button', { name: /open the page chat/i })).not.toBeInTheDocument();
  });

  it('closes on Escape and gives focus back to the button', async () => {
    show();
    fireEvent.click(helpButton());
    const close = screen.getByRole('button', { name: /close help/i });
    close.focus();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(helpButton()).toHaveFocus();
  });

  it('starts the welcome tour from its own button', () => {
    show();
    fireEvent.click(helpButton());
    fireEvent.click(screen.getByRole('button', { name: /take the tour/i }));
    expect(tourStart).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('turns a link in a reply into navigation and keeps the panel open', async () => {
    helpApi.getHelpChat.mockResolvedValue({
      data: {
        messages: [
          { role: 'user', content: 'what next?' },
          { role: 'assistant', content: 'Add a key on [Models](/models). Or [Take the tour](#tour).' },
        ],
        trace: [],
        chat_ref: null,
      },
    });
    show('/tasks');
    fireEvent.click(helpButton());

    fireEvent.click(await screen.findByRole('button', { name: 'Models' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/models');
    expect(screen.getByRole('dialog', { name: 'Help' })).toBeInTheDocument();

    const tourLinks = screen.getAllByRole('button', { name: 'Take the tour' });
    fireEvent.click(tourLinks[tourLinks.length - 1]);
    expect(tourStart).toHaveBeenCalledTimes(1);
  });
});

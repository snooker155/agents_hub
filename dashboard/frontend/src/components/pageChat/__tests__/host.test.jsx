import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { createPortal } from 'react-dom';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { WorkspaceContext } from '../../workspace';
import { PageChatProvider } from '../PageChatContext';
import PageChatPanel from '../PageChatPanel';
import { usePageChatHost } from '../pageChat';

/**
 * A page chat that is not an EntityChat (the task manager, the architect) is
 * drawn by its page inside the floating panel: the page's own button and the
 * corner launcher open the same panel, and it shows that chat.
 */

function HostedChat({ title = 'Architect' }) {
  const host = usePageChatHost({ title });
  return (
    <>
      <button type="button" onClick={host.toggle}>page button</button>
      {host.slot && createPortal(<div>hosted feed</div>, host.slot)}
      {host.tools && createPortal(<span>hosted tools</span>, host.tools)}
    </>
  );
}

// A parent that hosts nothing on this tab, around a child that does: the
// parent's registration must not wipe the child's.
function ParentWithoutHost({ children }) {
  usePageChatHost(null);
  return children;
}

const show = (page) => render(
  <MemoryRouter initialEntries={['/projects/p-1']}>
    <I18nProvider>
      <WorkspaceContext.Provider value={{ selectedWorkspace: 'default' }}>
        <PageChatProvider>
          {page}
          <PageChatPanel />
        </PageChatProvider>
      </WorkspaceContext.Provider>
    </I18nProvider>
  </MemoryRouter>,
);

const launcher = () => screen.queryByRole('button', { name: /open the page chat/i });

beforeEach(() => localStorage.removeItem('agents_hub_page_chat_open'));

describe('a hosted page chat', () => {
  it('opens in the panel from the corner launcher', () => {
    show(<HostedChat />);
    fireEvent.click(launcher());
    expect(screen.getByText('Architect')).toBeInTheDocument();
    expect(screen.getByText('hosted feed')).toBeInTheDocument();
    expect(screen.getByText('hosted tools')).toBeInTheDocument();
  });

  it('opens in the same panel from the page button, and closes from it', () => {
    show(<HostedChat />);
    fireEvent.click(screen.getByRole('button', { name: 'page button' }));
    expect(screen.getByText('hosted feed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'page button' }));
    expect(screen.queryByText('hosted feed')).not.toBeInTheDocument();
    expect(launcher()).toBeInTheDocument();
  });

  it('survives a parent that hosts nothing', () => {
    show(<ParentWithoutHost><HostedChat /></ParentWithoutHost>);
    fireEvent.click(launcher());
    expect(screen.getByText('hosted feed')).toBeInTheDocument();
  });
});

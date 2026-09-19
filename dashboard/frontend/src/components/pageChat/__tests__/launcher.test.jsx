import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useState } from 'react';
import { I18nProvider } from '../../../i18n';
import { WorkspaceContext } from '../../workspace';
import { PageChatProvider } from '../PageChatContext';
import PageChatPanel from '../PageChatPanel';
import { useInlineChatOpen } from '../pageChat';

/**
 * The launcher floats in the bottom-right corner, which is where a chat column
 * puts its send button. So a page showing a chat of its own takes the corner,
 * and gets it back when that chat closes.
 */

function FakeInlineChat({ initiallyVisible }) {
  const [visible, setVisible] = useState(initiallyVisible);
  useInlineChatOpen(visible);
  return (
    <button type="button" onClick={() => setVisible(false)}>
      {visible ? 'inline chat' : 'no chat'}
    </button>
  );
}

const show = (initiallyVisible) => render(
  <MemoryRouter initialEntries={['/projects/p-1']}>
    <I18nProvider>
      <WorkspaceContext.Provider value={{ selectedWorkspace: 'default' }}>
        <PageChatProvider>
          <FakeInlineChat initiallyVisible={initiallyVisible} />
          <PageChatPanel />
        </PageChatProvider>
      </WorkspaceContext.Provider>
    </I18nProvider>
  </MemoryRouter>,
);

// The launcher is the only button with the panel's accessible name; the fake
// page chat above is named for what it is.
const launcher = () => screen.queryByRole('button', { name: /open the page chat/i });

describe('the page-chat launcher', () => {
  it('is there on a page with no chat of its own', () => {
    show(false);
    expect(launcher()).toBeInTheDocument();
  });

  it('stands down while the page is showing a chat', () => {
    show(true);
    expect(launcher()).not.toBeInTheDocument();
  });

  it('comes back when that chat is closed', () => {
    show(true);
    expect(launcher()).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'inline chat' }));
    expect(launcher()).toBeInTheDocument();
  });
});

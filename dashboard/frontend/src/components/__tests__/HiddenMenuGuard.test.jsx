import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect } from 'vitest';
import HiddenMenuGuard from '../HiddenMenuGuard';
import { AuthContext, DEFAULT_AUTH } from '../auth';

const renderAt = (path, hidden_menu) => render(
  <AuthContext.Provider value={{ ...DEFAULT_AUTH, mode: 'multi', user: { role: 'member', hidden_menu } }}>
    <MemoryRouter initialEntries={[path]}>
      <HiddenMenuGuard>
        <Routes>
          <Route path="/chat" element={<p>chat page</p>} />
          <Route path="/models/*" element={<p>models page</p>} />
          <Route path="/agents" element={<p>agents page</p>} />
        </Routes>
      </HiddenMenuGuard>
    </MemoryRouter>
  </AuthContext.Provider>,
);

describe('HiddenMenuGuard', () => {
  it('sends a member to the chat from a hidden page and from under it', () => {
    renderAt('/models', ['/models']);
    expect(screen.getByText('chat page')).toBeTruthy();
  });

  it('sends a member away from a page under a hidden row', () => {
    renderAt('/models/openai/gpt', ['/models']);
    expect(screen.getByText('chat page')).toBeTruthy();
  });

  it('lets a page that is not hidden through', () => {
    renderAt('/agents', ['/models']);
    expect(screen.getByText('agents page')).toBeTruthy();
  });
});

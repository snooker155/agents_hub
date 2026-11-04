import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useState } from 'react';
import { WorkspaceContext } from '../workspace';
import useWorkspaceSwitchGuard, { listHolds, workspaceFilterOf } from '../useWorkspaceSwitchGuard';

// The workspace picker, as a field: typing a name switches to it.
function Host({ initial, children }) {
  const [selectedWorkspace, setSelectedWorkspace] = useState(initial);
  return (
    <WorkspaceContext.Provider value={{ selectedWorkspace, setSelectedWorkspace }}>
      <input aria-label="workspace" value={selectedWorkspace} onChange={(e) => setSelectedWorkspace(e.target.value)} />
      {children}
    </WorkspaceContext.Provider>
  );
}

const switchTo = (name) => fireEvent.change(screen.getByLabelText('workspace'), { target: { value: name } });

function Page({ isPresent }) {
  const { checking } = useWorkspaceSwitchGuard({ listPath: '/flows', isPresent });
  return <div>{checking ? 'checking' : 'flow page'}</div>;
}

const renderAt = (isPresent, initial = 'alpha') => render(
  <Host initial={initial}>
    <MemoryRouter initialEntries={['/flows/f1']}>
      <Routes>
        <Route path="/flows" element={<div>flow list</div>} />
        <Route path="/flows/:flowId" element={<Page isPresent={isPresent} />} />
      </Routes>
    </MemoryRouter>
  </Host>,
);

describe('useWorkspaceSwitchGuard', () => {
  it('does not check the workspace the page opened in', () => {
    const isPresent = vi.fn();
    renderAt(isPresent);
    expect(isPresent).not.toHaveBeenCalled();
    expect(screen.getByText('flow page')).toBeInTheDocument();
  });

  it('goes to the list when the new workspace does not hold the record', async () => {
    const isPresent = vi.fn(() => Promise.resolve(false));
    renderAt(isPresent);
    act(() => switchTo('beta'));
    await waitFor(() => expect(screen.getByText('flow list')).toBeInTheDocument());
    expect(isPresent).toHaveBeenCalledWith('beta', 'beta');
  });

  it('stays, checking until the answer, when the new workspace holds it', async () => {
    let answer;
    const isPresent = vi.fn(() => new Promise((resolve) => { answer = resolve; }));
    renderAt(isPresent);
    act(() => switchTo('beta'));
    expect(screen.getByText('checking')).toBeInTheDocument();
    await waitFor(() => expect(answer).toBeDefined());
    await act(async () => answer(true));
    expect(screen.getByText('flow page')).toBeInTheDocument();
  });

  it('stays when the list cannot be read', async () => {
    renderAt(() => Promise.reject(new Error('down')));
    act(() => switchTo('beta'));
    await waitFor(() => expect(screen.getByText('flow page')).toBeInTheDocument());
  });

  it('passes no filter for default, as the list pages do', async () => {
    const isPresent = vi.fn(() => true);
    renderAt(isPresent);
    act(() => switchTo('default'));
    await waitFor(() => expect(isPresent).toHaveBeenCalledWith('default', undefined));
    expect(workspaceFilterOf('')).toBeUndefined();
  });

  it('matches ids as strings and by the given key', () => {
    expect(listHolds([{ id: 7 }], '7')).toBe(true);
    expect(listHolds([{ loop_id: 'a' }], 'a', 'loop_id')).toBe(true);
    expect(listHolds(undefined, 'a')).toBe(false);
  });
});

import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../i18n';
import AgentConflictDialog from '../agent/AgentConflictDialog';

const handler = vi.hoisted(() => ({ fn: null }));
vi.mock('../../api/agentRevision', () => ({
  setAgentConflictHandler: (fn) => { handler.fn = fn; return () => { handler.fn = null; }; },
}));

describe('AgentConflictDialog', () => {
  it('asks and answers overwrite', async () => {
    render(<I18nProvider><AgentConflictDialog agentId="a1" /></I18nProvider>);
    expect(screen.queryByTestId('agent-conflict-dialog')).toBeNull();
    let decision;
    await act(async () => {
      decision = handler.fn({ agentId: 'a1', currentVersion: 5 });
    });
    expect(screen.getByText(/now v5/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /overwrite/i }));
    await expect(decision).resolves.toBe('overwrite');
    expect(screen.queryByTestId('agent-conflict-dialog')).toBeNull();
  });

  it('reloads through the callback and ignores other agents', async () => {
    const onReload = vi.fn();
    render(<I18nProvider><AgentConflictDialog agentId="a1" onReload={onReload} /></I18nProvider>);
    await expect(handler.fn({ agentId: 'other' })).resolves.toBe('cancel');
    let decision;
    await act(async () => {
      decision = handler.fn({ agentId: 'a1', currentVersion: null });
    });
    fireEvent.click(screen.getByRole('button', { name: /reload/i }));
    await expect(decision).resolves.toBe('reload');
    expect(onReload).toHaveBeenCalled();
  });
});

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';

// A workspace's data class (common/data_class.py): public, internal or
// confidential, saved on change; the server refuses non owners.

const ok = (data) => Promise.resolve({ data });
const getWorkspaceDataClass = vi.fn();
const setWorkspaceDataClass = vi.fn();

vi.mock('../../../api/isolation', () => ({
  getWorkspaceDataClass: (...a) => getWorkspaceDataClass(...a),
  setWorkspaceDataClass: (...a) => setWorkspaceDataClass(...a),
}));

import WorkspaceDataClass from '../WorkspaceDataClass';

const body = (data_class) => ({
  data_class,
  classes: ['public', 'internal', 'confidential'],
  default: 'internal',
  required_trust: { public: 'untrusted', internal: 'trusted', confidential: 'attested' },
});
const show = () => render(<I18nProvider><WorkspaceDataClass workspace="ws" /></I18nProvider>);

describe('WorkspaceDataClass', () => {
  it('shows the current class and what it needs', async () => {
    getWorkspaceDataClass.mockReturnValue(ok(body('internal')));
    show();
    const select = await screen.findByTestId('workspace-data-class');
    expect(select).toHaveValue('internal');
    expect(screen.getByTestId('data-class-description')).toHaveTextContent(/hub administrator trusts/i);
    expect(screen.getByText(/at least: Trusted/)).toBeInTheDocument();
  });

  it('saves a new class on change', async () => {
    getWorkspaceDataClass.mockReturnValue(ok(body('internal')));
    setWorkspaceDataClass.mockReturnValue(ok(body('public')));
    show();
    fireEvent.change(await screen.findByTestId('workspace-data-class'), { target: { value: 'public' } });
    await waitFor(() => expect(setWorkspaceDataClass).toHaveBeenCalledWith('ws', 'public'));
    await waitFor(() => expect(screen.getByTestId('workspace-data-class')).toHaveValue('public'));
  });

  it('shows the server reason when saving is refused', async () => {
    getWorkspaceDataClass.mockReturnValue(ok(body('internal')));
    setWorkspaceDataClass.mockRejectedValue({ response: { status: 403, data: { detail: 'Owner or admin only' } } });
    show();
    fireEvent.change(await screen.findByTestId('workspace-data-class'), { target: { value: 'confidential' } });
    await waitFor(() => expect(screen.getByTestId('data-class-error')).toHaveTextContent('Owner or admin only'));
    expect(screen.getByTestId('workspace-data-class')).toHaveValue('internal');
  });
});

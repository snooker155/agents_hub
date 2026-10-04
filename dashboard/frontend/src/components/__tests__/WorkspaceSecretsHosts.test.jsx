import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// A secret's allowed hosts: shown per row, set with a new secret, and edited
// in place without re-entering the value.

const ok = (data) => Promise.resolve({ data });
const getWorkspaceSecrets = vi.fn();
const setWorkspaceSecret = vi.fn(() => ok({}));
const setWorkspaceSecretHosts = vi.fn(() => ok({}));

vi.mock('../../api', () => ({
  getWorkspaceSecrets: (...a) => getWorkspaceSecrets(...a),
  setWorkspaceSecret: (...a) => setWorkspaceSecret(...a),
  setWorkspaceSecretHosts: (...a) => setWorkspaceSecretHosts(...a),
  deleteWorkspaceSecret: vi.fn(() => ok({})),
  getUsers: vi.fn(() => ok([])),
}));

vi.mock('../auth', () => ({ MULTI: 'multi', useAuth: () => ({ mode: 'single' }) }));

import WorkspaceSecrets from '../workspace/WorkspaceSecrets';

const ROWS = [
  { name: 'GITHUB_TOKEN', agent_id: '', user_id: '', hint: 'x9Qa', allowed_hosts: ['github.com'] },
  { name: 'OTHER', agent_id: '', user_id: '', hint: '****', allowed_hosts: [] },
];

const show = () => render(<I18nProvider><WorkspaceSecrets workspace="alpha" agents={[]} /></I18nProvider>);

describe('WorkspaceSecrets hosts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getWorkspaceSecrets.mockImplementation(() => ok(ROWS));
  });

  it('shows each secret\'s hosts, or any host', async () => {
    show();
    const cells = await screen.findAllByTestId('secret-hosts');
    expect(cells[0]).toHaveTextContent('github.com');
    expect(cells[1]).toHaveTextContent('any host');
  });

  it('sends the hosts with a new secret', async () => {
    show();
    await screen.findAllByTestId('secret-hosts');
    fireEvent.change(screen.getByPlaceholderText('Name, e.g. GITHUB_TOKEN'), { target: { value: 'API_KEY' } });
    fireEvent.change(screen.getByPlaceholderText('Value'), { target: { value: 'secret-value' } });
    fireEvent.change(screen.getByLabelText('Allowed hosts, e.g. api.github.com'),
      { target: { value: 'api.example.com, example.org' } });
    fireEvent.click(screen.getByText('Save secret'));
    await waitFor(() => expect(setWorkspaceSecret).toHaveBeenCalled());
    expect(setWorkspaceSecret.mock.calls[0][2]).toEqual({
      value: 'secret-value', allowed_hosts: ['api.example.com', 'example.org'],
    });
  });

  it('edits the hosts of an existing secret in place', async () => {
    show();
    await screen.findAllByTestId('secret-hosts');
    fireEvent.click(screen.getByRole('button', { name: 'Edit hosts of OTHER' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Edit hosts of OTHER' }),
      { target: { value: 'hooks.slack.com' } });
    fireEvent.click(screen.getByText('Save hosts'));
    await waitFor(() => expect(setWorkspaceSecretHosts).toHaveBeenCalled());
    expect(setWorkspaceSecretHosts.mock.calls[0]).toEqual(['alpha', 'OTHER', { allowed_hosts: ['hooks.slack.com'] }]);
  });
});

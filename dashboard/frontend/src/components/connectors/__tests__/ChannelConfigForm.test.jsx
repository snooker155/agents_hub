import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getGmailStatus: vi.fn() }));
vi.mock('../../../api', () => api);
vi.mock('../../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'default' }) }));
vi.mock('../../stream', () => ({ useLiveRefetch: () => {} }));

import { ConfigForm } from '../ChannelConnector';

const t = (k, vars) => (vars?.defaultValue !== undefined ? vars.defaultValue : vars ? `${k} ${JSON.stringify(vars)}` : k);
const GOOGLE = { auth_mode: 'google' };
// The mail channel's fields as connectors/mail/__init__.py describes them, trimmed.
const FIELDS = [
  { key: 'auth_mode', kind: 'select', options: ['password', 'google'] },
  { key: 'imap_host', kind: 'text', required: true, optional_when: GOOGLE },
  { key: 'imap_password', kind: 'password', secret: true, required: true, hidden_when: GOOGLE },
  { key: 'smtp_password', kind: 'password', secret: true, hidden_when: GOOGLE },
  { key: 'from_address', kind: 'text', required: true, optional_when: GOOGLE },
];

const renderForm = (onSave = vi.fn()) => render(
  <MemoryRouter>
    <ConfigForm name="mail" fields={FIELDS} presets={[]} config={{ auth_mode: 'password' }} onSave={onSave} t={t} />
  </MemoryRouter>,
);

describe('mail channel ConfigForm with a Google sign in', () => {
  beforeEach(() => {
    api.getGmailStatus.mockReset();
    api.getGmailStatus.mockResolvedValue({ data: { connected: true, gmail: false, account_email: 'anna@gmail.com' } });
  });

  it('hides the passwords, relaxes the hosts and says what Google lacks', async () => {
    const onSave = vi.fn();
    const { container } = renderForm(onSave);
    expect(container.querySelectorAll('input[type=password]')).toHaveLength(2);
    expect(container.querySelectorAll('.text-red-500')).toHaveLength(3);
    fireEvent.change(container.querySelector('select'), { target: { value: 'google' } });
    expect(container.querySelectorAll('input[type=password]')).toHaveLength(0);
    expect(container.querySelectorAll('.text-red-500')).toHaveLength(0);
    await waitFor(() => expect(screen.getByTestId('gmail-note')).toHaveTextContent('mailPresets.google.noGmail'));
    expect(screen.getByText('mailPresets.google.open').closest('a')).toHaveAttribute('href', '/connectors?tab=google');
    expect(screen.getByRole('option', { name: 'google' })).toBeInTheDocument();
    fireEvent.click(screen.getByText('common.save'));
    expect(onSave.mock.calls[0][0]).toMatchObject({ auth_mode: 'google' });
  });

  it('does not ask Google anything in password mode', () => {
    renderForm();
    expect(api.getGmailStatus).not.toHaveBeenCalled();
    expect(screen.queryByTestId('gmail-note')).toBeNull();
  });
});

import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getConsentSettings: vi.fn(),
  updateConsentSettings: vi.fn(),
  listConsentGrants: vi.fn(),
  revokeConsentGrant: vi.fn(),
}));
vi.mock('../../api/consent', () => api);
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'w1' }) }));
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k }) }));

import AgentConsentCard from '../agent/AgentConsentCard';

const catalog = {
  providers: [
    { id: 'google', label: 'Google', access: ['calendar', 'drive_read'], default: ['calendar'], ready: true },
    { id: 'microsoft', label: 'Microsoft', access: ['calendar', 'mail_read'], default: ['calendar'], ready: false },
  ],
  redirect_uri: 'https://hub.example/consent/callback',
};

describe('AgentConsentCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getConsentSettings.mockResolvedValue({ data: { providers: [], scopes: {}, catalog } });
    api.updateConsentSettings.mockImplementation((_id, payload) => Promise.resolve({ data: { ...payload, catalog } }));
    api.listConsentGrants.mockResolvedValue({ data: { grants: [{
      request_id: 'r1', agent_id: 'a1', provider: 'google', access: ['calendar'],
      principal: 'widget:w:vis_1', principal_kind: 'widget', account_email: 'v@gmail.com',
    }] } });
    api.revokeConsentGrant.mockResolvedValue({ data: { ok: true, revoked: true } });
  });

  it('shows the redirect URI and the grants of the workspace', async () => {
    render(<AgentConsentCard agentId="a1" />);
    expect(await screen.findByTestId('consent-redirect-uri')).toHaveTextContent('https://hub.example/consent/callback');
    expect(await screen.findByText('v@gmail.com')).toBeInTheDocument();
    expect(api.listConsentGrants).toHaveBeenCalledWith('w1', 'a1');
    expect(api.getConsentSettings).toHaveBeenCalledWith('a1', 'w1');
    expect(screen.getByText('consent.notReady')).toBeInTheDocument();
  });

  it('switches a provider on with its default access and saves, carrying the workspace', async () => {
    render(<AgentConsentCard agentId="a1" />);
    fireEvent.click(await screen.findByLabelText('Google'));
    fireEvent.click(screen.getByRole('button', { name: 'consent.save' }));
    await waitFor(() => expect(api.updateConsentSettings).toHaveBeenCalledWith(
      'a1', { providers: ['google'], scopes: { google: ['calendar'] } }, 'w1'));
    expect(await screen.findByText('consent.saved')).toBeInTheDocument();
  });

  it('revokes a grant after confirming', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AgentConsentCard agentId="a1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'consent.revoke' }));
    await waitFor(() => expect(api.revokeConsentGrant).toHaveBeenCalledWith('r1', 'w1'));
    expect(api.listConsentGrants).toHaveBeenCalledTimes(2);
  });
});

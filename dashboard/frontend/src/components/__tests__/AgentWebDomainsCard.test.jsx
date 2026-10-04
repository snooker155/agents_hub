import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });
const getAgentWebDomains = vi.fn();
const updateAgentWebDomains = vi.fn();

vi.mock('../../api/agentWebDomains', () => ({
  getAgentWebDomains: (...a) => getAgentWebDomains(...a),
  updateAgentWebDomains: (...a) => updateAgentWebDomains(...a),
}));
vi.mock('../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'alpha' }) }));

import AgentWebDomainsCard from '../agent/AgentWebDomainsCard';

const show = (props = {}) => render(
  <I18nProvider><AgentWebDomainsCard agentId="researcher" {...props} /></I18nProvider>,
);

describe('AgentWebDomainsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentWebDomains.mockImplementation(() => ok({
      allowed_domains: ['docs.python.org'], blocked_domains: ['tracker.example'],
      effective: { blocked: ['evil.example', 'tracker.example'], allowed: null, agent_allowed: ['docs.python.org'] },
    }));
    updateAgentWebDomains.mockImplementation((_id, body) => ok(body));
  });

  it('loads the lists for the current workspace and shows what it adds', async () => {
    show();
    expect(await screen.findByDisplayValue('docs.python.org')).toBeTruthy();
    expect(getAgentWebDomains).toHaveBeenCalledWith('researcher', 'alpha');
    const effective = screen.getByTestId('web-domains-effective');
    expect(effective).toHaveTextContent('evil.example');
    expect(effective).not.toHaveTextContent('tracker.example');
    expect(effective).toHaveTextContent('any host');
  });

  it('saves both lists, one host per line', async () => {
    show();
    const allowed = await screen.findByLabelText('Allowed domains');
    fireEvent.change(allowed, { target: { value: 'docs.python.org\narxiv.org' } });
    fireEvent.click(screen.getByText('Save domains'));
    await waitFor(() => expect(updateAgentWebDomains).toHaveBeenCalled());
    expect(updateAgentWebDomains.mock.calls[0]).toEqual(['researcher', {
      allowed_domains: ['docs.python.org', 'arxiv.org'], blocked_domains: ['tracker.example'],
    }]);
    expect(await screen.findByText('Domain lists saved.')).toBeTruthy();
  });

  it('is read only for a system agent', async () => {
    show({ readOnly: true });
    expect(await screen.findByLabelText('Allowed domains')).toBeDisabled();
    expect(screen.queryByText('Save domains')).toBeNull();
  });
});

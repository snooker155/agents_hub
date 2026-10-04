import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

const decideToolApproval = vi.fn();
const listRunApprovals = vi.fn();
vi.mock('../../../api/toolApprovals', () => ({
  decideToolApproval: (...a) => decideToolApproval(...a),
  listRunApprovals: (...a) => listRunApprovals(...a),
}));

const applyConnectionProposal = vi.fn();
vi.mock('../../../api/connectionProposals', () => ({
  applyConnectionProposal: (...a) => applyConnectionProposal(...a),
}));

import { ToolApprovalCard } from '../ToolApprovalCard';
import { ConnectionProposalCard } from '../ConnectionProposalCard';

// A `propose_connection` tool approval (connection-proposals-contract.md): the
// agent fills non-secret fields, the person types secrets and presses Connect.

const PROPOSAL = {
  approval_id: 'appr_cx', run_id: 'run-1', tool: 'propose_connection', status: 'pending',
  reason: 'Jira tickets came up twice in this chat.',
  expires_at: '2026-10-03T12:30:00+00:00',
  input: {
    kind: 'connector',
    target: 'jira',
    title: 'Jira',
    scope: 'workspace',
    workspace: 'demo',
    fields: [
      { key: 'base_url', kind: 'text', secret: false, required: true, value: 'https://acme.atlassian.net', placeholder: '', options: [], has_value: false },
      { key: 'api_token', kind: 'password', secret: true, required: true, value: '', placeholder: '', options: [], has_value: false },
    ],
    warnings: ['Runs on the hub\'s host.'],
    replaces: false,
  },
};

// Same proposal, but the secret is already stored: Connect does not need a
// freshly typed secret to be enabled, which is what most of the outcome and
// error-path tests below care about.
const STORED = {
  ...PROPOSAL,
  input: { ...PROPOSAL.input, fields: [PROPOSAL.input.fields[0], { ...PROPOSAL.input.fields[1], has_value: true }] },
};

const show = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('ConnectionProposalCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the title, the proposal title, the kind badge and the reason', () => {
    show(<ConnectionProposalCard approval={PROPOSAL} />);
    expect(screen.getByText('Agent proposes a connection')).toBeTruthy();
    expect(screen.getByText('Jira')).toBeTruthy();
    expect(screen.getByText('Connector')).toBeTruthy();
    expect(screen.getByText(/Jira tickets came up twice/)).toBeTruthy();
    expect(screen.getByText(/Runs on the hub's host/)).toBeTruthy();
    expect(screen.getByText('Waiting for you')).toBeTruthy();
  });

  it('renders a field per entry, with readable labels', () => {
    show(<ConnectionProposalCard approval={PROPOSAL} />);
    expect(screen.getByText('Base url')).toBeTruthy();
    expect(screen.getByText('Api token')).toBeTruthy();
  });

  it('disables Connect until every required field is filled', () => {
    const noUrl = {
      ...PROPOSAL,
      input: { ...PROPOSAL.input, fields: [{ ...PROPOSAL.input.fields[0], value: '' }, PROPOSAL.input.fields[1]] },
    };
    show(<ConnectionProposalCard approval={noUrl} />);
    const connectBtn = screen.getByRole('button', { name: 'Connect' });
    expect(connectBtn.disabled).toBe(true);
  });

  it('a required secret already stored (has_value) does not block Connect', () => {
    const stored = {
      ...PROPOSAL,
      input: {
        ...PROPOSAL.input,
        fields: [PROPOSAL.input.fields[0], { ...PROPOSAL.input.fields[1], has_value: true }],
      },
    };
    show(<ConnectionProposalCard approval={stored} />);
    expect(screen.getByRole('button', { name: 'Connect' }).disabled).toBe(false);
  });

  it('sends non-secret values and only the non-empty secrets the person typed', async () => {
    applyConnectionProposal.mockResolvedValue({ data: { approval: { ...PROPOSAL, status: 'approved' }, outcome: { ok: true, summary: 'Connected to Jira.' } } });
    show(<ConnectionProposalCard approval={PROPOSAL} />);
    fireEvent.change(screen.getByDisplayValue('https://acme.atlassian.net'), { target: { value: 'https://acme2.atlassian.net' } });
    const secretInput = document.querySelector('input[type="password"]');
    fireEvent.change(secretInput, { target: { value: ' secret-token ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(applyConnectionProposal).toHaveBeenCalledWith('appr_cx', {
      values: { base_url: 'https://acme2.atlassian.net' },
      secrets: { api_token: 'secret-token' },
    }));
  });

  it('omits an empty secret from the request', async () => {
    applyConnectionProposal.mockResolvedValue({ data: { approval: { ...STORED, status: 'approved' }, outcome: { ok: true, summary: 'Connected.' } } });
    show(<ConnectionProposalCard approval={STORED} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(applyConnectionProposal).toHaveBeenCalledWith('appr_cx', {
      values: { base_url: 'https://acme.atlassian.net' },
      secrets: {},
    }));
  });

  it('shows the outcome summary and a link after a successful connect, and turns to Approved', async () => {
    applyConnectionProposal.mockResolvedValue({
      data: { approval: { ...STORED, status: 'approved' }, outcome: { ok: true, summary: 'Connected to Jira.', href: '/connectors?tab=trackers' } },
    });
    show(<ConnectionProposalCard approval={STORED} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.getByText('Approved')).toBeTruthy());
    expect(screen.getByTestId('connection-proposal-outcome').textContent).toMatch(/Connected to Jira\./);
    expect(screen.getByRole('link', { name: 'Open' }).getAttribute('href')).toBe('/connectors?tab=trackers');
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
  });

  it('shows the backend detail on a 400 and keeps the form open', async () => {
    applyConnectionProposal.mockRejectedValue({ response: { status: 400, data: { detail: 'base_url must be an https URL' } } });
    show(<ConnectionProposalCard approval={STORED} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('base_url must be an https URL'));
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('reads detail.message when detail is an object', async () => {
    applyConnectionProposal.mockRejectedValue({ response: { status: 400, data: { detail: { message: 'bad token format' } } } });
    show(<ConnectionProposalCard approval={STORED} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('bad token format'));
  });

  it('maps 403 the same way the approval card does', async () => {
    applyConnectionProposal.mockRejectedValue({ response: { status: 403 } });
    show(<ConnectionProposalCard approval={STORED} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/owner or an admin/));
  });

  it('denies with a note, like a tool approval', async () => {
    decideToolApproval.mockResolvedValue({ data: { approval: { ...PROPOSAL, status: 'denied' } } });
    show(<ConnectionProposalCard approval={PROPOSAL} />);
    fireEvent.change(screen.getByLabelText('Note for the agent'), { target: { value: 'not now' } });
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(decideToolApproval).toHaveBeenCalledWith('appr_cx', 'deny', 'not now'));
    await waitFor(() => expect(screen.getByText('Denied')).toBeTruthy());
  });

  it('calls onSettled after a successful connect', async () => {
    const onSettled = vi.fn();
    applyConnectionProposal.mockResolvedValue({ data: { approval: { ...STORED, status: 'approved' }, outcome: { ok: true, summary: 'ok' } } });
    show(<ConnectionProposalCard approval={STORED} onSettled={onSettled} />);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
  });

  it('calls onSettled after a deny', async () => {
    const onSettled = vi.fn();
    decideToolApproval.mockResolvedValue({ data: { approval: { ...PROPOSAL, status: 'denied' } } });
    show(<ConnectionProposalCard approval={PROPOSAL} onSettled={onSettled} />);
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
  });

  it('shows the note and who decided once the turn is over, with no outcome from history', () => {
    show(<ConnectionProposalCard approval={{ ...PROPOSAL, status: 'denied', decided_by_name: 'ann', note: 'use staging' }} />);
    expect(screen.getByText('Denied')).toBeTruthy();
    expect(screen.getByTestId('connection-proposal-outcome-note').textContent).toBe('Answered by ann · Note: use staging');
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.queryByTestId('connection-proposal-outcome')).toBeNull();
  });

  it('shows a note about replacing an existing connection', () => {
    const replaces = { ...PROPOSAL, input: { ...PROPOSAL.input, replaces: true } };
    show(<ConnectionProposalCard approval={replaces} />);
    expect(screen.getByText(/overwrites the connection already configured for Jira/)).toBeTruthy();
  });

  it('offers no buttons once the turn is over', () => {
    show(<ConnectionProposalCard approval={PROPOSAL} live={false} />);
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull();
  });
});

describe('ToolApprovalCard delegation', () => {
  it('renders ConnectionProposalCard for a propose_connection approval', () => {
    show(<ToolApprovalCard approval={PROPOSAL} />);
    expect(screen.getByTestId('connection-proposal-card')).toBeTruthy();
    expect(screen.getByText('Agent proposes a connection')).toBeTruthy();
  });

  it('still renders the generic card for every other tool', () => {
    show(<ToolApprovalCard approval={{ approval_id: 'a1', tool: 'run_shell', status: 'pending', input: { command: 'ls' } }} />);
    expect(screen.getByTestId('tool-approval-card')).toBeTruthy();
    expect(screen.queryByTestId('connection-proposal-card')).toBeNull();
  });
});

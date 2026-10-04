import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Sequence guardrails: rules about a run's tool calls. The modal takes the
// rule type and its fields, the stage is fixed to tool calls and the action
// is block or ask; the Test box runs pasted calls.

const ok = (data) => Promise.resolve({ data });
const getGuardrails = vi.fn(() => ok([]));
const createGuardrail = vi.fn(() => ok({ id: 'g-new' }));
const testGuardrailCalls = vi.fn();

vi.mock('../../api/guardrails', () => ({
  getGuardrails: (...a) => getGuardrails(...a),
  createGuardrail: (...a) => createGuardrail(...a),
  updateGuardrail: vi.fn(() => ok({})),
  archiveGuardrail: vi.fn(() => ok({})),
  deleteGuardrail: vi.fn(() => ok({})),
  testGuardrail: vi.fn(() => ok({})),
  testGuardrailCalls: (...a) => testGuardrailCalls(...a),
  getGuardrailEvents: vi.fn(() => ok([])),
}));
vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, selectedWorkspace: 'default' }),
}));

import Guardrails from '../Guardrails';

const show = () => render(<I18nProvider><MemoryRouter><Guardrails /></MemoryRouter></I18nProvider>);

const SEQUENCE = {
  id: 'g-seq', name: 'transfer cap', description: '', workspace: null, stage: 'tool', kind: 'sequence',
  config: { rule: 'sum_max', tools: ['pay'], argument: 'amount', max: 100 }, action: 'ask',
  applies_to: 'all', enabled: true, fail_closed: true, model: null, archived_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  getGuardrails.mockImplementation(() => ok([]));
});

describe('Guardrails: sequence rules', () => {
  it('creates a sum_max rule that asks a person', async () => {
    show();
    await screen.findByText(/no guardrails yet/i);
    fireEvent.click(screen.getByRole('button', { name: /new guardrail/i }));
    fireEvent.change(screen.getByPlaceholderText(/no-secrets-out/i), { target: { value: 'transfer cap' } });
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[2], { target: { value: 'sequence' } });
    // The stage is fixed to tool calls, the actions are block and ask.
    expect(screen.getByRole('option', { name: 'Tool calls' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /^Warn/ })).toBeNull();
    fireEvent.change(screen.getAllByRole('combobox')[3], { target: { value: 'ask' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Rule' }), { target: { value: 'sum_max' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Tools' }), { target: { value: 'pay\nrefund' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Argument' }), { target: { value: 'amount' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Limit per run' }), { target: { value: '500' } });
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(createGuardrail).toHaveBeenCalled());
    const payload = createGuardrail.mock.calls[0][0];
    expect(payload).toMatchObject({
      kind: 'sequence', stage: 'tool', action: 'ask',
      config: { rule: 'sum_max', tools: ['pay', 'refund'], argument: 'amount', max: 500 },
    });
  });

  it('shows the rule in the list and tests it on pasted calls', async () => {
    getGuardrails.mockImplementation(() => ok([SEQUENCE]));
    testGuardrailCalls.mockImplementation(() => ok({
      applies: true, passed: false, index: 1, tool: 'pay', reason: 'over the limit',
    }));
    show();
    await screen.findByText('transfer cap');
    expect(screen.getByText('Tool calls')).toBeTruthy();
    expect(screen.getByText('Tool call sequence')).toBeTruthy();
    fireEvent.click(screen.getByTitle('Test'));
    const box = await screen.findByRole('textbox', { name: 'Tool calls' });
    const calls = [{ tool: 'pay', input: { amount: 60 } }, { tool: 'pay', input: { amount: 60 } }];
    fireEvent.change(box, { target: { value: JSON.stringify(calls) } });
    fireEvent.click(screen.getByRole('button', { name: /run test/i }));
    await waitFor(() => expect(testGuardrailCalls).toHaveBeenCalledWith('g-seq', calls));
    expect(await screen.findByText(/Would ask a person about call 2 \(pay\): over the limit/)).toBeTruthy();
  });

  it('refuses calls that are not a JSON list', async () => {
    getGuardrails.mockImplementation(() => ok([SEQUENCE]));
    show();
    await screen.findByText('transfer cap');
    fireEvent.click(screen.getByTitle('Test'));
    fireEvent.change(await screen.findByRole('textbox', { name: 'Tool calls' }), { target: { value: '{"tool": 1}' } });
    fireEvent.click(screen.getByRole('button', { name: /run test/i }));
    expect(await screen.findByText('The calls must be a JSON list.')).toBeTruthy();
    expect(testGuardrailCalls).not.toHaveBeenCalled();
  });
});

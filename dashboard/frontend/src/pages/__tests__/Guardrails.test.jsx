import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

const ok = (data) => Promise.resolve({ data });

const getGuardrails = vi.fn(() => ok([]));
const createGuardrail = vi.fn(() => ok({ id: 'g-new' }));
const updateGuardrail = vi.fn(() => ok({}));
const archiveGuardrail = vi.fn(() => ok({}));
const deleteGuardrail = vi.fn(() => ok({}));
const testGuardrail = vi.fn(() => ok({ applies: true, passed: true, reason: '' }));
const NO_EVENTS = { items: [], total: 0 };
const getGuardrailEvents = vi.fn(() => ok(NO_EVENTS));

vi.mock('../../api/guardrails', () => ({
  getGuardrails: (...args) => getGuardrails(...args),
  createGuardrail: (...args) => createGuardrail(...args),
  updateGuardrail: (...args) => updateGuardrail(...args),
  archiveGuardrail: (...args) => archiveGuardrail(...args),
  deleteGuardrail: (...args) => deleteGuardrail(...args),
  testGuardrail: (...args) => testGuardrail(...args),
  getGuardrailEvents: (...args) => getGuardrailEvents(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, selectedWorkspace: 'default' }),
}));

import Guardrails from '../Guardrails';

const show = () => render(
  <I18nProvider><MemoryRouter><Guardrails /></MemoryRouter></I18nProvider>,
);

const GUARDRAIL = {
  id: 'g-1',
  name: 'no-secrets-out',
  description: 'Blocks API keys leaking in the answer',
  workspace: null,
  stage: 'output',
  kind: 'pii',
  config: { detectors: ['api_key'] },
  action: 'block',
  applies_to: 'all',
  enabled: true,
  fail_closed: true,
  model: null,
  archived_at: null,
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-20T10:00:00Z',
};

beforeEach(() => {
  getGuardrails.mockClear();
  createGuardrail.mockClear();
  updateGuardrail.mockClear();
  archiveGuardrail.mockClear();
  deleteGuardrail.mockClear();
  testGuardrail.mockClear();
  getGuardrailEvents.mockClear();
  getGuardrails.mockImplementation(() => ok([]));
  getGuardrailEvents.mockImplementation(() => ok(NO_EVENTS));
});

describe('Guardrails — empty state', () => {
  it('says there is nothing yet', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no guardrails yet/i)).toBeInTheDocument());
  });
});

describe('Guardrails — the list', () => {
  it('shows a guardrail with its stage, kind and action', async () => {
    getGuardrails.mockImplementation(() => ok([GUARDRAIL]));
    show();
    await screen.findByText('no-secrets-out', { selector: 'span' });
    expect(screen.getByText('Blocks API keys leaking in the answer')).toBeInTheDocument();
    expect(getGuardrails).toHaveBeenCalledWith(undefined, false);
  });

  it('archives a guardrail', async () => {
    getGuardrails.mockImplementation(() => ok([GUARDRAIL]));
    show();
    await screen.findByText('no-secrets-out', { selector: 'span' });
    fireEvent.click(screen.getByTitle('Archive'));
    await waitFor(() => expect(archiveGuardrail).toHaveBeenCalledWith('g-1'));
  });

  it('deletes a guardrail after confirming', async () => {
    getGuardrails.mockImplementation(() => ok([GUARDRAIL]));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    show();
    await screen.findByText('no-secrets-out', { selector: 'span' });
    fireEvent.click(screen.getByTitle('Delete'));
    await waitFor(() => expect(deleteGuardrail).toHaveBeenCalledWith('g-1'));
  });

  it('runs a dry-run test from the inline test box', async () => {
    getGuardrails.mockImplementation(() => ok([GUARDRAIL]));
    show();
    await screen.findByText('no-secrets-out', { selector: 'span' });
    fireEvent.click(screen.getByTitle('Test'));
    const textarea = await screen.findByPlaceholderText(/paste text to check/i);
    fireEvent.change(textarea, { target: { value: 'sk-abcdefghijklmnop' } });
    fireEvent.click(screen.getByRole('button', { name: /run test/i }));
    await waitFor(() => expect(testGuardrail).toHaveBeenCalledWith('g-1', 'sk-abcdefghijklmnop', 'output'));
  });
});

describe('Guardrails — create', () => {
  it('creates a keywords guardrail', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no guardrails yet/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /new guardrail/i }));

    fireEvent.change(screen.getByPlaceholderText(/no-secrets-out/i), { target: { value: 'no-swearing' } });
    const kindSelects = within(screen.getByRole('dialog')).getAllByRole('combobox');
    // scope, stage, kind, action, appliesTo — kind is the third select in the modal.
    fireEvent.change(kindSelects[2], { target: { value: 'keywords' } });
    const keywordsBox = await screen.findByPlaceholderText(/one per line/i);
    fireEvent.change(keywordsBox, { target: { value: 'badword' } });

    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(createGuardrail).toHaveBeenCalled());
    const payload = createGuardrail.mock.calls[0][0];
    expect(payload.name).toBe('no-swearing');
    expect(payload.kind).toBe('keywords');
    expect(payload.config.keywords).toEqual(['badword']);
  });
});

describe('Guardrails — the event log', () => {
  const event = (i) => ({
    id: `e-${i}`, at: '2026-10-27T10:00:00Z', guardrail_id: 'g-1', guardrail_name: 'no-secrets-out',
    stage: 'output', action: 'block', reason: 'contains what looks like api key', excerpt: '****', run_id: '',
  });

  it('filters by guardrail and loads the next batch', async () => {
    getGuardrails.mockImplementation(() => ok([GUARDRAIL]));
    getGuardrailEvents.mockImplementation((params) => ok({
      items: Array.from({ length: Math.min(params.limit, 25) }, (_, i) => event(i)), total: 25,
    }));
    show();
    await screen.findByText('25 found');
    expect(getGuardrailEvents).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 20, offset: 0, sort: 'at' }));

    fireEvent.click(screen.getByRole('button', { name: /load 5 more/i }));
    await waitFor(() => expect(getGuardrailEvents).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 40 })));

    fireEvent.change(screen.getByRole('combobox', { name: 'Guardrail' }), { target: { value: 'g-1' } });
    await waitFor(() => expect(getGuardrailEvents).toHaveBeenLastCalledWith(
      expect.objectContaining({ guardrail_id: 'g-1', limit: 20 })));
  });
});

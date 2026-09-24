import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getAgentLoopSettings: vi.fn(),
  updateAgentLoopSettings: vi.fn(),
  getModelsCatalog: vi.fn(),
  flattenModelCatalog: (raw) => {
    const out = [];
    Object.entries(raw || {}).forEach(([provider, entry]) => {
      (entry?.models || []).forEach((m) => {
        if (m?.enabled && m?.id) out.push({ id: `${provider}/${m.id}`, provider, model: m.id });
      });
    });
    return out;
  },
}));
vi.mock('../../api/agentLoop', () => api);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import LoopSettingsCard from '../agent/LoopSettingsCard';

const settingsBody = (overrides = {}) => ({
  fallback_models: [], output_schema: null, tool_search: null, compaction: null, ...overrides,
});

const catalogBody = () => ({
  openai: { default: '', models: [{ id: 'gpt-4o-mini', enabled: true }, { id: 'gpt-4o', enabled: true }] },
  anthropic: { default: '', models: [{ id: 'claude-haiku', enabled: true }] },
});

describe('LoopSettingsCard', () => {
  beforeEach(() => {
    api.getAgentLoopSettings.mockReset();
    api.updateAgentLoopSettings.mockReset();
    api.getModelsCatalog.mockReset();
    api.getAgentLoopSettings.mockResolvedValue({ data: settingsBody() });
    api.getModelsCatalog.mockResolvedValue({ data: catalogBody() });
    api.updateAgentLoopSettings.mockResolvedValue({ data: settingsBody() });
  });

  it('loads and shows the empty fallback list', async () => {
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('agentLoop.fallback.empty');
    expect(api.getAgentLoopSettings).toHaveBeenCalledWith('a1');
    expect(screen.getByRole('button', { name: 'agentLoop.save' })).toBeDisabled();
  });

  it('adds a fallback model from the catalog and saves it in order', async () => {
    const onSaved = vi.fn();
    api.updateAgentLoopSettings.mockResolvedValue({
      data: settingsBody({ fallback_models: ['openai/gpt-4o-mini', 'anthropic/claude-haiku'] }),
    });
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} onSaved={onSaved} />);
    await screen.findByText('agentLoop.fallback.empty');

    const picker = screen.getByLabelText('agentLoop.fallback.addLabel');
    fireEvent.change(picker, { target: { value: 'openai/gpt-4o-mini' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.fallback.add' }));
    fireEvent.change(picker, { target: { value: 'anthropic/claude-haiku' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.fallback.add' }));

    expect(screen.getByText('openai/gpt-4o-mini')).toBeInTheDocument();
    expect(screen.getByText('anthropic/claude-haiku')).toBeInTheDocument();

    const save = screen.getByRole('button', { name: 'agentLoop.save' });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    await waitFor(() => expect(api.updateAgentLoopSettings).toHaveBeenCalledWith('a1', {
      fallback_models: ['openai/gpt-4o-mini', 'anthropic/claude-haiku'],
      output_schema: null, tool_search: null, compaction: null,
    }));
    expect(await screen.findByText('agentLoop.saved')).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalled();
  });

  it('reorders and removes a fallback model', async () => {
    api.getAgentLoopSettings.mockResolvedValue({
      data: settingsBody({ fallback_models: ['openai/gpt-4o-mini', 'openai/gpt-4o'] }),
    });
    // Echo back whatever was saved, like the real route does, so the second
    // save in this test acts on the list the first save left behind.
    api.updateAgentLoopSettings.mockImplementation((_id, payload) => (
      Promise.resolve({ data: settingsBody(payload) })
    ));
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('openai/gpt-4o-mini');

    fireEvent.click(screen.getByLabelText('agentLoop.fallback.moveDown {"model":"openai/gpt-4o-mini"}'));
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));
    await waitFor(() => expect(api.updateAgentLoopSettings).toHaveBeenCalledWith('a1', expect.objectContaining({
      fallback_models: ['openai/gpt-4o', 'openai/gpt-4o-mini'],
    })));

    fireEvent.click(screen.getByLabelText('agentLoop.fallback.remove {"model":"openai/gpt-4o"}'));
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));
    await waitFor(() => expect(api.updateAgentLoopSettings).toHaveBeenLastCalledWith('a1', expect.objectContaining({
      fallback_models: ['openai/gpt-4o-mini'],
    })));
  });

  it('rejects invalid JSON in the schema editor without calling the API', async () => {
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('agentLoop.fallback.empty');

    fireEvent.click(screen.getByLabelText('agentLoop.schema.jsonSchema') || screen.getByDisplayValue('schema'));
    fireEvent.change(screen.getByLabelText('agentLoop.schema.title'), { target: { value: '{not json' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));

    expect(await screen.findByText(/agentLoop.schemaInvalidJson/)).toBeInTheDocument();
    expect(api.updateAgentLoopSettings).not.toHaveBeenCalled();
  });

  it('saves a valid JSON schema', async () => {
    api.updateAgentLoopSettings.mockResolvedValue({
      data: settingsBody({ output_schema: { type: 'object' } }),
    });
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('agentLoop.fallback.empty');

    fireEvent.click(screen.getByRole('radio', { name: 'agentLoop.schema.jsonSchema' }));
    fireEvent.change(screen.getByLabelText('agentLoop.schema.title'), { target: { value: '{"type": "object"}' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));

    await waitFor(() => expect(api.updateAgentLoopSettings).toHaveBeenCalledWith('a1', expect.objectContaining({
      output_schema: { type: 'object' },
    })));
  });

  it('sets tool_search and compaction through the tri-state selects', async () => {
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('agentLoop.fallback.empty');

    fireEvent.change(screen.getByLabelText('agentLoop.toolSearch.title'), { target: { value: 'on' } });
    fireEvent.change(screen.getByLabelText('agentLoop.compaction.title'), { target: { value: 'off' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));

    await waitFor(() => expect(api.updateAgentLoopSettings).toHaveBeenCalledWith('a1', expect.objectContaining({
      tool_search: true, compaction: false,
    })));
  });

  it('shows the refusal the backend sends back', async () => {
    api.updateAgentLoopSettings.mockRejectedValueOnce({ response: { data: { detail: "'x' is not enabled" } } });
    render(<LoopSettingsCard agentId="a1" agent={{ id: 'a1' }} />);
    await screen.findByText('agentLoop.fallback.empty');

    fireEvent.change(screen.getByLabelText('agentLoop.toolSearch.title'), { target: { value: 'on' } });
    fireEvent.click(screen.getByRole('button', { name: 'agentLoop.save' }));
    expect(await screen.findByText("'x' is not enabled")).toBeInTheDocument();
  });
});

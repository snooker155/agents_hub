import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getWorkspaceLoopSettings: vi.fn(),
  updateWorkspaceLoopSettings: vi.fn(),
}));
vi.mock('../../api/loopSettings', () => api);
vi.mock('../../i18n', () => ({
  useI18n: () => ({ t: (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k) }),
}));

import LoopSettingsWorkspace from '../settings/LoopSettingsWorkspace';

const DEFAULTS = {
  compaction: true,
  compaction_fraction: 0.7,
  compaction_keep: 3,
  tool_search_threshold: 30,
  native: true,
  strict_tools: false,
  view_focus: true,
};

function payload(settings, overrides = {}) {
  const effective = { ...DEFAULTS, ...overrides, ...settings };
  return { settings, effective, defaults: DEFAULTS, env: {
    compaction: 'AGENTS_HUB_LOOP_COMPACTION',
    compaction_fraction: 'AGENTS_HUB_LOOP_COMPACTION_FRACTION',
    compaction_keep: 'AGENTS_HUB_LOOP_COMPACTION_KEEP',
    tool_search_threshold: 'AGENTS_HUB_TOOL_SEARCH_THRESHOLD',
    native: 'AGENTS_HUB_LOOP_NATIVE',
    strict_tools: 'AGENTS_HUB_LOOP_STRICT_TOOLS',
    view_focus: 'AGENTS_HUB_LOOP_VIEW_FOCUS',
  } };
}

describe('LoopSettingsWorkspace', () => {
  beforeEach(() => {
    api.getWorkspaceLoopSettings.mockReset();
    api.updateWorkspaceLoopSettings.mockReset();
  });

  it('loads and shows the effective values with nothing overridden', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({ data: payload({}) });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(api.getWorkspaceLoopSettings).toHaveBeenCalledWith('w1'));
    expect(await screen.findByLabelText('loopSettings.fields.compaction.label')).toBeChecked();
    expect(screen.getByLabelText('loopSettings.fields.compactionFraction.label')).toHaveValue(0.7);
    expect(screen.getByLabelText('loopSettings.fields.compactionKeep.label')).toHaveValue(3);
    expect(screen.getByLabelText('loopSettings.fields.toolSearchThreshold.label')).toHaveValue(30);
  });

  it('shows the workspace source for an overridden key and disables its reset otherwise', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({ data: payload({ compaction: false }) });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.compaction.label')).not.toBeChecked());

    const resetButtons = screen.getAllByText('loopSettings.reset');
    // compaction is overridden: its reset is enabled; native (not overridden) stays disabled.
    const compactionReset = screen.getByLabelText(
      'loopSettings.resetFor {"field":"loopSettings.fields.compaction.label"}',
    );
    const nativeReset = screen.getByLabelText(
      'loopSettings.resetFor {"field":"loopSettings.fields.native.label"}',
    );
    expect(compactionReset).toBeEnabled();
    expect(nativeReset).toBeDisabled();
    expect(resetButtons.length).toBe(Object.keys(DEFAULTS).length);
  });

  it('shows the environment source when the effective value differs from the default without a workspace override', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({
      data: payload({}, { tool_search_threshold: 12 }),
    });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.toolSearchThreshold.label')).toHaveValue(12));
    expect(screen.getByText('loopSettings.source.environment')).toBeInTheDocument();
  });

  it('saves only the fields the user touched', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({ data: payload({}) });
    api.updateWorkspaceLoopSettings.mockResolvedValue({ data: payload({ compaction: false }) });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.compaction.label')).toBeChecked());

    fireEvent.click(screen.getByLabelText('loopSettings.fields.compaction.label'));
    fireEvent.click(screen.getByRole('button', { name: /loopSettings.save/ }));

    await waitFor(() => expect(api.updateWorkspaceLoopSettings).toHaveBeenCalledWith('w1', { compaction: false }));
    expect(await screen.findByText('loopSettings.saved')).toBeInTheDocument();
  });

  it('resets a key by sending null and re-merges the response', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({ data: payload({ compaction: false }) });
    api.updateWorkspaceLoopSettings.mockResolvedValue({ data: payload({}) });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.compaction.label')).not.toBeChecked());

    fireEvent.click(screen.getByLabelText(
      'loopSettings.resetFor {"field":"loopSettings.fields.compaction.label"}',
    ));

    await waitFor(() => expect(api.updateWorkspaceLoopSettings).toHaveBeenCalledWith('w1', { compaction: null }));
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.compaction.label')).toBeChecked());
  });

  it('shows the load error the backend returns', async () => {
    api.getWorkspaceLoopSettings.mockRejectedValueOnce({ response: { data: { detail: 'boom' } } });
    render(<LoopSettingsWorkspace workspace="w1" />);
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });

  it('disables save until a field is dirty', async () => {
    api.getWorkspaceLoopSettings.mockResolvedValue({ data: payload({}) });
    render(<LoopSettingsWorkspace workspace="w1" />);
    await waitFor(() => expect(screen.getByLabelText('loopSettings.fields.compaction.label')).toBeChecked());
    expect(screen.getByRole('button', { name: /loopSettings.save/ })).toBeDisabled();
  });
});

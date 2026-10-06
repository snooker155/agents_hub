import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Workspace roles (agents/roles.py): each role is held by the product's own
// agent until the workspace gives it to another of its agents. Special
// models (providers/special.py): a model per purpose, plus models of the
// workspace's own; a purpose left empty is shown as not added, nothing comes
// from another workspace.

const ok = (data) => Promise.resolve({ data });

const getWorkspaceRoles = vi.fn();
const updateWorkspaceRole = vi.fn();
const getWorkspaceSpecialModels = vi.fn();
const updateWorkspaceSpecialModels = vi.fn();
const discoverWorkspaceSpecialModels = vi.fn();
const checkWorkspaceSpecialModel = vi.fn();
const getAgents = vi.fn(() => ok([
  { id: 'swe_agent', name: 'SWE Agent' }, { id: 'claude-code', name: 'Claude Code' },
]));

vi.mock('../../api', () => ({
  getWorkspaceRoles: (...a) => getWorkspaceRoles(...a),
  updateWorkspaceRole: (...a) => updateWorkspaceRole(...a),
  getWorkspaceSpecialModels: (...a) => getWorkspaceSpecialModels(...a),
  updateWorkspaceSpecialModels: (...a) => updateWorkspaceSpecialModels(...a),
  discoverWorkspaceSpecialModels: (...a) => discoverWorkspaceSpecialModels(...a),
  checkWorkspaceSpecialModel: (...a) => checkWorkspaceSpecialModel(...a),
  getAgents: (...a) => getAgents(...a),
}));

import WorkspaceRoles from '../workspace/WorkspaceRoles';
import WorkspaceSpecialModels from '../workspace/WorkspaceSpecialModels';

const wrap = (node) => render(<MemoryRouter><I18nProvider>{node}</I18nProvider></MemoryRouter>);

const coder = (overrides) => ({
  role: 'coder', ref: '@coder', summary: '', default: 'swe_agent', default_name: 'SWE Agent',
  agent: 'swe_agent', agent_name: 'SWE Agent', bound: null, stale: false,
  callers: ['main-agent', 'orchestrator'], ...overrides,
});

describe('WorkspaceRoles', () => {
  beforeEach(() => vi.clearAllMocks());

  it('gives a role to another agent of the workspace and back to the default', async () => {
    getWorkspaceRoles.mockReturnValue(ok({ roles: [coder()] }));
    updateWorkspaceRole.mockReturnValue(ok({ roles: [coder({ agent: 'claude-code', bound: 'claude-code' })] }));
    wrap(<WorkspaceRoles workspace="alpha" />);

    const select = await screen.findByLabelText('Coder');
    expect(screen.getByText('Called by: main-agent, orchestrator')).toBeTruthy();
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Claude Code' })).toBeTruthy());
    expect(within(select).getByRole('option', { name: 'SWE Agent (default)' })).toBeTruthy();

    fireEvent.change(select, { target: { value: 'claude-code' } });
    await waitFor(() => expect(updateWorkspaceRole).toHaveBeenCalledWith('alpha', 'coder', 'claude-code'));
    await waitFor(() => expect(select).toHaveValue('claude-code'));

    updateWorkspaceRole.mockReturnValue(ok({ roles: [coder()] }));
    fireEvent.change(select, { target: { value: 'swe_agent' } });
    await waitFor(() => expect(updateWorkspaceRole).toHaveBeenLastCalledWith('alpha', 'coder', ''));
  });

  it('says when the bound agent left the workspace', async () => {
    getWorkspaceRoles.mockReturnValue(ok({ roles: [coder({ bound: 'codex', stale: true })] }));
    wrap(<WorkspaceRoles workspace="alpha" />);
    expect(await screen.findByText('codex is no longer in this workspace, so swe_agent holds the role.')).toBeTruthy();
  });
});

const options = {
  purposes: [
    { id: 'image', tool: 'generate_image', unit: 'image', summary: '', kinds: ['openai_compat', 'google'],
      suggestions: { openai_compat: ['gpt-image-1'], google: ['imagen-4.0-generate-001'] },
      options: { size: 'for example 1024x1024' } },
    { id: 'speech', tool: 'synthesize_speech', unit: '1k_chars', summary: '', kinds: ['openai_compat'],
      suggestions: { openai_compat: ['gpt-4o-mini-tts'] }, options: {} },
  ],
  custom: { tool: 'ask_special_model', kinds: ['chat', 'http'] },
  providers: [
    { id: 'openai', label: 'OpenAI', kind: 'openai_compat' },
    { id: 'google', label: 'Google', kind: 'google' },
  ],
};

describe('WorkspaceSpecialModels', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows an empty purpose as not added and saves a purpose and a model of its own', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    updateWorkspaceSpecialModels.mockImplementation((ws, body) => ok({
      workspace: ws, own: body, effective: body, options,
    }));
    wrap(<WorkspaceSpecialModels workspace="alpha" />);

    expect((await screen.findByTestId('special-image-missing')).textContent)
      .toMatch(/Not added. A call to generate_image answers that the model is not added/);
    expect(screen.queryByText(/default workspace/)).toBeNull();

    const [, speechProvider] = screen.getAllByLabelText('Provider');
    fireEvent.change(speechProvider, { target: { value: 'openai' } });
    const [, speechModel] = screen.getAllByLabelText('Model');
    fireEvent.change(speechModel, { target: { value: 'gpt-4o-mini-tts' } });

    fireEvent.click(screen.getByRole('button', { name: /Add a model/ }));
    const custom = screen.getByTestId('special-model-custom');
    fireEvent.change(within(custom).getByLabelText('Id'), { target: { value: 'jev' } });
    fireEvent.change(within(custom).getByLabelText('What it is for'), { target: { value: 'Predicts video frames' } });
    fireEvent.change(within(custom).getByLabelText('Provider'), { target: { value: 'my-vllm' } });
    fireEvent.change(within(custom).getByLabelText('Model'), { target: { value: 'jev-7b' } });

    fireEvent.click(screen.getByRole('button', { name: /Save models/ }));
    await waitFor(() => expect(updateWorkspaceSpecialModels).toHaveBeenCalled());
    const [ws, body] = updateWorkspaceSpecialModels.mock.calls[0];
    expect(ws).toBe('alpha');
    expect(body.image).toBeUndefined();
    expect(body.speech).toMatchObject({ provider: 'openai', model: 'gpt-4o-mini-tts' });
    expect(body.custom).toEqual([expect.objectContaining({
      id: 'jev', kind: 'chat', provider: 'my-vllm', model: 'jev-7b', description: 'Predicts video frames',
    })]);
  });

  it('finds the models of the provider that fit the purpose and picks one', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({ workspace: 'alpha', own: {}, effective: {}, options }));
    discoverWorkspaceSpecialModels.mockReturnValue(ok({
      purpose: 'speech', provider: 'openai', models: ['gpt-4o-mini-tts', 'tts-1'], total: 90,
    }));
    wrap(<WorkspaceSpecialModels workspace="alpha" />);
    await screen.findByTestId('special-image-missing');
    // No provider yet: nothing to ask.
    expect(screen.queryByTestId('special-speech-discover')).toBeNull();

    const [, speechProvider] = screen.getAllByLabelText('Provider');
    fireEvent.change(speechProvider, { target: { value: 'openai' } });
    fireEvent.click(screen.getByTestId('special-speech-discover'));
    await waitFor(() => expect(discoverWorkspaceSpecialModels).toHaveBeenCalledWith('alpha', 'speech', 'openai'));

    const found = await screen.findByTestId('special-speech-found');
    expect(found.textContent).toMatch(/2 of the provider's 90 models fit/);
    fireEvent.click(within(found).getByRole('button', { name: 'tts-1' }));
    const [, speechModel] = screen.getAllByLabelText('Model');
    expect(speechModel).toHaveValue('tts-1');

    // Another provider: the list was for the first one.
    fireEvent.change(speechProvider, { target: { value: '' } });
    expect(screen.queryByTestId('special-speech-found')).toBeNull();
  });

  it('checks the connection of the model the form holds, saved or not', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({
      workspace: 'alpha', effective: {}, options,
      own: { custom: [{ id: 'seg', description: 'Segments', kind: 'http', url: 'https://x/seg',
                        headers: { Authorization: '********' } }] },
    }));
    checkWorkspaceSpecialModel.mockReturnValueOnce(ok({ status: 'warn', message: 'not among them' }))
      .mockReturnValueOnce(ok({ status: 'ok', message: 'The URL answered 405' }));
    wrap(<WorkspaceSpecialModels workspace="alpha" />);
    await screen.findByTestId('special-image-missing');
    // No model yet: nothing to check.
    expect(screen.queryByTestId('special-speech-check')).toBeNull();

    const [, speechProvider] = screen.getAllByLabelText('Provider');
    fireEvent.change(speechProvider, { target: { value: 'openai' } });
    expect(screen.queryByTestId('special-speech-check')).toBeNull();
    const [, speechModel] = screen.getAllByLabelText('Model');
    fireEvent.change(speechModel, { target: { value: 'tts-9' } });
    fireEvent.click(screen.getByTestId('special-speech-check'));
    await waitFor(() => expect(checkWorkspaceSpecialModel).toHaveBeenCalledWith(
      'alpha', { purpose: 'speech', provider: 'openai', model: 'tts-9' }));
    expect((await screen.findByTestId('special-speech-check-result')).textContent).toMatch(/not among them/);

    // Another model: the result was for the first one.
    fireEvent.change(speechModel, { target: { value: 'tts-1' } });
    expect(screen.queryByTestId('special-speech-check-result')).toBeNull();

    // A custom HTTP model sends its headers, the mask included, for the server to fill in.
    fireEvent.click(screen.getByTestId('special-custom-0-check'));
    await waitFor(() => expect(checkWorkspaceSpecialModel).toHaveBeenLastCalledWith('alpha', {
      custom: { id: 'seg', kind: 'http', url: 'https://x/seg', headers: { Authorization: '********' } },
    }));
    expect((await screen.findByTestId('special-custom-0-check-result')).textContent).toMatch(/405/);
  });

  it('sends http headers as an object', async () => {
    getWorkspaceSpecialModels.mockReturnValue(ok({
      workspace: 'alpha', effective: {}, options,
      own: { custom: [{ id: 'seg', description: 'Segments', kind: 'http', url: 'https://x/seg',
                        headers: { Authorization: '********' } }] },
    }));
    updateWorkspaceSpecialModels.mockImplementation((ws, body) => ok({ workspace: ws, own: body, effective: body, options }));
    wrap(<WorkspaceSpecialModels workspace="alpha" />);
    const custom = await screen.findByTestId('special-model-custom');
    const headers = within(custom).getByLabelText(/Headers, one per line/);
    expect(headers).toHaveValue('Authorization: ********');
    fireEvent.change(headers, { target: { value: 'Authorization: ********\nX-Org: ${ORG}' } });
    fireEvent.click(screen.getByRole('button', { name: /Save models/ }));
    await waitFor(() => expect(updateWorkspaceSpecialModels).toHaveBeenCalled());
    expect(updateWorkspaceSpecialModels.mock.calls[0][1].custom[0].headers)
      .toEqual({ Authorization: '********', 'X-Org': '${ORG}' });
  });
});

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';
import { AuthContext, DEFAULT_AUTH } from '../../auth';

const getModelsCatalog = vi.fn();
const getWorkspaceModelPolicy = vi.fn();
const updateWorkspaceModelPolicy = vi.fn();

vi.mock('../../../api', () => ({
  getModelsCatalog: (...a) => getModelsCatalog(...a),
  getWorkspaceModelPolicy: (...a) => getWorkspaceModelPolicy(...a),
  updateWorkspaceModelPolicy: (...a) => updateWorkspaceModelPolicy(...a),
}));

import ModelPolicyCard from '../ModelPolicyCard';

const ok = (data) => Promise.resolve({ data });
const catalog = {
  providers: {
    openai: { models: [{ id: 'gpt-5', enabled: true }, { id: 'gpt-5-mini', enabled: true }, { id: 'old', enabled: false }] },
    anthropic: { models: [{ id: 'claude-sonnet', enabled: true }] },
  },
};
const policy = {
  models: [{ provider: 'openai', model: 'gpt-5' }, { provider: 'anthropic', model: 'claude-sonnet' }],
  default: { provider: 'openai', model: 'gpt-5' },
  temperature: 0.3, effort: 'high', max_tokens: null,
};

const admin = { ...DEFAULT_AUTH, mode: 'single' };
const member = { ...DEFAULT_AUTH, mode: 'multi', user: { role: 'member' } };
const wrap = (auth) => render(
  <AuthContext.Provider value={auth}><I18nProvider><ModelPolicyCard workspace="ws" /></I18nProvider></AuthContext.Provider>,
);

describe('ModelPolicyCard', () => {
  it('lets an administrator tick models and saves the body', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy: null }));
    getModelsCatalog.mockReturnValue(ok(catalog));
    updateWorkspaceModelPolicy.mockImplementation((_n, body) => ok({ policy: { ...body, default: body.default } }));
    wrap(admin);
    const box = await screen.findByLabelText('gpt-5-mini');
    expect(screen.queryByText('old')).toBeNull();
    fireEvent.click(box);
    fireEvent.click(screen.getByRole('button', { name: /Save policy/ }));
    await waitFor(() => expect(updateWorkspaceModelPolicy).toHaveBeenCalled());
    const [name, body] = updateWorkspaceModelPolicy.mock.calls[0];
    expect(name).toBe('ws');
    expect(body.models).toEqual([{ provider: 'openai', model: 'gpt-5-mini' }]);
    expect(body.default).toEqual({ provider: 'openai', model: 'gpt-5-mini' });
  });

  it('shows the server reason when saving fails', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy }));
    getModelsCatalog.mockReturnValue(ok(catalog));
    updateWorkspaceModelPolicy.mockRejectedValue({ response: { status: 400, data: { detail: 'model not enabled' } } });
    wrap(admin);
    fireEvent.click(await screen.findByLabelText('gpt-5-mini'));
    fireEvent.click(screen.getByRole('button', { name: /Save policy/ }));
    expect((await screen.findByTestId('model-policy-error')).textContent).toContain('model not enabled');
  });

  it('removes the policy with an empty model list', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy }));
    getModelsCatalog.mockReturnValue(ok(catalog));
    updateWorkspaceModelPolicy.mockReturnValue(ok({ policy: null }));
    wrap(admin);
    fireEvent.click(await screen.findByRole('button', { name: /Remove policy/ }));
    await waitFor(() => expect(updateWorkspaceModelPolicy).toHaveBeenCalledWith('ws', { models: [] }));
  });

  it('gives a member a read only summary', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy }));
    wrap(member);
    const summary = await screen.findByTestId('model-policy-summary');
    expect(summary.textContent).toContain('claude-sonnet');
    expect(summary.textContent).toContain('0.3');
    expect(screen.queryByRole('button', { name: /Save policy/ })).toBeNull();
    expect(getModelsCatalog).not.toHaveBeenCalled();
  });

  it('tells a member there is no policy', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy: null }));
    wrap(member);
    expect((await screen.findByTestId('model-policy-none')).textContent).toMatch(/No model policy/);
  });
  it('says when a personal workspace follows default and offers no removal then', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy: { ...policy, inherited_from: 'default', lock_special_models: true } }));
    getModelsCatalog.mockReturnValue(ok(catalog));
    wrap(admin);
    expect(await screen.findByTestId('model-policy-inherited')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Remove policy/ })).toBeNull();
    expect(screen.getByLabelText(/Special models are set by an administrator/)).toBeChecked();
  });

  it('tells a member the policy is inherited and special models are locked', async () => {
    getWorkspaceModelPolicy.mockReturnValue(ok({ policy: { ...policy, inherited_from: 'default', lock_special_models: true } }));
    wrap(member);
    expect(await screen.findByTestId('model-policy-inherited')).toBeTruthy();
    expect(screen.getByText(/Special models \(images, video, speech, transcription\) are set by an administrator/)).toBeTruthy();
  });
});

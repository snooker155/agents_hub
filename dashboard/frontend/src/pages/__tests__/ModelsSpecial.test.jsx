import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { I18nProvider } from '../../i18n';

// The Models page shows the special models (providers/special.py) of the
// workspace picked in the header, with the same form the workspace settings
// use and a link there.

const ok = (data) => Promise.resolve({ data });

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'studio' }),
}));

const getWorkspaceSpecialModels = vi.fn(() => ok({
  workspace: 'studio', own: {}, effective: {},
  options: {
    purposes: [{ id: 'image', tool: 'generate_image', unit: 'image', summary: '', kinds: ['openai_compat'],
                 suggestions: { openai_compat: ['gpt-image-1'] }, options: {} }],
    custom: { tool: 'ask_special_model', kinds: ['chat', 'http'] },
    providers: [{ id: 'openai', label: 'OpenAI', kind: 'openai_compat' }],
  },
}));

vi.mock('../../api', () => ({
  getModelsCatalog: () => ok({ providers: {} }),
  saveModelsCatalog: vi.fn(), discoverProviderModels: vi.fn(),
  getModelsUsage: () => ok({ rows: [] }),
  getWorkspaceModel: () => ok({}), updateWorkspaceDefaultModel: vi.fn(),
  getWorkspaceSpecialModels: (...a) => getWorkspaceSpecialModels(...a),
  updateWorkspaceSpecialModels: vi.fn(), discoverWorkspaceSpecialModels: vi.fn(),
}));
vi.mock('../../components/models/LocalTab', () => ({ default: () => null }));
// The catalog asks whether the local model servers run; nothing does here.
vi.mock('../../api/localModels', () => ({ getLocalServers: () => ok({ servers: {} }) }));

import Models from '../Models';

describe('Models page, special models tab', () => {
  it('shows the form for the header workspace with a link to its settings', async () => {
    render(<MemoryRouter><I18nProvider><Models /></I18nProvider></MemoryRouter>);
    screen.getByRole('button', { name: /Special models/ }).click();
    expect(await screen.findByText('generate_image')).toBeTruthy();
    expect(getWorkspaceSpecialModels).toHaveBeenCalledWith('studio');
    expect(screen.getByRole('link', { name: /Also in the workspace settings/ }))
      .toHaveAttribute('href', '/workspaces/studio?tab=settings&section=specialModels');
  });
});

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import N8nImportModal from '../N8nImportModal';

// The n8n import dialog: picks a workflow from the connected instance (or a
// pasted export), sends the chosen mode, then shows the backend's report
// before the flow is opened.

const api = vi.hoisted(() => ({
  listN8nWorkflows: vi.fn(),
  importN8nFlow: vi.fn(),
}));
vi.mock('../../../api', () => api);

const show = (props = {}) => render(
  <MemoryRouter>
    <I18nProvider>
      <N8nImportModal workspace="default" onClose={vi.fn()} onOpen={vi.fn()} {...props} />
    </I18nProvider>
  </MemoryRouter>,
);

const REPORT = {
  converted: [{ name: 'AI Agent', as: 'agent', ai: true }, { name: 'Urgent?', as: 'condition' }],
  dropped: [{ name: 'When chat message received', reason: 'trigger; the hub flow starts on its own' }],
  warnings: ["IF 'Urgent?': its n8n condition does not carry over."],
};

describe('N8nImportModal', () => {
  beforeEach(() => {
    api.listN8nWorkflows.mockReset();
    api.importN8nFlow.mockReset();
  });

  it('imports a workflow from the instance and shows the report', async () => {
    api.listN8nWorkflows.mockResolvedValue({
      data: [{ id: 'wf1', name: 'Support triage', active: true, nodes: 6, trigger: { kind: 'chat' } }],
    });
    api.importN8nFlow.mockResolvedValue({ data: { flow: { id: 'f1', name: 'Support triage' }, report: REPORT } });
    const onOpen = vi.fn();
    show({ onOpen });

    fireEvent.click(await screen.findByText('Support triage'));
    fireEvent.click(screen.getByTestId('n8n-import-submit'));

    await screen.findByTestId('n8n-import-report');
    expect(api.importN8nFlow).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'convert', workflow_id: 'wf1', workspace: 'default' }),
    );
    expect(screen.getByText('agent, prompt carried over')).toBeInTheDocument();
    expect(screen.getByText(/its n8n condition does not carry over/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('n8n-open-flow'));
    expect(onOpen).toHaveBeenCalledWith({ id: 'f1', name: 'Support triage' }, 'convert');
  });

  it('falls back to pasting JSON when no connector is set, and sends the run mode', async () => {
    api.listN8nWorkflows.mockRejectedValue({ response: { data: { detail: 'No n8n connection configured.' } } });
    api.importN8nFlow.mockResolvedValue({
      data: { flow: { id: 'f2', name: 'Hook' }, report: { converted: [], dropped: [], warnings: [] } },
    });
    show();

    const box = await screen.findByTestId('n8n-json');
    fireEvent.click(screen.getByTestId('n8n-mode-run'));
    fireEvent.change(box, { target: { value: '{"nodes": [], "connections": {}}' } });
    fireEvent.click(screen.getByTestId('n8n-import-submit'));

    await waitFor(() => expect(api.importN8nFlow).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'run', workflow: '{"nodes": [], "connections": {}}' }),
    ));
  });

  it('shows the backend error and keeps the form', async () => {
    api.listN8nWorkflows.mockResolvedValue({ data: [] });
    api.importN8nFlow.mockRejectedValue({ response: { data: { detail: 'This is not an n8n workflow' } } });
    show();
    fireEvent.click(await screen.findByText('Paste JSON'));
    fireEvent.change(screen.getByTestId('n8n-json'), { target: { value: '{}' } });
    fireEvent.click(screen.getByTestId('n8n-import-submit'));
    expect(await screen.findByText('This is not an n8n workflow')).toBeInTheDocument();
  });
});

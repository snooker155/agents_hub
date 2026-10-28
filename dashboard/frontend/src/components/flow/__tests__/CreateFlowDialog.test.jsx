import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';

const createFlow = vi.fn(async () => ({ data: { id: 'f1' } }));
const createFlowFromTemplate = vi.fn(async () => ({ data: { id: 'f2' } }));
vi.mock('../../../api/flows', () => ({
  createFlow: (...args) => createFlow(...args),
  createFlowFromTemplate: (...args) => createFlowFromTemplate(...args),
  getFlowTemplates: async () => ({ data: [] }),
}));

const { default: CreateFlowDialog } = await import('../CreateFlowDialog');

// A new flow belongs to the workspace it is created in. The first step picks
// a template or the blank canvas; the second names the flow.
describe('CreateFlowDialog', () => {
  it('creates the flow in the given workspace', async () => {
    render(
      <MemoryRouter><I18nProvider><CreateFlowDialog workspace="zerno" onClose={() => {}} /></I18nProvider></MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId('create-flow-next'));
    fireEvent.change(screen.getByTestId('create-flow-name'), { target: { value: 'Coffee' } });
    fireEvent.click(screen.getByTestId('create-flow-submit'));
    await waitFor(() => expect(createFlow).toHaveBeenCalledWith({ name: 'Coffee', description: '', workspace: 'zerno' }));
    expect(createFlowFromTemplate).not.toHaveBeenCalled();
  });
});

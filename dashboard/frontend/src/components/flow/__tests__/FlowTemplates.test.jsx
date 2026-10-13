import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

// The Create flow dialog's template grid (FlowTemplatePicker) and the example
// requests an empty flow chat offers (ExampleRequests): a click picks, a
// click on an example fills the box rather than sending.

const ok = (data) => Promise.resolve({ data });

const TEMPLATE = {
  id: 'research_report',
  name: 'Research report',
  description: 'Researches a question.',
  category: 'Research',
  icon: 'search',
  agents: ['researcher', 'writer'],
  steps: [
    { key: 'research', agent: 'researcher', label: 'Research' },
    { key: 'write', agent: 'writer', label: 'Write report' },
  ],
  edges: [{ from: 'research', to: 'write' }],
  examples: ['One', 'Two'],
};

const getFlowTemplates = vi.fn(() => ok([TEMPLATE]));
const createFlow = vi.fn(() => ok({ id: 'blank-1' }));
const createFlowFromTemplate = vi.fn(() => ok({ id: 'tpl-1' }));
const navigate = vi.fn();
vi.mock('../../../api/flows', () => ({
  getFlowTemplates: (...a) => getFlowTemplates(...a),
  createFlow: (...a) => createFlow(...a),
  createFlowFromTemplate: (...a) => createFlowFromTemplate(...a),
}));
vi.mock('react-router-dom', async (orig) => ({ ...(await orig()), useNavigate: () => navigate }));

const { default: FlowTemplatePicker } = await import('../FlowTemplatePicker');
const { default: ExampleRequests } = await import('../../chat/ExampleRequests');
const { default: CreateFlowDialog } = await import('../CreateFlowDialog');

const wrap = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

beforeEach(() => { vi.clearAllMocks(); });

describe('FlowTemplatePicker', () => {
  it('offers a blank canvas and each template with its steps', async () => {
    const onPick = vi.fn();
    wrap(<FlowTemplatePicker selectedId={null} onPick={onPick} />);
    const card = await screen.findByTestId('flow-template-research_report');
    expect(card.textContent).toContain('Research report');
    expect(card.textContent).toContain('Write report');
    fireEvent.click(card);
    expect(onPick).toHaveBeenCalledWith(TEMPLATE);
    fireEvent.click(screen.getByTestId('flow-template-blank'));
    expect(onPick).toHaveBeenLastCalledWith(null);
  });
});

describe('ExampleRequests', () => {
  it('shows the loaded examples and hands a clicked one over', async () => {
    const onPick = vi.fn();
    wrap(<ExampleRequests load={() => ok({ examples: ['Find X', 'Compare Y'] })} loadKey="f1:en" onPick={onPick} />);
    fireEvent.click(await screen.findByText('Compare Y'));
    expect(onPick).toHaveBeenCalledWith('Compare Y');
  });

  it('uses the fallback only when nothing came back, and nothing without one', async () => {
    const { container, unmount } = wrap(
      <ExampleRequests load={() => ok({ examples: [] })} loadKey="f2:en" fallback={['General']} onPick={() => {}} />,
    );
    expect(await screen.findByText('General')).toBeTruthy();
    unmount();
    const empty = wrap(<ExampleRequests load={() => ok({ examples: [] })} loadKey="f3:en" onPick={() => {}} />);
    await waitFor(() => expect(empty.container.textContent).toBe(''));
    expect(container.textContent).toBe('');
  });
});

describe('CreateFlowDialog', () => {
  it('picks a template first, then takes the name and opens the run page', async () => {
    wrap(<CreateFlowDialog onClose={() => {}} />);
    fireEvent.click(await screen.findByTestId('flow-template-research_report'));
    fireEvent.click(screen.getByTestId('create-flow-next'));
    const name = screen.getByTestId('create-flow-name');
    expect(name.value).toBe('Research report');
    fireEvent.change(name, { target: { value: 'Market study' } });
    fireEvent.click(screen.getByTestId('create-flow-submit'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/flows/tpl-1'));
    expect(createFlowFromTemplate).toHaveBeenCalledWith('research_report', expect.objectContaining({ name: 'Market study' }));
  });

  it('keeps a typed name when going back to another choice, and a blank flow opens the editor', async () => {
    wrap(<CreateFlowDialog onClose={() => {}} />);
    fireEvent.doubleClick(await screen.findByTestId('flow-template-blank'));
    fireEvent.change(screen.getByTestId('create-flow-name'), { target: { value: 'Mine' } });
    fireEvent.click(screen.getByText('Back'));
    fireEvent.doubleClick(await screen.findByTestId('flow-template-research_report'));
    expect(screen.getByTestId('create-flow-name').value).toBe('Mine');
    fireEvent.click(screen.getByText('Back'));
    fireEvent.doubleClick(screen.getByTestId('flow-template-blank'));
    fireEvent.click(screen.getByTestId('create-flow-submit'));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/flows/blank-1/edit'));
    expect(createFlow).toHaveBeenCalledWith({ name: 'Mine', description: '' });
  });
});

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import NodeRunPanel from '../NodeRunPanel';

const agentNode = {
  id: 'n1',
  data: {
    label: 'Draft', category: 'agent', agent_id: 'writer', nodeTask: 'Keep it short.',
    input: ['brief'], output: ['draft'], retry: { max: 2, backoff_seconds: 5 }, config: {},
  },
};
const agents = [{ id: 'writer', name: 'Writer', description: 'Writes things.', model: 'gpt-x', provider: 'openai', tools: ['read_file'] }];

const show = (props) => render(
  <MemoryRouter><I18nProvider>
    <NodeRunPanel node={agentNode} agents={agents} editPath="/flows/f/edit" {...props} />
  </I18nProvider></MemoryRouter>,
);

describe('NodeRunPanel', () => {
  it('shows the agent and what the node is set to in this flow', () => {
    show({});
    expect(screen.getByText('Writer')).toBeInTheDocument();
    expect(screen.getByText('Writes things.')).toBeInTheDocument();
    expect(screen.getByText('openai · gpt-x')).toBeInTheDocument();
    expect(screen.queryByText('read_file')).toBeNull();
    expect(screen.getByText('Keep it short.')).toBeInTheDocument();
    expect(screen.getByText('brief')).toBeInTheDocument();
    expect(screen.getByText('draft')).toBeInTheDocument();
    expect(screen.getByText('2, 5 s apart')).toBeInTheDocument();
  });

  it('without a picked run, offers to write to the node', () => {
    const onAddress = vi.fn();
    show({ onAddress });
    expect(screen.queryByTestId('node-rerun-single')).toBeNull();
    fireEvent.click(screen.getByTestId('node-start'));
    expect(onAddress).toHaveBeenCalled();
  });

  it('with a picked run, offers both ways to run again from the node', () => {
    const onRerun = vi.fn();
    show({ record: { run_group: 'g1', kind: 'task', title: 'Nightly', status: 'completed' }, onRerun });
    expect(screen.queryByTestId('node-start')).toBeNull();
    expect(screen.getByText(/picked run “Nightly”/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('node-rerun-single'));
    fireEvent.click(screen.getByTestId('node-rerun-rest'));
    expect(onRerun.mock.calls).toEqual([['single'], ['rest']]);
  });

  it('shows a non-agent node its config', () => {
    show({ node: { id: 'n2', data: { label: 'Gate', category: 'condition', entity_id: 'state_switch', config: { key: 'score' } } } });
    expect(screen.queryByTestId('node-panel-agent')).toBeNull();
    expect(screen.getByText('score')).toBeInTheDocument();
  });

  it('keeps the run buttons off while the picked run is still going', () => {
    show({ record: { run_group: 'g1', kind: 'task', status: 'running' }, onRerun: vi.fn() });
    expect(screen.getByTestId('node-rerun-rest')).toBeDisabled();
  });
});

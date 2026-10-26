import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import * as api from '../../../api';

const handlers = [];
const stream = { on: (_ch, fn) => { handlers.push(fn); return () => {}; }, acquireChannel: () => () => {}, clientId: 'c1' };
vi.mock('../../stream', () => ({ useStream: () => stream }));
vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunTrail: vi.fn(() => Promise.resolve({ data: null })),
  getContextKinds: vi.fn(() => Promise.resolve({ data: [] })),
  stopMessage: vi.fn(),
  startChatOverSSE: vi.fn(() => Promise.resolve({ data: {} })),
  summarizeFlowChatTurn: vi.fn(() => Promise.resolve({ data: { summary: 'Both steps are done.' } })),
}));

import FlowChat from '../FlowChat';

beforeAll(() => { Element.prototype.scrollIntoView = () => {}; });
beforeEach(() => {
  api.startChatOverSSE.mockClear();
  handlers.length = 0;
  localStorage.clear();
});

const emit = (event) => act(() => { handlers.forEach((fn) => fn(event)); });

describe('Flow chat: Summary view', () => {
  it('asks for a summary and shows one reply per request instead of a bubble per node', async () => {
    render(<MemoryRouter><I18nProvider><FlowChat flow={{ name: 'Pipeline' }} flowId="f1" workspace="ws" /></I18nProvider></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /Summary/ }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'write a post' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(api.startChatOverSSE).toHaveBeenCalled());
    expect(api.startChatOverSSE.mock.calls[0][0].flow_summary).toBe(true);

    emit({ type: 'node_start', node_id: 'n1', agent_label: 'Researcher' });
    emit({ type: 'tool_start', node_id: 'n1', tool: 'web_search', input: '{"query": "news"}' });
    // The node's name is under the avatar, not in the bubble.
    expect(screen.getByTestId('flow-step-progress')).toHaveTextContent('Searching the web: news');
    expect(screen.getByTestId('flow-step-progress')).not.toHaveTextContent('Researcher');
    expect(screen.getByTitle('Researcher')).toBeInTheDocument();
    emit({ type: 'tool_end', node_id: 'n1', output: 'ok' });
    emit({ type: 'node_done', node_id: 'n1', ok: true, response: 'Notes from the research' });
    emit({ type: 'node_start', node_id: 'n2', agent_label: 'Writer' });
    emit({ type: 'node_done', node_id: 'n2', ok: true, response: 'The post itself' });
    emit({ type: 'flow_summary_delta', content: 'Wrote the post ' });
    emit({ type: 'flow_summary', content: 'Wrote the post about the news.' });
    emit({ type: 'done', ok: true });
    emit({ type: 'chat_stream_end' });

    expect(screen.getByText('Wrote the post about the news.')).toBeInTheDocument();
    expect(screen.queryByText('Notes from the research')).toBeNull();
    expect(screen.queryByText('The post itself')).toBeNull();

    // The node bubbles are all there in the Chat view.
    fireEvent.click(screen.getByRole('button', { name: /^Chat$/ }));
    expect(screen.getByText('Notes from the research')).toBeInTheDocument();
    expect(screen.getByText('The post itself')).toBeInTheDocument();
    // The summary is the Summary view's alone.
    expect(screen.queryByText('Wrote the post about the news.')).toBeNull();
  });

  it('does not ask for a summary in the Chat view', async () => {
    render(<MemoryRouter><I18nProvider><FlowChat flow={{ name: 'Pipeline' }} flowId="f1" workspace="ws" /></I18nProvider></MemoryRouter>);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'go' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(api.startChatOverSSE).toHaveBeenCalled());
    expect(api.startChatOverSSE.mock.calls[0][0].flow_summary).toBeUndefined();
  });

  it('offers to sum up turns sent in the Chat view, and writes them on a yes', async () => {
    const resumed = [
      { id: 'u1', role: 'user', content: 'research and write', turn_id: 'T1' },
      { id: 'a1', role: 'agent', agent_label: 'Researcher', content: 'Notes', turn_id: 'T1' },
      { id: 'a2', role: 'agent', agent_label: 'Writer', content: 'The post', turn_id: 'T1' },
    ];
    render(<MemoryRouter><I18nProvider><FlowChat flow={{ name: 'Pipeline' }} flowId="f1" workspace="ws"
      resumeConversationId="conv-1" resumeMessages={resumed} resumeKey="k1" /></I18nProvider></MemoryRouter>);
    expect(screen.queryByTestId('flow-summary-offer')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Summary/ }));
    expect(screen.getByTestId('flow-summary-offer')).toHaveTextContent('One request');
    fireEvent.click(screen.getByRole('button', { name: 'Write the summary' }));
    await screen.findByText('Both steps are done.');
    expect(api.summarizeFlowChatTurn).toHaveBeenCalledWith('f1', 'conv-1', expect.objectContaining({
      turn_id: 'T1', user_message: 'research and write',
    }));
    expect(screen.queryByTestId('flow-summary-offer')).toBeNull();
    expect(screen.queryByText('The post')).toBeNull();
  });
});

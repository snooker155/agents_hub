import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import * as api from '../../../api';

const stream = { on: () => () => {}, acquireChannel: () => () => {}, clientId: 'c1' };
vi.mock('../../stream', () => ({ useStream: () => stream }));
vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunTrail: vi.fn(() => Promise.resolve({ data: null })),
  getContextKinds: vi.fn(() => Promise.resolve({ data: [] })),
  stopMessage: vi.fn(),
  startChatOverSSE: vi.fn(() => Promise.resolve({ data: {} })),
}));

import FlowChat from '../FlowChat';

beforeAll(() => { Element.prototype.scrollIntoView = () => {}; });
beforeEach(() => { api.startChatOverSSE.mockClear(); });

const show = (props) => render(
  <MemoryRouter><I18nProvider><FlowChat flow={{ name: 'Pipeline' }} flowId="f1" workspace="ws" {...props} /></I18nProvider></MemoryRouter>,
);

describe('Flow chat: starting at a node, running again from one', () => {
  it('names the flow in the box in the page language', () => {
    show();
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Message Pipeline…');
  });

  it('sends a message to the picked node, then forgets it', async () => {
    const onClear = vi.fn();
    show({ startNode: { id: 'n2', label: 'Writer' }, onClearStartNode: onClear });
    expect(screen.getByTestId('flow-chat-start-node')).toHaveTextContent('Writer');
    expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Message for Writer…');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'draft it' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(api.startChatOverSSE).toHaveBeenCalled());
    const body = api.startChatOverSSE.mock.calls[0][0];
    expect(body.flow_start_node).toBe('n2');
    expect(body.message).toBe('draft it');
    expect(onClear).toHaveBeenCalled();
  });

  it('runs the last turn again from a node, once per request, without the bubble in the history', async () => {
    const onHandled = vi.fn();
    const request = { node_id: 'n2', label: 'Writer', mode: 'single', key: 'k1' };
    const { rerender } = show({ rerunRequest: request, onRerunHandled: onHandled });
    await waitFor(() => expect(api.startChatOverSSE).toHaveBeenCalledTimes(1));
    const body = api.startChatOverSSE.mock.calls[0][0];
    expect(body.flow_rerun).toEqual({ node_id: 'n2', mode: 'single' });
    expect(body.flow_start_node).toBeUndefined();
    expect(body.history).toEqual([]);
    expect(onHandled).toHaveBeenCalledWith(true);
    expect(screen.getByText(/Run node “Writer” again on its own/)).toBeInTheDocument();
    rerender(
      <MemoryRouter><I18nProvider><FlowChat flow={{ name: 'Pipeline' }} flowId="f1" workspace="ws" rerunRequest={request} onRerunHandled={onHandled} /></I18nProvider></MemoryRouter>,
    );
    expect(api.startChatOverSSE).toHaveBeenCalledTimes(1);
  });
});

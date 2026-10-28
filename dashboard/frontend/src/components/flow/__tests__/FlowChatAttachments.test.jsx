import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it, vi } from 'vitest';
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

describe('Flow chat: files ride with the message, as on the Chat page', () => {
  it('sends a picked file with the turn and names it in the bubble', async () => {
    const { container } = render(
      <MemoryRouter><I18nProvider><FlowChat flow={{ name: 'F' }} flowId="f1" workspace="ws" /></I18nProvider></MemoryRouter>,
    );
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    // jsdom's File may lack text(); the box reads a picked file with it.
    if (!file.text) file.text = () => Promise.resolve('hello');
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [file] } });
    await screen.findByText('notes.txt');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'read this' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(api.startChatOverSSE).toHaveBeenCalled());
    const body = api.startChatOverSSE.mock.calls[0][0];
    expect(body.attachments).toEqual([{ filename: 'notes.txt', content: 'hello', store_to_workspace: false }]);
    expect(body.references).toEqual([]);
    expect(body.message).toBe('read this');
    // The box is empty again, the file gone from above it.
    expect(screen.queryByText('notes.txt')).toBeNull();
    expect(screen.getByText(/read this/)).toHaveTextContent('notes.txt');
  });
});

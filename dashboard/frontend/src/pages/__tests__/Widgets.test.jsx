import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Hoisted with the mocks below, which vitest lifts above every import.
const { ok, api, getAgents } = vi.hoisted(() => {
  const resolve = (data) => Promise.resolve({ data });
  return { ok: resolve, api: {}, getAgents: vi.fn(() => resolve([{ id: 'helper', name: 'Helper' }, { id: 'other', name: 'Other' }])) };
});

const WIDGET = {
  widget_id: 'wgt_abc123',
  workspace: 'default',
  name: 'Shop chat',
  agent_id: 'helper',
  agent_name: 'Helper',
  owner_id: 'local',
  owner_ok: true,
  public_key: 'ahw_publickey',
  allowed_origins: ['https://shop.example'],
  enabled: true,
  title: 'Ask us',
  greeting: 'Hi',
  placeholder: '',
  accent: 'teal',
  language: 'auto',
  limits: { messages_per_minute: 6, attachment_max_bytes: 2097152, max_attachments: 3, tokens_per_day: 200000 },
  thread_count: 2,
  tokens_today: 1200,
  created_at: '2026-09-25T10:00:00Z',
  updated_at: '2026-09-25T10:00:00Z',
};

Object.assign(api, {
  getWidgetOptions: vi.fn(() => ok({
    accents: ['navy', 'teal'], languages: ['auto', 'en', 'ru', 'de'], wildcard_allowed: true,
    limits: {
      messages_per_minute: { min: 1, max: 120, default: 6 },
      attachment_max_bytes: { min: 0, max: 5242880, default: 2097152 },
      max_attachments: { min: 0, max: 5, default: 3 },
      tokens_per_day: { min: 0, max: 100000000, default: 200000 },
    },
  })),
  getWidgets: vi.fn(() => ok([])),
  createWidget: vi.fn(() => ok({ ...WIDGET })),
  updateWidget: vi.fn(() => ok({ ...WIDGET })),
  deleteWidget: vi.fn(() => ok({ deleted: true })),
  rotateWidgetKey: vi.fn(() => ok({ ...WIDGET, public_key: 'ahw_newkey' })),
  getWidgetSnippet: vi.fn(() => ok({
    snippet: '<script src="http://hub/widget.js" data-widget="wgt_abc123" data-key="ahw_publickey" async></script>',
  })),
  createWidgetPreview: vi.fn(() => ok({
    ticket: 'tkt', widget_id: 'wgt_abc123', public_key: 'ahw_publickey', script_path: '/api/widgets/public/widget.js',
  })),
  getWidgetThreads: vi.fn(() => ok([])),
  getWidgetThread: vi.fn(() => ok({ thread: {}, messages: [] })),
  deleteWidgetThread: vi.fn(() => ok({ deleted: true })),
});

vi.mock('../../api/widgets', () => Object.fromEntries(
  ['getWidgetOptions', 'getWidgets', 'createWidget', 'updateWidget', 'deleteWidget', 'rotateWidgetKey',
    'getWidgetSnippet', 'createWidgetPreview', 'getWidgetThreads', 'getWidgetThread', 'deleteWidgetThread']
    .map((name) => [name, (...args) => api[name](...args)]),
));

vi.mock('../../api', () => ({
  API_ORIGIN: '',
  getAgents: (...args) => getAgents(...args),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ workspaceFilter: undefined, selectedWorkspace: 'default' }),
}));

import Widgets from '../Widgets';

const show = () => render(
  <I18nProvider><MemoryRouter><Widgets /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockClear());
  api.getWidgets.mockImplementation(() => ok([]));
  getAgents.mockClear();
});

describe('Widgets page', () => {
  it('says there is nothing yet', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no widgets in this workspace yet/i)).toBeInTheDocument());
    expect(api.getWidgets).toHaveBeenCalledWith('default');
  });

  it('lists a widget and shows its script tag and key', async () => {
    api.getWidgets.mockImplementation(() => ok([WIDGET]));
    show();
    await screen.findAllByText('Shop chat');
    expect(screen.getByText(/2 conversations/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('widget-snippet').textContent).toContain('data-key="ahw_publickey"'));
    expect(screen.getByText('ahw_publickey')).toBeInTheDocument();
    expect(screen.getByText('https://shop.example')).toBeInTheDocument();
  });

  it('rotates the key only after a confirmation', async () => {
    api.getWidgets.mockImplementation(() => ok([WIDGET]));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    show();
    const button = await screen.findByRole('button', { name: /rotate key/i });
    fireEvent.click(button);
    expect(api.rotateWidgetKey).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(api.rotateWidgetKey).toHaveBeenCalledWith('wgt_abc123'));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it('creates a widget from the form', async () => {
    show();
    await screen.findByText(/no widgets in this workspace yet/i);
    fireEvent.click(screen.getByRole('button', { name: /new widget/i }));
    await waitFor(() => expect(getAgents).toHaveBeenCalledWith('default'));
    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: 'Help desk' } });
    fireEvent.change(screen.getByLabelText(/allowed origins/i), {
      target: { value: 'https://shop.example\nhttp://localhost:3000' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(api.createWidget).toHaveBeenCalled());
    const payload = api.createWidget.mock.calls[0][0];
    expect(payload).toMatchObject({
      workspace: 'default', name: 'Help desk', agent_id: 'helper',
      allowed_origins: ['https://shop.example', 'http://localhost:3000'], accent: 'navy', language: 'auto',
    });
    expect(payload.limits).toEqual({
      messages_per_minute: 6, attachment_max_bytes: 2097152, max_attachments: 3, tokens_per_day: 200000,
    });
  });

  it('opens the conversations and the preview tabs', async () => {
    api.getWidgets.mockImplementation(() => ok([WIDGET]));
    api.getWidgetThreads.mockImplementation(() => ok([{
      thread_id: 'wth_1', widget_id: 'wgt_abc123', visitor_id: 'vis_abcdef99', title: 'Where is my order?',
      agent_id: 'helper', preview: true, visitor_deleted_at: null, message_count: 2,
      created_at: '2026-09-25T10:00:00Z', updated_at: '2026-09-25T10:01:00Z',
    }]));
    api.getWidgetThread.mockImplementation(() => ok({
      thread: { thread_id: 'wth_1' },
      messages: [
        { message_id: 'm1', role: 'user', text: 'Where is my order?', attachments: [], created_at: '2026-09-25T10:00:00Z' },
        { message_id: 'm2', role: 'assistant', text: 'On its way.', run_id: 'run-9', agent_name: 'Helper',
          status: 'ok', tokens: 50, citations: [], created_at: '2026-09-25T10:01:00Z' },
      ],
    }));
    show();
    fireEvent.click(await screen.findByRole('tab', { name: /conversations/i }));
    fireEvent.click(await screen.findByText('Where is my order?'));
    const link = await screen.findByRole('link', { name: /open run/i });
    expect(link.getAttribute('href')).toBe('/messages/run-9');
    expect(screen.getByText('On its way.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /preview/i }));
    const frame = await screen.findByTitle(/widget preview/i);
    expect(frame.getAttribute('srcdoc')).toContain('data-preview="tkt"');
    expect(api.createWidgetPreview).toHaveBeenCalledWith('wgt_abc123');
  });
});

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// Attaching an MCP server is the one place in this product where somebody
// hands over tools nobody here wrote, so two things are worth holding onto:
// the capability declaration has to be part of attaching rather than an
// advanced option, and a server that will not connect has to say why on the
// page instead of looking like a server with no tools.

const ok = (data) => Promise.resolve({ data });

const listMcpServers = vi.fn(() => ok({ servers: [], transports: ['stdio', 'streamable_http', 'sse'] }));
const createMcpServer = vi.fn(() => ok({ server: {} }));
const updateMcpServer = vi.fn(() => ok({ server: {} }));
const deleteMcpServer = vi.fn(() => ok({ deleted: true }));
const testMcpServer = vi.fn(() => ok({ ok: true, count: 1, tools: [] }));

vi.mock('../../api', () => ({
  listMcpServers: (...a) => listMcpServers(...a),
  createMcpServer: (...a) => createMcpServer(...a),
  updateMcpServer: (...a) => updateMcpServer(...a),
  deleteMcpServer: (...a) => deleteMcpServer(...a),
  testMcpServer: (...a) => testMcpServer(...a),
  listMcpServerTools: () => ok({ tools: [] }),
}));

vi.mock('../../components/workspace', () => ({
  useWorkspace: () => ({ selectedWorkspace: 'acme' }),
}));

import Mcp from '../Mcp';

const SERVER = {
  id: 'tickets',
  name: 'Tickets',
  transport: 'streamable_http',
  url: 'https://tickets.internal/mcp',
  headers: { Authorization: '••••1234' },
  env: {},
  args: [],
  enabled: true,
  capabilities: { ingests_untrusted: true, reads_private: true, can_exfiltrate: false },
  approval: 'all',
  tool_allowlist: [],
  tool_count: 3,
  cached: true,
  last_error: '',
  last_seen: '2026-01-05T10:00:00Z',
};

const show = () => render(
  <I18nProvider><MemoryRouter><Mcp /></MemoryRouter></I18nProvider>,
);

beforeEach(() => {
  localStorage.clear();
  [listMcpServers, createMcpServer, updateMcpServer, deleteMcpServer, testMcpServer]
    .forEach((fn) => fn.mockClear());
  listMcpServers.mockImplementation(() => ok({ servers: [], transports: ['stdio', 'streamable_http', 'sse'] }));
  testMcpServer.mockImplementation(() => ok({ ok: true, count: 1, tools: [] }));
});

describe('Mcp — the empty state', () => {
  it('explains what attaching one does before asking for a form', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no mcp server attached here/i)).toBeInTheDocument());
    expect(screen.getByText(/mcp__tickets__search/)).toBeInTheDocument();
  });

  it('asks for the capability declaration as part of attaching', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no mcp server attached here/i)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText(/attach a server/i)[0]);

    expect(screen.getByText(/what this collection can do/i)).toBeInTheDocument();
    // The agent editor's own wording, so the same claim reads the same way in
    // both places.
    expect(screen.getByText('ingests untrusted content')).toBeInTheDocument();
    expect(screen.getByText('reads private data')).toBeInTheDocument();
    expect(screen.getByText('can send data outside')).toBeInTheDocument();
  });

  it('switches the form between a child process and a URL', async () => {
    show();
    await waitFor(() => expect(screen.getByText(/no mcp server attached here/i)).toBeInTheDocument());
    fireEvent.click(screen.getAllByText(/attach a server/i)[0]);

    expect(screen.getByText('Command')).toBeInTheDocument();
    expect(screen.queryByText('URL')).not.toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue(/stdio/i), { target: { value: 'streamable_http' } });
    expect(screen.getByText('URL')).toBeInTheDocument();
    expect(screen.queryByText('Command')).not.toBeInTheDocument();
  });
});

describe('Mcp — a configured server', () => {
  it('shows what it grants and how many tools it has', async () => {
    listMcpServers.mockImplementation(() => ok({ servers: [SERVER], transports: ['stdio'] }));
    show();
    await waitFor(() => expect(screen.getByText('Tickets')).toBeInTheDocument());

    expect(screen.getByText(/3 tools/)).toBeInTheDocument();
    expect(screen.getByText('ingests untrusted content')).toBeInTheDocument();
    expect(screen.queryByText('can send data outside')).not.toBeInTheDocument();
  });

  it('says tools are not loaded rather than showing zero', async () => {
    listMcpServers.mockImplementation(() => ok({
      servers: [{ ...SERVER, tool_count: null, cached: false, last_seen: null }],
      transports: ['stdio'],
    }));
    show();
    await waitFor(() => expect(screen.getByText(/tools not loaded yet/i)).toBeInTheDocument());
    expect(screen.getByText(/never connected/i)).toBeInTheDocument();
  });

  it('shows the error from the last attempt on the row', async () => {
    listMcpServers.mockImplementation(() => ok({
      servers: [{ ...SERVER, last_error: 'connection refused' }],
      transports: ['stdio'],
    }));
    show();
    await waitFor(() => expect(screen.getByText(/connection refused/)).toBeInTheDocument());
  });

  it('lists what a test found, marking what the allowlist keeps', async () => {
    listMcpServers.mockImplementation(() => ok({ servers: [SERVER], transports: ['stdio'] }));
    testMcpServer.mockImplementation(() => ok({
      ok: true,
      count: 2,
      tools: [
        { id: 'mcp__tickets__search', name: 'search', description: 'Find one', allowed: true },
        { id: 'mcp__tickets__purge', name: 'purge', description: 'Delete all', allowed: false },
      ],
    }));
    show();
    await waitFor(() => expect(screen.getByText('Tickets')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => expect(screen.getByText('mcp__tickets__search')).toBeInTheDocument());
    expect(screen.getByText('granted')).toBeInTheDocument();
    expect(screen.getByText(/filtered out by the allowlist/i)).toBeInTheDocument();
  });

  it('reports a failed test as a result, not as a broken page', async () => {
    listMcpServers.mockImplementation(() => ok({ servers: [SERVER], transports: ['stdio'] }));
    testMcpServer.mockImplementation(() => ok({ ok: false, error: 'no such command', tools: [] }));
    show();
    await waitFor(() => expect(screen.getByText('Tickets')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => expect(screen.getByText(/could not connect/i)).toBeInTheDocument());
    expect(screen.getByText('no such command')).toBeInTheDocument();
  });

  it('sends a masked credential straight back, unedited', async () => {
    listMcpServers.mockImplementation(() => ok({ servers: [SERVER], transports: ['streamable_http'] }));
    show();
    await waitFor(() => expect(screen.getByText('Tickets')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Edit'));
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updateMcpServer).toHaveBeenCalled());
    const [, payload] = updateMcpServer.mock.calls[0];
    // The page never holds the real token, so it round-trips the mask and the
    // backend keeps what it stored.
    expect(payload.headers.Authorization).toBe('••••1234');
    // The id is not editable, so it is not part of the edit at all.
    expect(payload.id).toBeUndefined();
  });

  it('confirms before deleting', async () => {
    listMcpServers.mockImplementation(() => ok({ servers: [SERVER], transports: ['stdio'] }));
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    show();
    await waitFor(() => expect(screen.getByText('Tickets')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Delete'));
    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteMcpServer).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

describe('Mcp — translations', () => {
  const EVIDENCE = { ru: /[А-Яа-я]/, de: /\b(der|die|das|und|ein|eine|mit|für)\b/ };

  for (const lang of ['en', 'ru', 'de']) {
    it(`renders in ${lang} with no unresolved keys`, async () => {
      localStorage.setItem('agents_hub_language', lang);
      // The empty state, because it is the screen that carries the prose: a row
      // is mostly an id and a transport, which look the same in every language.
      const { container, unmount } = show();
      await waitFor(() => expect(container.querySelector('h3')).toBeTruthy());

      const text = container.textContent;
      expect(text.length).toBeGreaterThan(120);
      expect(text).not.toMatch(/mcp\.[a-zA-Z]/);
      expect(text).not.toMatch(/common\.[a-zA-Z]/);
      if (EVIDENCE[lang]) expect(text).toMatch(EVIDENCE[lang]);
      unmount();
    });
  }
});

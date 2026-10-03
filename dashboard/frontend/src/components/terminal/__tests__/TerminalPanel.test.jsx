import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../i18n';

// The panel wires xterm to the connection: bytes from the hub are written to
// the terminal, keystrokes go back as input, a resize of the terminal is sent
// as one. xterm itself needs a canvas jsdom does not have, so it is replaced
// by a recording fake.

const terms = [];
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    constructor(options) {
      this.options = options;
      this.cols = 80;
      this.rows = 24;
      this.written = [];
      terms.push(this);
    }
    loadAddon() {}
    open() {}
    focus() {}
    reset() { this.written = []; }
    dispose() { this.disposed = true; }
    write(data) { this.written.push(typeof data === 'string' ? data : new TextDecoder().decode(data)); }
    onData(cb) { this.dataCb = cb; return { dispose() {} }; }
    onResize(cb) { this.resizeCb = cb; return { dispose() {} }; }
  },
}));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@xterm/xterm/css/xterm.css', () => ({}));

const conn = {
  connect: vi.fn(), send: vi.fn(), resize: vi.fn(), close: vi.fn(), reconnect: vi.fn(),
};
let handlers = null;
vi.mock('../terminalConnection', () => ({
  createTerminalConnection: (opts) => { handlers = opts; return conn; },
}));

import TerminalPanel from '../TerminalPanel';

const show = (props = {}) => render(
  <I18nProvider>
    <TerminalPanel kind="run" id="r1" title="Terminal: swe" {...props} />
  </I18nProvider>,
);

beforeEach(() => {
  terms.length = 0;
  handlers = null;
  vi.clearAllMocks();
});

describe('TerminalPanel', () => {
  it('connects, writes output, sends keystrokes and resizes', () => {
    show();
    expect(conn.connect).toHaveBeenCalled();
    const term = terms[0];
    expect(handlers.kind).toBe('run');
    expect(handlers.getSize()).toEqual({ cols: 80, rows: 24 });
    act(() => handlers.onStatus('open', { container: 'agents-hub-run-1', grace_seconds: 60 }));
    expect(screen.getByTestId('terminal-status').textContent).toBe('Connected');
    expect(screen.getByText('agents-hub-run-1')).toBeTruthy();
    act(() => handlers.onOutput(new TextEncoder().encode('$ ')));
    expect(term.written).toContain('$ ');
    term.dataCb('ls\r');
    expect(conn.send).toHaveBeenCalledWith('ls\r');
    term.resizeCb({ cols: 120, rows: 40 });
    expect(conn.resize).toHaveBeenCalledWith(120, 40);
  });

  it('theme colors come from the tokens, with named fallbacks in a test DOM', () => {
    show();
    expect(terms[0].options.theme.background).toBe('black');
    expect(terms[0].options.theme.foreground).toBe('whitesmoke');
  });

  it('shows a refusal and offers a new session', () => {
    show();
    act(() => handlers.onStatus('error', { detail: 'This run executes in the hub\'s own process (local mode)' }));
    expect(screen.getByRole('alert').textContent).toMatch(/local mode/);
    fireEvent.click(screen.getByText('New session'));
    expect(conn.reconnect).toHaveBeenCalled();
  });

  it('notes why a session ended, in the terminal', () => {
    show();
    act(() => handlers.onStatus('ended', { why: 'exited', code: 0 }));
    expect(terms[0].written.join('')).toContain('the shell exited (code 0)');
    act(() => handlers.onNotice('resumed'));
    expect(terms[0].written.join('')).toContain('session resumed');
  });

  it('hide keeps the session, end session closes it', () => {
    const onClose = vi.fn();
    const { unmount } = show({ onClose });
    fireEvent.click(screen.getByLabelText('Hide'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(conn.close).not.toHaveBeenCalledWith({ end: true });
    fireEvent.click(screen.getByText('End session'));
    expect(conn.close).toHaveBeenCalledWith({ end: true });
    unmount();
    expect(conn.close).toHaveBeenLastCalledWith();
    expect(terms[0].disposed).toBe(true);
  });
});

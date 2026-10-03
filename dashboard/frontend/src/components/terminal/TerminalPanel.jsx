import { useEffect, useRef, useState } from 'react';
import { Loader, Power, RotateCcw, SquareTerminal, X } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { createTerminalConnection } from './terminalConnection';

/*
 * A shell in a run's or a service replica's container, docked at the bottom
 * of the page (docs/terminal.md). Opened from the run page for a container
 * run and from a replica's row on the service page.
 *
 * Hiding the panel only drops the socket: the hub keeps the shell for its
 * grace period, and opening the panel again (or reloading the page) resumes
 * it with the recent output replayed. "End session" closes the shell at once.
 *
 * The terminal is dark in both themes, so the ANSI colours programs print
 * stay readable; its surface and text come from the neutral ramp and the
 * cursor from the brand ramp of src/theme.css.
 */

const COLORS = {
  background: ['--neutral-900', 'black'],
  foreground: ['--neutral-100', 'whitesmoke'],
  cursor: ['--brand-400', 'cornflowerblue'],
  selection: ['--neutral-600', 'slategray'],
};

const STATUS_STYLES = {
  connecting: 'bg-gray-100 text-gray-600',
  reconnecting: 'bg-amber-100 text-amber-700',
  open: 'bg-emerald-100 text-emerald-700',
  ended: 'bg-gray-100 text-gray-600',
  taken: 'bg-amber-100 text-amber-700',
  error: 'bg-red-100 text-red-700',
};

const notice = (term, text) => {
  term.write(`\r\n\x1b[2m[${text}]\x1b[0m\r\n`);
};

export default function TerminalPanel({ kind, id, title, onClose }) {
  const { t } = useI18n();
  const colors = useThemeColors(COLORS);
  const hostRef = useRef(null);
  const termRef = useRef(null);
  const connRef = useRef(null);
  const [status, setStatus] = useState('connecting');
  const [info, setInfo] = useState({});

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      convertEol: false,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: {
        background: colors.background, foreground: colors.foreground,
        cursor: colors.cursor, selectionBackground: colors.selection,
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    try { fit.fit(); } catch { /* not laid out yet (a test DOM) */ }
    termRef.current = term;

    const conn = createTerminalConnection({
      kind, id,
      onOutput: (bytes) => term.write(bytes),
      onStatus: (next, detail) => {
        setStatus(next);
        setInfo(detail || {});
        if (next === 'ended') {
          notice(term, t(`terminal.reasons.${detail?.why || 'exited'}`, {
            code: detail?.code ?? '', defaultValue: detail?.reason || detail?.why,
          }));
        } else if (next === 'taken') {
          notice(term, t('terminal.notices.takenOver'));
        }
      },
      onNotice: (what) => notice(term, t(`terminal.notices.${what}`)),
      getSize: () => ({ cols: term.cols, rows: term.rows }),
    });
    connRef.current = conn;
    const input = term.onData((data) => conn.send(data));
    const sized = term.onResize(({ cols, rows }) => conn.resize(cols, rows));
    conn.connect();
    term.focus();

    let observer = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        try { fit.fit(); } catch { /* hidden */ }
      });
      observer.observe(hostRef.current);
    }
    return () => {
      observer?.disconnect();
      input.dispose();
      sized.dispose();
      conn.close();
      term.dispose();
      termRef.current = null;
      connRef.current = null;
    };
    // The terminal is built once per target; theme changes are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, id]);

  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.theme = {
      background: colors.background, foreground: colors.foreground,
      cursor: colors.cursor, selectionBackground: colors.selection,
    };
  }, [colors]);

  const endSession = () => {
    connRef.current?.close({ end: true });
    onClose?.();
  };
  const restart = () => {
    termRef.current?.reset();
    connRef.current?.reconnect();
  };

  const canRestart = ['ended', 'taken', 'error'].includes(status);
  const graceSeconds = info?.grace_seconds || 60;

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 flex flex-col h-[60vh] bg-white border-t border-gray-200 shadow-2xl"
         role="dialog" aria-label={t('terminal.title')} data-testid="terminal-panel">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 min-w-0">
        <SquareTerminal className="w-4 h-4 text-gray-500 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-gray-900 truncate">{title || t('terminal.title')}</div>
          {info?.container && (
            <div className="text-[11px] text-gray-500 truncate font-mono">{info.container}</div>
          )}
        </div>
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${STATUS_STYLES[status] || STATUS_STYLES.connecting}`}
              data-testid="terminal-status">
          {(status === 'connecting' || status === 'reconnecting') && <Loader className="w-3 h-3 animate-spin" />}
          {t(`terminal.status.${status}`, { defaultValue: status })}
        </span>
        {canRestart && (
          <button type="button" onClick={restart}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50">
            <RotateCcw className="w-3.5 h-3.5" />{t('terminal.newSession')}
          </button>
        )}
        {!canRestart && (
          <button type="button" onClick={endSession} title={t('terminal.endHint')}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs border border-red-200 rounded-lg text-red-600 hover:bg-red-50">
            <Power className="w-3.5 h-3.5" />{t('terminal.end')}
          </button>
        )}
        <button type="button" onClick={() => onClose?.()}
                title={t('terminal.hideHint', { seconds: graceSeconds })}
                aria-label={t('terminal.hide')}
                className="p-1.5 rounded hover:bg-gray-100 text-gray-500">
          <X className="w-4 h-4" />
        </button>
      </div>
      {status === 'error' && info?.detail && (
        <div className="px-3 py-2 text-xs text-red-700 bg-red-50 border-b border-red-100" role="alert">
          {info.detail}
        </div>
      )}
      <div className="flex-1 min-h-0 p-2" style={{ background: colors.background }}>
        <div ref={hostRef} className="w-full h-full" data-testid="terminal-host" />
      </div>
    </div>
  );
}

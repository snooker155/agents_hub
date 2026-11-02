import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import {
  isQuote, messageQuotes, quoteAttachment, quoteLabel, readQuotableSelection, withQuotes,
} from '../quote';
import { buildAttachmentLine, buildHistoryPayload, buildStreamRequestBody } from '../send/buildRequest';
import { attachmentsPayload } from '../composerPayload';
import SelectionDiscuss from '../SelectionDiscuss';
import { MessageBubble } from '../MessageBubble';

const t = (key, vars = {}) => {
  if (key === 'chat.quote.lines') return `${vars.source}, lines ${vars.from}-${vars.to}`;
  if (key === 'chat.quote.line') return `${vars.source}, line ${vars.from}`;
  if (key === 'chat.quote.fallbackSource') return 'chat';
  if (key === 'chat.attachedFiles') return `Files: ${vars.files}`;
  return key;
};

/** Selects `text` (its first match) inside the text node of `el`. */
function select(el, text) {
  const node = [...el.childNodes].find((n) => n.nodeType === 3 && n.textContent.includes(text));
  const start = node.textContent.indexOf(text);
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(node, start + text.length);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  return sel;
}

afterEach(() => {
  window.getSelection().removeAllRanges();
  document.body.innerHTML = '';
});

describe('readQuotableSelection', () => {
  it('reads a passage of a file with its lines', () => {
    document.body.innerHTML = '<div data-quote-source="app.py" data-quote-lines><pre id="f">a = 1\nb = 2\nc = 3\nd = 4\n</pre></div>';
    const pick = readQuotableSelection(select(document.getElementById('f'), 'b = 2\nc = 3'));
    expect(pick).toMatchObject({ text: 'b = 2\nc = 3', source: 'app.py', lines: [2, 3] });
    expect(quoteLabel(t, pick)).toBe('app.py, lines 2-3');
  });

  it('reads a reply without lines', () => {
    document.body.innerHTML = '<div data-quote-source="reply of researcher"><p id="p">The rate rose by 4%.</p></div>';
    const pick = readQuotableSelection(select(document.getElementById('p'), 'rose by 4%'));
    expect(pick).toMatchObject({ text: 'rose by 4%', source: 'reply of researcher', lines: null });
    expect(quoteLabel(t, pick)).toBe('reply of researcher');
  });

  it('ignores a selection outside anything quotable', () => {
    document.body.innerHTML = '<p id="p">composer text</p>';
    expect(readQuotableSelection(select(document.getElementById('p'), 'composer'))).toBeNull();
  });
});

describe('a quote as an attachment', () => {
  const quote = quoteAttachment({ id: 'q1', text: 'b = 2', label: 'app.py, line 2' });

  it('is sent with where it is from, never stored', () => {
    expect(isQuote(quote)).toBe(true);
    const body = buildStreamRequestBody({ pendingAttachments: [quote], pendingReferences: [], selectedWorkspace: 'ws' });
    expect(body.attachments).toEqual([{ filename: 'app.py, line 2', content: 'b = 2', store_to_workspace: false, quote_of: 'app.py, line 2' }]);
    expect(attachmentsPayload([quote], 'ws')[0].quote_of).toBe('app.py, line 2');
  });

  it('is not listed among the attached files, and rides on the message', () => {
    const file = { id: 'f1', filename: 'notes.txt', content: 'x' };
    expect(buildAttachmentLine(t, { pendingAttachments: [quote, file], pendingReferences: [] })).toBe('Files: notes.txt');
    expect(messageQuotes([quote, file])).toEqual([{ source: 'app.py, line 2', text: 'b = 2' }]);
  });

  it('stays in history ahead of the request', () => {
    const history = buildHistoryPayload([
      { role: 'user', content: 'why two?', quotes: [{ source: 'app.py, line 2', text: 'b = 2' }] },
      { role: 'user', content: '', quotes: [{ source: 'reply', text: 'only a quote' }] },
    ]);
    expect(history[0].content).toBe('[Quoted from app.py, line 2]\n> b = 2\n\nwhy two?');
    expect(history[1].content).toBe('[Quoted from reply]\n> only a quote');
    expect(withQuotes('plain', [])).toBe('plain');
  });
});

describe('SelectionDiscuss', () => {
  it('shows Discuss over a quotable selection and hands the passage over', async () => {
    const onQuote = vi.fn();
    render(
      <I18nProvider>
        <div data-quote-source="report.md"><p data-testid="text">Revenue grew in Q3.</p></div>
        <SelectionDiscuss onQuote={onQuote} />
      </I18nProvider>,
    );
    select(screen.getByTestId('text'), 'grew in Q3');
    await act(async () => {
      fireEvent.mouseUp(document);
      await new Promise((r) => setTimeout(r, 0));
    });
    fireEvent.click(screen.getByTestId('selection-discuss'));
    expect(onQuote).toHaveBeenCalledWith(expect.objectContaining({ text: 'grew in Q3', source: 'report.md' }));
    expect(screen.queryByTestId('selection-discuss')).toBeNull();
  });
});

describe('MessageBubble with quotes', () => {
  it('shows the quoted passage above the request', () => {
    render(
      <I18nProvider>
        <MessageBubble msg={{ id: 'u1', role: 'user', content: 'explain', quotes: [{ source: 'app.py, line 2', text: 'b = 2' }] }} />
      </I18nProvider>,
    );
    const quote = screen.getByTestId('message-quote');
    expect(quote).toHaveTextContent('app.py, line 2');
    expect(quote).toHaveTextContent('b = 2');
  });
});

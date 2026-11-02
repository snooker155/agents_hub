/**
 * Quoting a passage of the chat: text the person selected in a reply, a file
 * of the Artifacts panel or code, attached to the next message as a quote
 * (an attachment with `quote_of`, chat/context.py shows it to the model as
 * one), not pasted into the box.
 *
 * What can be quoted is marked in the DOM: an element with
 * `data-quote-source` (where the passage is from, as the person reads it),
 * and `data-quote-lines` when its `<pre>` holds a file verbatim, so a
 * selection there is quoted with its line numbers. A selection in a
 * CodeMirror editor is read from the editor (lib/editorRegistry.js).
 */
import { editorFor } from '../../lib/editorRegistry';

/** The longest passage a quote carries; the prompt cuts at the same length. */
export const QUOTE_MAX_CHARS = 40_000;

const elementOf = (node) => (node?.nodeType === 1 ? node : node?.parentElement) || null;

const countLines = (text) => (text.match(/\n/g) || []).length;

/** The first and last line `text` takes, starting at line `first`. */
function lineSpan(first, text) {
  return [first, first + countLines(text.replace(/\n+$/, ''))];
}

/**
 * The passage selected on the page, if it can be quoted: `{ text, source,
 * lines, rect }`, `lines` being `[from, to]` when known. Null for no
 * selection, one outside a quotable element, or one running from one such
 * element into another.
 */
export function readQuotableSelection(selection = typeof window !== 'undefined' ? window.getSelection() : null) {
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const start = elementOf(range.startContainer);
  const end = elementOf(range.endContainer);
  const host = start?.closest?.('[data-quote-source]');
  if (!host || !end || !host.contains(end)) return null;
  const source = host.getAttribute('data-quote-source') || '';

  let text = '';
  let lines = null;
  const view = editorFor(start);
  if (view && view === editorFor(end)) {
    // The editor's own text and lines: its DOM holds only the lines in sight.
    const { from, to } = view.state.selection.main;
    if (from !== to) {
      text = view.state.sliceDoc(from, to);
      lines = lineSpan(view.state.doc.lineAt(from).number, text);
    }
  }
  if (!text) {
    text = range.toString();
    const pre = host.hasAttribute('data-quote-lines') ? start.closest('pre') : null;
    if (pre && host.contains(pre) && pre.contains(end)) {
      const before = document.createRange();
      before.setStart(pre, 0);
      before.setEnd(range.startContainer, range.startOffset);
      lines = lineSpan(countLines(before.toString()) + 1, text);
    }
  }
  if (!text.trim()) return null;
  // Where the button goes; the host's box where a Range has none (jsdom).
  const rect = typeof range.getBoundingClientRect === 'function' ? range.getBoundingClientRect() : host.getBoundingClientRect();
  return { text, source, lines, rect };
}

/** Where a quote is from, as the person and the model read it. */
export function quoteLabel(t, { source, lines }) {
  const name = source || t('chat.quote.fallbackSource');
  if (!lines) return name;
  const [from, to] = lines;
  return from === to
    ? t('chat.quote.line', { source: name, from })
    : t('chat.quote.lines', { source: name, from, to });
}

/**
 * The quote as a composer attachment: the passage is its content, `quote_of`
 * where it is from (also its filename, for what lists attachments by name).
 */
export function quoteAttachment({ id, text, label }) {
  const content = text.length > QUOTE_MAX_CHARS ? `${text.slice(0, QUOTE_MAX_CHARS)}\n…` : text;
  return {
    id, quote_of: label, filename: label, content,
    size: new Blob([content]).size, store_to_workspace: false,
  };
}

export const isQuote = (attachment) => typeof attachment?.quote_of === 'string';

/** The quotes of a sent message, as its bubble keeps them. */
export function messageQuotes(attachments) {
  return (attachments || []).filter(isQuote).map((a) => ({ source: a.quote_of, text: a.content }));
}

/** History cuts each quote of an earlier message to this many characters. */
const HISTORY_QUOTE_CHARS = 4_000;

/**
 * A sent message as the model reads it again in history: its quotes ahead of
 * its text, so "this" in an earlier request still points at something.
 */
export function withQuotes(content, quotes) {
  if (!quotes?.length) return content;
  const blocks = quotes.map((q) => {
    const body = q.text.length > HISTORY_QUOTE_CHARS ? `${q.text.slice(0, HISTORY_QUOTE_CHARS)}\n…` : q.text;
    return `[Quoted from ${q.source}]\n${body.split('\n').map((l) => `> ${l}`).join('\n')}`;
  });
  return [...blocks, content].filter(Boolean).join('\n\n');
}

/**
 * The Discuss button over a selection: select a passage in a reply, a file of
 * the Artifacts panel or code (anything marked `data-quote-source`, see
 * quote.js) and the button floats above it; a click attaches the passage to
 * the next message as a quote. One per page, listening on the document.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MessageSquareQuote } from 'lucide-react';
import { useI18n } from '../../i18n';
import { readQuotableSelection } from './quote';

const BUTTON_HEIGHT = 30;
const GAP = 6;

/** Above the selection, or under it when there is no room above. */
function placeFor(rect) {
  const above = rect.top - BUTTON_HEIGHT - GAP;
  const top = above >= 8 ? above : rect.bottom + GAP;
  const left = Math.min(Math.max(rect.left + rect.width / 2, 60), window.innerWidth - 60);
  return { top, left };
}

export default function SelectionDiscuss({ onQuote }) {
  const { t } = useI18n();
  const [pick, setPick] = useState(null);
  const pickRef = useRef(null);
  useEffect(() => { pickRef.current = pick; }, [pick]);

  useEffect(() => {
    // After the event: the selection is final once mouseup / keyup is handled.
    let timer = null;
    const read = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setPick(readQuotableSelection()), 0);
    };
    const onKeyUp = (e) => { if (e.shiftKey || e.key === 'Shift' || e.metaKey || e.ctrlKey) read(); };
    const onDown = (e) => { if (!e.target?.closest?.('[data-quote-discuss]')) setPick(null); };
    const onSelection = () => {
      const sel = window.getSelection();
      if (pickRef.current && (!sel || sel.isCollapsed)) setPick(null);
    };
    // A scroll moves the passage: the button follows it.
    const onScroll = () => {
      if (!pickRef.current) return;
      const sel = window.getSelection();
      const range = sel?.rangeCount ? sel.getRangeAt(0) : null;
      if (typeof range?.getBoundingClientRect !== 'function') return;
      const rect = range.getBoundingClientRect();
      setPick((p) => (p ? { ...p, rect } : p));
    };
    const onEscape = (e) => { if (e.key === 'Escape') setPick(null); };
    document.addEventListener('mouseup', read);
    document.addEventListener('keyup', onKeyUp);
    document.addEventListener('keydown', onEscape);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('selectionchange', onSelection);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mouseup', read);
      document.removeEventListener('keyup', onKeyUp);
      document.removeEventListener('keydown', onEscape);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('selectionchange', onSelection);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  const discuss = useCallback(() => {
    if (!pick) return;
    onQuote(pick);
    window.getSelection()?.removeAllRanges();
    setPick(null);
  }, [pick, onQuote]);

  if (!pick) return null;
  const { top, left } = placeFor(pick.rect);
  return createPortal(
    <button
      type="button"
      data-quote-discuss
      data-testid="selection-discuss"
      // Keeps the selection (and the editor's focus) until the click lands.
      onMouseDown={(e) => e.preventDefault()}
      onClick={discuss}
      title={t('chat.quote.discussHint')}
      style={{ top, left, height: BUTTON_HEIGHT }}
      className="fixed z-[60] -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full bg-indigo-600 px-3 text-xs font-medium text-white shadow-lg hover:bg-indigo-700"
    >
      <MessageSquareQuote className="h-3.5 w-3.5" />
      {t('chat.quote.discuss')}
    </button>,
    document.body,
  );
}

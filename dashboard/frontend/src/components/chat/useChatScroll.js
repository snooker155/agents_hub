/**
 * How the transcript scrolls.
 *
 * It sticks to the bottom: a conversation opens at its end, straight away, and a
 * reply being written keeps its last line in view. Content that settles after
 * the first paint (a formula, a view, a code block) is followed the same way,
 * without the smooth scroll that used to replay the whole conversation on open.
 * Scrolling up lets go; scrolling back to the end, or sending, takes hold again.
 *
 * It also keeps the prompt the reply on screen answers in view. Each turn
 * (a prompt and the replies to it) is one block, `[data-turn]`, and the prompt
 * inside it, `[data-turn-prompt]`, is `position: sticky`, so the browser holds
 * it at the top while its turn scrolls by and the next prompt pushes it out.
 * Nothing is copied or re-rendered on scroll. This hook only marks a held
 * prompt (`data-stuck`), which folds a long one to a couple of lines, and
 * grows the spacer after it, `[data-turn-spacer]`, by what the fold took away,
 * so the rest of the turn does not move.
 */
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';

// Within this many pixels of the end still counts as "at the end".
const BOTTOM_SLACK = 48;

export function useChatScroll({ convId, lastUserId, messages, stuckHint }) {
  const scrollerRef = useRef(null);
  const contentRef = useRef(null);
  const atBottomRef = useRef(true);
  const frameRef = useRef(0);
  const hintRef = useRef(stuckHint);
  useEffect(() => { hintRef.current = stuckHint; }, [stuckHint]);

  const toBottom = useCallback(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // A prompt is held once its turn has started above the top of the scroller
  // and is still on screen. A turn's top is where its prompt sits in the flow,
  // so this does not depend on the prompt's own, already shifted, position.
  const updateStuck = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    const turns = el.querySelectorAll('[data-turn]');
    for (let i = 0; i < turns.length; i += 1) {
      const turn = turns[i];
      const slot = turn.querySelector(':scope > [data-turn-prompt]');
      if (!slot) continue;
      const rect = turn.getBoundingClientRect();
      const stuck = rect.top < top - 1 && rect.bottom > top;
      if (stuck === (slot.dataset.stuck === 'true')) continue;
      const spacer = turn.querySelector(':scope > [data-turn-spacer]');
      const bubble = slot.querySelector('[data-prompt-bubble]');
      if (stuck) {
        const full = slot.offsetHeight;
        slot.dataset.stuck = 'true';
        if (spacer) spacer.style.height = `${Math.max(0, full - slot.offsetHeight)}px`;
        if (bubble && hintRef.current) bubble.title = hintRef.current;
      } else {
        slot.dataset.stuck = 'false';
        if (spacer) spacer.style.height = '0px';
        if (bubble) bubble.removeAttribute('title');
      }
    }
  }, []);

  const scheduleStuck = useCallback(() => {
    if (frameRef.current) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = 0;
      updateStuck();
    });
  }, [updateStuck]);

  useEffect(() => () => {
    if (frameRef.current) cancelAnimationFrame(frameRef.current);
    frameRef.current = 0;
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
    scheduleStuck();
  }, [scheduleStuck]);

  // Another conversation, or a new prompt in this one: to the end, before paint.
  useLayoutEffect(() => {
    atBottomRef.current = true;
    toBottom();
    scheduleStuck();
  }, [convId, lastUserId, toBottom, scheduleStuck]);

  // Anything else that changes the transcript's height (tokens, a view that
  // loaded, a card opened) keeps the end in view while the reader is there.
  useLayoutEffect(() => {
    if (atBottomRef.current) toBottom();
    scheduleStuck();
  }, [messages, toBottom, scheduleStuck]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => {
      if (atBottomRef.current) toBottom();
      scheduleStuck();
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, [toBottom, scheduleStuck]);

  // Back to where the prompt sits in the flow, a little below the top so it
  // is released and shows whole.
  const scrollToPrompt = useCallback((id) => {
    const el = scrollerRef.current;
    const turn = el?.querySelector(`[data-turn="${CSS.escape(String(id))}"]`);
    if (!el || !turn) return;
    const offset = turn.getBoundingClientRect().top - el.getBoundingClientRect().top;
    el.scrollTo({ top: el.scrollTop + offset - 16, behavior: 'smooth' });
  }, []);

  // A click on a held prompt goes back to the whole message; an unheld one
  // is an ordinary bubble.
  const onPromptClick = useCallback((event) => {
    const slot = event.currentTarget;
    if (slot.dataset.stuck !== 'true') return;
    scrollToPrompt(slot.getAttribute('data-turn-prompt'));
  }, [scrollToPrompt]);

  return { scrollerRef, contentRef, onScroll, onPromptClick, scrollToPrompt };
}

export default useChatScroll;

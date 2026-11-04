import { useCallback, useEffect, useRef, useState } from 'react';

// Below Tailwind's `sm`: a phone.
const PHONE = '(max-width: 639px)';
// How far the reader has to move before the bar goes or comes back.
const STEP_PX = 8;
// A scroll this long after a touch or the wheel is the reader's (a fling goes
// on after the finger lifts); any other is the page following the reply.
const GESTURE_MS = 1500;
// The bar's slide (the wrapper's transition in pages/Chat.jsx): scrolling in
// this time is the transcript resizing under it, not the reader.
const SETTLE_MS = 250;

/**
 * On a phone the chat's top bar scrolls away with the transcript: reading
 * down slides it up out of sight, scrolling back up brings it back, the way a
 * phone browser's own bar behaves. Only the reader's own scrolling moves it;
 * the page keeping a growing reply in view, or jumping to the end of a chat
 * that opens, does not. Another conversation shows it again.
 *
 * A transcript that would fit once the bar is gone keeps the bar: hiding it
 * there grows the transcript past its content, the browser takes the scroll
 * back to the top, that reads as scrolling up and the bar would come and go
 * for as long as the finger moves. The same goes for the scroll the slide
 * itself causes, which is let pass.
 *
 * Returns the ref for the bar's wrapper, its top margin while hidden (minus
 * its height, null while shown) and the transcript's scroll handler.
 */
export function useHideBarOnScroll(convId) {
  const barRef = useRef(null);
  const [offset, setOffset] = useState(null);
  const lastTop = useRef(0);
  const gestureAt = useRef(0);
  const movedAt = useRef(0);

  useEffect(() => {
    const mark = () => { gestureAt.current = Date.now(); };
    window.addEventListener('touchstart', mark, { passive: true });
    window.addEventListener('touchmove', mark, { passive: true });
    window.addEventListener('wheel', mark, { passive: true });
    return () => {
      window.removeEventListener('touchstart', mark);
      window.removeEventListener('touchmove', mark);
      window.removeEventListener('wheel', mark);
    };
  }, []);

  // Another conversation starts with the bar in view (adjusted while rendering).
  const [seenConv, setSeenConv] = useState(convId);
  if (convId !== seenConv) {
    setSeenConv(convId);
    setOffset(null);
  }

  const onTranscriptScroll = useCallback((el) => {
    const top = el.scrollTop;
    const now = Date.now();
    if (now - movedAt.current < SETTLE_MS) { lastTop.current = top; return; }
    const delta = top - lastTop.current;
    if (Math.abs(delta) < STEP_PX) return;
    lastTop.current = top;
    if (typeof window.matchMedia !== 'function' || !window.matchMedia(PHONE).matches) return;
    if (now - gestureAt.current > GESTURE_MS) return;
    const bar = barRef.current?.offsetHeight || 0;
    // Room to scroll with the bar gone, or hiding it only bounces the scroll back.
    const room = el.scrollHeight - el.clientHeight - bar;
    const move = (next) => setOffset((cur) => {
      if (cur === next) return cur;
      movedAt.current = now;
      return next;
    });
    if (delta > 0 && top > 0 && room > STEP_PX) move(-bar);
    else if (delta < 0) move(null);
  }, []);

  return { barRef, barOffset: offset, onTranscriptScroll };
}

export default useHideBarOnScroll;

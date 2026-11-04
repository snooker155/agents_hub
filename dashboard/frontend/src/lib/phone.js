import { useEffect, useState } from 'react';

// A phone whichever way it is held: narrow, or turned on its side (short and
// touched). Kept equal to the `phone:` variant in tailwind.config.js, so what
// the code decides and what the classes draw agree.
export const PHONE_QUERY = '(max-width: 639px), (max-height: 500px) and (pointer: coarse)';

/** Whether the page is on a phone (PHONE_QUERY), following the phone as it
    turns. */
export function usePhone() {
  const query = () => (typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(PHONE_QUERY).matches : false);
  const [phone, setPhone] = useState(query);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const mq = window.matchMedia(PHONE_QUERY);
    const on = () => setPhone(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return phone;
}

// A page's content edge to edge on a phone: the page's side gutter undone
// (px-4, sm:px-6 on a phone on its side) and its cards, the block's own or
// one level down (a grid of them), square with no side borders.
export const PHONE_BLEED = 'max-sm:-mx-4 sm:phone:-mx-6 '
  + 'phone:[&>div.rounded-xl]:rounded-none phone:[&>div.rounded-xl]:border-x-0 '
  + 'phone:[&>div>div.rounded-xl]:rounded-none phone:[&>div>div.rounded-xl]:border-x-0';

// A row across the page edge to edge on a phone (a tab row's line): the
// page's side gutter undone on the row, and given back inside it, so what
// scrolls in it runs to the screen's edge but starts in line with the page.
export const PHONE_ROW_BLEED = 'max-sm:-mx-4 sm:phone:-mx-6';
export const PHONE_ROW_PAD = 'max-sm:px-4 sm:phone:px-6';

// A file's text on a phone with nothing around it: the pane's padding gone
// when what it holds is a code block (its own padding and header suffice) or
// plain text (kept a hair off the edge), the block square and edge to edge.
export const PHONE_SOURCE_EDGE = 'phone:has-[>.hl-code-block]:p-0 '
  + 'phone:[&>.hl-code-block]:m-0 phone:[&>.hl-code-block]:rounded-none phone:[&>.hl-code-block]:border-0 '
  + 'phone:has-[>pre]:p-2';

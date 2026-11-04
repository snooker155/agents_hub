import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useHideBarOnScroll } from '../useHideBarOnScroll';

// On a phone the chat's top bar scrolls away with the transcript, and never
// where hiding it would leave nothing to scroll (the empty chat's flicker).

const BAR = 80;

function setup() {
  const hook = renderHook(() => useHideBarOnScroll('c1'));
  hook.result.current.barRef.current = { offsetHeight: BAR };
  return hook;
}

const scroller = (scrollHeight, clientHeight) => ({ scrollTop: 0, scrollHeight, clientHeight });

function swipeTo(hook, el, top) {
  window.dispatchEvent(new Event('touchstart'));
  el.scrollTop = top;
  act(() => hook.result.current.onTranscriptScroll(el));
}

describe('useHideBarOnScroll', () => {
  let now = 10_000;
  beforeEach(() => {
    window.matchMedia = vi.fn(() => ({ matches: true }));
    vi.spyOn(Date, 'now').mockImplementation(() => now);
  });
  afterEach(() => vi.restoreAllMocks());

  it('hides the bar reading down a long transcript and brings it back scrolling up', () => {
    const hook = setup();
    const el = scroller(3000, 600);
    swipeTo(hook, el, 200);
    expect(hook.result.current.barOffset).toBe(-BAR);
    now += 1000;
    swipeTo(hook, el, 150);
    expect(hook.result.current.barOffset).toBeNull();
  });

  it('keeps the bar where the transcript would fit without it', () => {
    const hook = setup();
    // 60px to scroll: less than the bar, so hiding it would bounce the scroll back.
    const el = scroller(660, 600);
    swipeTo(hook, el, 40);
    expect(hook.result.current.barOffset).toBeNull();
  });

  it('lets the scroll its own slide causes pass', () => {
    const hook = setup();
    const el = scroller(3000, 600);
    swipeTo(hook, el, 200);
    expect(hook.result.current.barOffset).toBe(-BAR);
    // The transcript grows as the bar goes and the browser moves the scroll.
    now += 100;
    swipeTo(hook, el, 120);
    expect(hook.result.current.barOffset).toBe(-BAR);
  });

  it('does nothing on a wider screen', () => {
    window.matchMedia = vi.fn(() => ({ matches: false }));
    const hook = setup();
    swipeTo(hook, scroller(3000, 600), 200);
    expect(hook.result.current.barOffset).toBeNull();
  });
});

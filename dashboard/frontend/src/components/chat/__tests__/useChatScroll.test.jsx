import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { useChatScroll } from '../useChatScroll';

// Every element a ResizeObserver is watching, and the callback that fires
// when one of them changes size.
const observed = new Set();
let fire = () => {};
class FakeResizeObserver {
  constructor(cb) { fire = cb; }
  observe(el) { observed.add(el); }
  disconnect() { observed.clear(); }
}

// A host like the flow chat: the content is mounted only once there are
// messages, a placeholder before that.
function Host({ messages }) {
  const { scrollerRef, contentRef, onScroll } = useChatScroll({ convId: 'c1', lastUserId: null, messages });
  return (
    <div ref={scrollerRef} onScroll={onScroll} data-testid="scroller">
      {messages.length ? <div ref={contentRef} data-testid="content">{messages.join(' ')}</div> : <p>empty</p>}
    </div>
  );
}

describe('useChatScroll', () => {
  afterEach(() => { vi.unstubAllGlobals(); observed.clear(); });

  it('follows content that appears after the first render', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    const { rerender, getByTestId } = render(<Host messages={[]} />);
    rerender(<Host messages={['hi']} />);
    const content = getByTestId('content');
    expect(observed.has(content)).toBe(true);

    // The content grows (a view loaded): the scroller goes to its end.
    const scroller = getByTestId('scroller');
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 900 });
    fire();
    expect(scroller.scrollTop).toBe(900);
  });
});

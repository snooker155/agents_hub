import { describe, expect, it, vi } from 'vitest';
import { copyBubbleText } from '../chatText';

function copyFrom(bubble, select) {
  const range = document.createRange();
  select(range);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const setData = vi.fn();
  const event = { currentTarget: bubble, clipboardData: { setData }, preventDefault: vi.fn() };
  copyBubbleText(event);
  return { event, setData };
}

describe('copyBubbleText', () => {
  it('drops the line break a selection to the end of the bubble picks up', () => {
    document.body.innerHTML = '<div id="b">hello world</div><div id="next">reply</div>';
    const bubble = document.getElementById('b');
    const { event, setData } = copyFrom(bubble, (r) => {
      r.setStart(bubble.firstChild, 0);
      r.setEnd(document.getElementById('next'), 0);
    });
    // jsdom renders no block break, so fake the browser's one.
    if (!setData.mock.calls.length) {
      vi.spyOn(window.getSelection(), 'toString').mockReturnValue('hello world\n');
      copyBubbleText(event);
    }
    expect(event.preventDefault).toHaveBeenCalled();
    expect(setData).toHaveBeenCalledWith('text/plain', 'hello world');
  });

  it('leaves a selection reaching into the next message to the browser', () => {
    document.body.innerHTML = '<div id="b">hello</div><div id="next">reply</div>';
    const bubble = document.getElementById('b');
    const next = document.getElementById('next');
    const { event, setData } = copyFrom(bubble, (r) => {
      r.setStart(bubble.firstChild, 0);
      r.setEnd(next.firstChild, 5);
    });
    vi.spyOn(window.getSelection(), 'toString').mockReturnValue('hello\nreply\n');
    copyBubbleText(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(setData).not.toHaveBeenCalled();
  });
});

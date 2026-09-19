import { render, screen } from '@testing-library/react';
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { ChatColumn } from '../ChatColumn';

/**
 * The column's height is measured, not written down, because how much screen is
 * left below it differs per page. These pin the measurement itself: the two
 * bounds, and the choice of what to measure from.
 */
describe('ChatColumn height', () => {
  beforeEach(() => {
    window.innerHeight = 900;
  });

  const renderAt = (rowTop) => {
    // The column measures its parent row, which never sticks.
    const spy = vi.spyOn(Element.prototype, 'getBoundingClientRect')
      .mockReturnValue({ top: rowTop, bottom: 0, left: 0, right: 0, width: 0, height: 0 });
    const { container } = render(
      <div>
        <ChatColumn><p>chat</p></ChatColumn>
      </div>,
    );
    spy.mockRestore();
    return container.querySelector('aside');
  };

  it('fills what is left of the screen below the row', () => {
    const aside = renderAt(200);
    // 900 viewport - 200 top - 24 gap
    expect(aside.style.getPropertyValue('--chat-column-height')).toBe('676px');
  });

  it('never grows past the screen when the page is scrolled', () => {
    // A recalculation while scrolled puts the row above the fold.
    const aside = renderAt(-500);
    expect(aside.style.getPropertyValue('--chat-column-height')).toBe('852px');
  });

  it('never collapses below a usable height', () => {
    const aside = renderAt(880);
    expect(aside.style.getPropertyValue('--chat-column-height')).toBe('320px');
  });

  it('keeps the small-screen height out of the inline style', () => {
    const aside = renderAt(200);
    expect(aside.className).toContain('h-[70vh]');
    expect(aside.style.height).toBe('');
  });
});

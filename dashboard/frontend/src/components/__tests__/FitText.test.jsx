import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import FitText from '../FitText';

// A long name steps its font down until it fits its box (jsdom has no
// layout: the text is 20px wide per px of font, the box 260px).

describe('FitText', () => {
  afterEach(() => vi.restoreAllMocks());

  const layout = (box) => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(box);
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function width() {
      return 20 * parseFloat(this.style.fontSize || '16');
    });
  };

  it('takes the largest size that fits', () => {
    layout(260);
    render(<FitText>a_rather_long_file_name.py</FitText>);
    expect(screen.getByText('a_rather_long_file_name.py').style.fontSize).toBe('13px');
  });

  it('stays at the first size when the name fits', () => {
    layout(400);
    render(<FitText sizes={[14, 12]}>short.py</FitText>);
    expect(screen.getByText('short.py').style.fontSize).toBe('14px');
  });

  it('ends at the smallest size, cut with an ellipsis, when nothing fits', () => {
    layout(100);
    render(<FitText>very_long_name.py</FitText>);
    const el = screen.getByText('very_long_name.py');
    expect(el.style.fontSize).toBe('11px');
    expect(el).toHaveClass('truncate');
  });
});

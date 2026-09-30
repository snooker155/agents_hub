import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ComposerDock from '../ComposerDock';

/**
 * The dock is the page's floor: fixed to the bottom of the screen, drawn
 * outside the page so nothing on it can clip the box, with a spacer left in
 * the page's flow so the page can still be scrolled clear of the box.
 */
describe('ComposerDock', () => {
  it('draws the composer fixed at the bottom of the screen', () => {
    render(<ComposerDock><textarea aria-label="say" /></ComposerDock>);
    const dock = screen.getByTestId('composer-dock');
    expect(dock.className).toContain('fixed');
    expect(dock.className).toContain('bottom-0');
    expect(screen.getByLabelText('say')).toBeTruthy();
  });

  it('is rendered on the body, not inside the page', () => {
    const { container } = render(<div data-testid="page"><ComposerDock><p>box</p></ComposerDock></div>);
    const dock = screen.getByTestId('composer-dock');
    expect(container.contains(dock)).toBe(false);
    expect(document.body.contains(dock)).toBe(true);
  });

  it('leaves a spacer in the page where it stands', () => {
    const { container } = render(<div><ComposerDock><p>box</p></ComposerDock></div>);
    expect(container.querySelector('[data-testid="composer-dock-spacer"]')).toBeTruthy();
  });

  it('centres a box narrower than half the page', () => {
    render(<ComposerDock><p>box</p></ComposerDock>);
    const inner = screen.getByText('box').parentElement;
    expect(inner.className).toContain('mx-auto');
    expect(inner.className).toContain('lg:w-[44%]');
  });

  it('tells the document how tall it is, and takes that back on unmount', () => {
    const { unmount } = render(<ComposerDock><p>box</p></ComposerDock>);
    expect(document.documentElement.style.getPropertyValue('--composer-dock-height')).toMatch(/px$/);
    unmount();
    expect(document.documentElement.style.getPropertyValue('--composer-dock-height')).toBe('0px');
  });
});

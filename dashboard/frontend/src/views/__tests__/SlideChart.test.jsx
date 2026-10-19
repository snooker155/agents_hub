import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import SlideStage from '../slides/SlideStage';
import { formatValue, niceTicks, seriesColours } from '../slides/chartMath';
import THEMES from '../slideThemes.json';

const sales = {
  type: 'line',
  categories: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
  series: [
    { name: 'Espresso', values: [40, 38, 45, 50, 62] },
    { name: 'Latte', values: [55, 60, 52, 70, 85] },
    { name: 'Cappuccino', values: [30, 28, null, 40, 48] },
  ],
  x_label: 'Day',
  y_label: 'Cups',
};

const stage = (slide, spec = { theme: 'corporate' }) =>
  render(<SlideStage slide={slide} spec={spec} number={2} total={3} />);

describe('chart math', () => {
  it('picks round ticks from zero up past the largest value', () => {
    expect(niceTicks(0, 85)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(-12, 30)).toEqual([-20, -10, 0, 10, 20, 30]);
    expect(niceTicks(0, 0)).toEqual([0, 1]);
  });

  it('colours series with the accent, the second accent, then a fixed set', () => {
    const t = THEMES.corporate;
    expect(seriesColours(t, '#123456').slice(0, 3)).toEqual(['#123456', t.accent2, '#10b981']);
    expect(seriesColours(t, '')[0]).toBe(t.accent);
  });

  it('puts the unit after the number', () => {
    expect(formatValue(12.5, '%')).toBe('12.5%');
    expect(formatValue(null, '%')).toBe('');
  });
});

describe('chart slide', () => {
  it('draws a line per series with a legend and both axis titles', () => {
    const { container, getByText } = stage({ layout: 'chart', title: 'Sales by day', chart: sales });
    expect(getByText('Sales by day')).toBeTruthy();
    // The null in Cappuccino breaks its line in two.
    expect(container.querySelectorAll('polyline')).toHaveLength(4);
    for (const name of ['Espresso', 'Latte', 'Cappuccino', 'Day', 'Cups', 'Mon', 'Fri']) {
      expect(getByText(name)).toBeTruthy();
    }
  });

  it('draws grouped bars with value labels, and the takeaway beside the chart', () => {
    const { container, getByText } = stage({
      layout: 'chart', title: 'Totals', body: '**Latte** leads',
      chart: { type: 'bar', categories: ['Espresso', 'Latte'], series: [{ name: 'Week', values: [235, 322] }], unit: '₽' },
    });
    const bars = [...container.querySelectorAll('rect')].filter((r) => r.getAttribute('rx') === '2');
    expect(bars).toHaveLength(2);
    expect(getByText('322 ₽')).toBeTruthy();
    expect(container.textContent).toContain('Latte leads');
    // One series: no legend.
    expect(container.querySelectorAll('rect[width="16"]')).toHaveLength(0);
  });

  it('draws a pie with a slice per category and their shares', () => {
    const { container, getByText } = stage({
      layout: 'chart', title: 'Share',
      chart: { type: 'pie', categories: ['A', 'B'], series: [{ name: 'Share', values: [3, 1] }] },
    });
    expect(container.querySelectorAll('path')).toHaveLength(2);
    expect(getByText('75%')).toBeTruthy();
    expect(getByText('25%')).toBeTruthy();
  });

  it('stacks bars and areas on the series below them', () => {
    const { container } = stage({
      layout: 'chart', title: 'Stacked',
      chart: { type: 'area', stacked: true, categories: ['a', 'b'], series: [{ name: 'x', values: [1, 2] }, { name: 'y', values: [3, 4] }] },
    });
    expect(container.querySelectorAll('path')).toHaveLength(2);
  });

  it('shows a placeholder for a chart slide without data', () => {
    const { getByText } = stage({ layout: 'chart', title: 'Empty', chart: { categories: [], series: [] } });
    expect(getByText('📊')).toBeTruthy();
  });
});

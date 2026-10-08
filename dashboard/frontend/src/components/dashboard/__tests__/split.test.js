import { describe, it, expect } from 'vitest';
import { bestSplit } from '../split';

describe('bestSplit', () => {
  it('pairs the cards so both columns hold about as much', () => {
    // runs, costs, local models, endpoint, services, as measured at 1440 px
    expect(bestSplit([335, 424, 377, 168, 222])).toEqual([true, true, false, false, false]);
  });

  it('moves cards across when one grows', () => {
    // the endpoint card with calls in it: chart and top models
    const left = bestSplit([335, 424, 377, 380, 222]);
    expect(left[0]).toBe(true);
    expect(left.filter(Boolean).length).toBeGreaterThan(0);
    expect(left.filter((v) => !v).length).toBeGreaterThan(0);
  });

  it('keeps a single card in the left column', () => {
    expect(bestSplit([100])).toEqual([true]);
  });
});

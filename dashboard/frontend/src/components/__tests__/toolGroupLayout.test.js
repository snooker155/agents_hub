import { describe, it, expect } from 'vitest';
import { packToolGroups } from '../agent/toolGroupLayout';

const g = (category, span) => ({ category, span });
// Two groups fit side by side, three do not (the geometry of the Tools tab).
const fits = (items) => items.length <= 2;
const names = (out) => out.map((x) => x.category);

describe('packToolGroups', () => {
  it('puts groups that add up to a row together, whole rows next, the rest last', () => {
    const out = packToolGroups(
      [g('web', 2), g('calc', 1), g('exec', 2), g('tasks', 4), g('coord', 1), g('docs', 2), g('runs', 3)],
      { cols: 4, fits },
    );
    // 3 + 1, 2 + 2, then the four-wide group, then the 2 and the 1 left.
    expect(names(out)).toEqual(['runs', 'calc', 'web', 'exec', 'tasks', 'docs', 'coord']);
    expect(out.find((x) => x.category === 'tasks').full).toBe(true);
    expect(out.find((x) => x.category === 'web').full).toBeUndefined();
  });

  it('uses three small groups for a row when three fit', () => {
    const out = packToolGroups([g('a', 2), g('b', 1), g('c', 1)], { cols: 4, fits: () => true });
    expect(names(out)).toEqual(['a', 'b', 'c']);
  });

  it('keeps every group exactly once', () => {
    const groups = [g('a', 3), g('b', 3), g('c', 2), g('d', 1), g('e', 4), g('f', 1)];
    const out = packToolGroups(groups, { cols: 4, fits });
    expect(names(out).sort()).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
  });
});

/**
 * The order of the tool groups on the Tools tab, chosen so as many rows as
 * possible are whole.
 *
 * Each group comes with `span`, the number of cards it is wide (at most
 * `cols`). `fits(items)` says whether those groups fit side by side in one
 * row (it knows the card width and the gaps). The rows come out as:
 *
 * 1. rows of smaller groups that add up to exactly `cols` cards: the widest
 *    group left goes first, and the first set of others that completes it
 *    and fits joins it;
 * 2. the groups as wide as a whole row, each alone, marked `full`;
 * 3. whatever could not make a whole row, first fit, widest first.
 *
 * Within each part the groups keep their given order where the widths allow.
 */
export function packToolGroups(groups, { cols, fits }) {
  const total = (items) => items.reduce((sum, g) => sum + g.span, 0);
  const whole = groups.filter((g) => g.span >= cols).map((g) => [{ ...g, full: true }]);
  // Widest first; the sort is stable, so equal widths keep their order.
  let pool = groups.filter((g) => g.span < cols).sort((a, b) => b.span - a.span);

  const complete = (row, rest) => {
    const used = total(row);
    if (used === cols) return row;
    for (let i = 0; i < rest.length; i += 1) {
      const next = [...row, rest[i]];
      if (total(next) <= cols && fits(next)) {
        const found = complete(next, rest.slice(i + 1));
        if (found) return found;
      }
    }
    return null;
  };

  const exact = [];
  const leftover = [];
  while (pool.length) {
    const [first, ...others] = pool;
    const row = complete([first], others);
    if (row) {
      exact.push(row);
      pool = others.filter((g) => !row.includes(g));
    } else {
      leftover.push(first);
      pool = others;
    }
  }

  const partial = [];
  leftover.forEach((group) => {
    const row = partial.find((r) => total([...r, group]) <= cols && fits([...r, group]));
    if (row) row.push(group);
    else partial.push([group]);
  });

  return [...exact, ...whole, ...partial].flat();
}

export default packToolGroups;

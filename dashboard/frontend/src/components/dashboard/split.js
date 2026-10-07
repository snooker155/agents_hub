export const GAP = 24; // gap-6, between cards in a column

/** Which items go left: of every split that keeps each column's order, the
 *  one whose column heights differ least; ties keep the earlier split. */
export function bestSplit(heights) {
  const n = heights.length;
  let best = null;
  for (let mask = 1; mask < (1 << n) - 1; mask += 1) {
    if (!(mask & 1)) continue; // the first item always opens the left column
    let left = 0; let right = 0; let nl = 0; let nr = 0;
    heights.forEach((h, i) => {
      if (mask & (1 << i)) { left += h; nl += 1; } else { right += h; nr += 1; }
    });
    const diff = Math.abs((left + GAP * (nl - 1)) - (right + GAP * (nr - 1)));
    if (best === null || diff < best.diff) best = { mask, diff };
  }
  return best ? heights.map((_, i) => Boolean(best.mask & (1 << i))) : heights.map(() => true);
}

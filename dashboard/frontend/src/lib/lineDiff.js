/**
 * A minimal LCS line diff: readable for the short texts a memory item or a
 * skill version holds, without pulling in a diff library. Rows are
 * `{ type: 'same' | 'added' | 'removed', text }` in reading order.
 */
export function lineDiff(beforeText, afterText) {
  const before = (beforeText || '').split('\n');
  const after = (afterText || '').split('\n');
  const n = before.length;
  const m = after.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = before[i] === after[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) {
      rows.push({ type: 'same', text: before[i] });
      i += 1; j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: 'removed', text: before[i] });
      i += 1;
    } else {
      rows.push({ type: 'added', text: after[j] });
      j += 1;
    }
  }
  while (i < n) { rows.push({ type: 'removed', text: before[i] }); i += 1; }
  while (j < m) { rows.push({ type: 'added', text: after[j] }); j += 1; }
  return rows;
}

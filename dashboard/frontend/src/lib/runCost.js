// One run often costs a fraction of a cent, so small sums keep four decimals
// instead of rounding them to "< $0.01".
export const fmtRunUsd = (n) => {
  const v = Number(n) || 0;
  if (v > 0 && v < 0.0001) return '< $0.0001';
  return `$${v.toFixed(v >= 1 ? 2 : 4)}`;
};

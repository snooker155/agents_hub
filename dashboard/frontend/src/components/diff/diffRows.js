// Parsing and word marks for a unified diff, drawn by UnifiedDiff.jsx.

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/;

/** Rows of `{type: 'hunk'|'ctx'|'del'|'add', text, old, new}`. */
export function parseUnifiedDiff(diff) {
  const rows = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  for (const line of String(diff || '').replace(/\n$/, '').split('\n')) {
    const hunk = line.match(HUNK_RE);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      inHunk = true;
      rows.push({ type: 'hunk', text: line, context: hunk[3] || '', old: oldNo, new: newNo });
      continue;
    }
    if (!inHunk) continue; // file headers before the first hunk
    if (line.startsWith('\\')) continue; // "\ No newline at end of file"
    if (line.startsWith('+')) rows.push({ type: 'add', text: line.slice(1), new: newNo++ });
    else if (line.startsWith('-')) rows.push({ type: 'del', text: line.slice(1), old: oldNo++ });
    else rows.push({ type: 'ctx', text: line.slice(1), old: oldNo++, new: newNo++ });
  }
  return rows;
}

const TOKEN_RE = /\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu;
const MAX_TOKENS = 600;

/** Mark the tokens of `a` and `b` that are not in their longest common
 * subsequence. Returns `[aParts, bParts]`, each a list of `{text, changed}`,
 * or null when the lines are too long to compare cheaply. */
function wordDiff(a, b) {
  const x = a.match(TOKEN_RE) || [];
  const y = b.match(TOKEN_RE) || [];
  if (x.length * y.length > MAX_TOKENS * MAX_TOKENS / 4) return null;
  const n = x.length;
  const m = y.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] = x[i] === y[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const aParts = [];
  const bParts = [];
  const push = (parts, text, changed) => {
    const last = parts[parts.length - 1];
    if (last && last.changed === changed) last.text += text;
    else parts.push({ text, changed });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { push(aParts, x[i], false); push(bParts, y[j], false); i += 1; j += 1; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { push(aParts, x[i], true); i += 1; }
    else { push(bParts, y[j], true); j += 1; }
  }
  while (i < n) { push(aParts, x[i], true); i += 1; }
  while (j < m) { push(bParts, y[j], true); j += 1; }
  // Whitespace alone between two changed words reads as part of the change.
  [aParts, bParts].forEach((parts) => parts.forEach((p, k) => {
    if (!p.changed && /^\s+$/.test(p.text) && parts[k - 1]?.changed && parts[k + 1]?.changed) p.changed = true;
  }));
  // A line rewritten almost entirely gains nothing from word marks.
  const share = (parts) => {
    const all = parts.reduce((s, p) => s + p.text.length, 0) || 1;
    return parts.filter((p) => !p.changed).reduce((s, p) => s + p.text.trim().length, 0) / all;
  };
  if (share(aParts) < 0.2 && share(bParts) < 0.2) return null;
  return [aParts, bParts];
}

/** Attach word level parts to paired del/add runs. */
export function withWordMarks(rows) {
  const out = rows.map((r) => ({ ...r }));
  let k = 0;
  while (k < out.length) {
    if (out[k].type !== 'del') { k += 1; continue; }
    const delStart = k;
    while (k < out.length && out[k].type === 'del') k += 1;
    const addStart = k;
    while (k < out.length && out[k].type === 'add') k += 1;
    const pairs = Math.min(addStart - delStart, k - addStart);
    for (let p = 0; p < pairs; p += 1) {
      const marks = wordDiff(out[delStart + p].text, out[addStart + p].text);
      if (marks) {
        out[delStart + p].parts = marks[0];
        out[addStart + p].parts = marks[1];
      }
    }
  }
  return out;
}

export function diffStats(rows) {
  return rows.reduce((s, r) => {
    if (r.type === 'add') s.added += 1;
    if (r.type === 'del') s.removed += 1;
    return s;
  }, { added: 0, removed: 0 });
}

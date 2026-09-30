// Markdown for slides: the same block subset and the same parsing rules as the
// .pptx export (views/slides_pptx.py parse_markdown / inline_runs), so a slide
// reads the same in the browser, in the PDF and in PowerPoint.

const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|!?\[[^\]]*\]\([^)]+\))/g;
const BULLET = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

export function inlineRuns(text) {
  const out = [];
  const src = text || '';
  let pos = 0;
  for (const m of src.matchAll(INLINE)) {
    if (m.index > pos) out.push([src.slice(pos, m.index), {}]);
    const tok = m[0];
    if (tok.startsWith('**')) out.push([tok.slice(2, -2), { bold: true }]);
    else if (tok.startsWith('`')) out.push([tok.slice(1, -1), { code: true }]);
    else if (tok.startsWith('![')) {
      const alt = tok.slice(2, tok.indexOf(']'));
      if (alt) out.push([alt, { italic: true }]);
    } else if (tok.startsWith('[')) {
      out.push([tok.slice(1, tok.indexOf(']')), { link: tok.slice(tok.indexOf('(') + 1, -1) }]);
    } else out.push([tok.slice(1, -1), { italic: true }]);
    pos = m.index + tok.length;
  }
  if (pos < src.length) out.push([src.slice(pos), {}]);
  return out;
}

const cells = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

export function parseMarkdown(md) {
  const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let para = [];
  let counters = {};
  const flush = () => {
    if (para.length) blocks.push({ kind: 'para', text: para.map((s) => s.trim()).join(' ') });
    para = [];
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const stripped = line.trim();
    if (stripped.startsWith('```')) {
      flush();
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) { body.push(lines[i]); i += 1; }
      blocks.push({ kind: 'code', text: body.join('\n') });
      i += 1;
      continue;
    }
    if (!stripped) { flush(); counters = {}; i += 1; continue; }
    let m = stripped.match(/^(#{1,6})\s+(.*)$/);
    if (m) { flush(); blocks.push({ kind: 'heading', level: m[1].length, text: m[2].trim() }); i += 1; continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(stripped)) { flush(); blocks.push({ kind: 'hr' }); i += 1; continue; }
    if (stripped.startsWith('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flush();
      const rows = [cells(stripped)];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(cells(lines[i].trim())); i += 1; }
      blocks.push({ kind: 'table', rows });
      continue;
    }
    m = line.match(BULLET);
    if (m) {
      flush();
      const level = Math.min(Math.floor(m[1].replace(/\t/g, '  ').length / 2), 3);
      const ordered = /\d/.test(m[2][0]);
      counters[level] = ordered ? (counters[level] || 0) + 1 : 0;
      Object.keys(counters).forEach((k) => { if (Number(k) > level) delete counters[k]; });
      blocks.push({ kind: 'bullet', level, ordered, number: counters[level] || 0, text: m[3].trim() });
      i += 1;
      continue;
    }
    if (stripped.startsWith('>')) { flush(); blocks.push({ kind: 'quote', text: stripped.replace(/^>+/, '').trim() }); i += 1; continue; }
    para.push(stripped);
    i += 1;
  }
  flush();
  return blocks;
}

export const MONO = "'Courier New', ui-monospace, Menlo, monospace";

export const plainText = (md) => inlineRuns(md).map(([c]) => c).join('');

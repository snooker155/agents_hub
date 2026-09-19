import React from 'react';

// ---------------------------------------------------------------------------
// Lightweight, dependency-free Markdown renderer.
//
// Supports the common subset used in workspace/task files: headings, bold /
// italic / inline code, fenced code blocks, ordered & unordered lists,
// blockquotes, horizontal rules, links and GitHub-style tables. It is not a
// full CommonMark implementation — anything it doesn't recognise is rendered
// as plain text so content is never lost.
// ---------------------------------------------------------------------------

// Render inline spans: **bold**, *italic*, `code`, [text](url).
function renderInline(text, keyPrefix = '') {
  const nodes = [];
  // Split on the supported inline tokens while keeping the delimiters.
  const regex = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;
  let match;
  let idx = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}i${idx++}`;
    if (token.startsWith('**')) {
      nodes.push(<strong key={key} className="font-semibold">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*')) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="bg-gray-100 text-indigo-700 px-1 py-0.5 rounded text-[0.85em]">
          {token.slice(1, -1)}
        </code>
      );
    } else {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (linkMatch) {
        nodes.push(
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noreferrer noopener"
            className="text-indigo-600 hover:text-indigo-800 underline"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        nodes.push(token);
      }
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

const HEADING_CLASSES = {
  1: 'text-xl font-bold text-gray-900 mt-4 mb-2',
  2: 'text-lg font-bold text-gray-900 mt-4 mb-2',
  3: 'text-base font-semibold text-gray-900 mt-3 mb-1.5',
  4: 'text-sm font-semibold text-gray-800 mt-3 mb-1',
  5: 'text-sm font-semibold text-gray-700 mt-2 mb-1',
  6: 'text-xs font-semibold text-gray-600 mt-2 mb-1',
};

function MarkdownRenderer({ content = '', className = '' }) {
  const lines = String(content).split('\n');
  const blocks = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Fenced code block
    if (trimmed.startsWith('```')) {
      const lang = trimmed.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      blocks.push(
        <div key={key++} className="my-3">
          {lang && (
            <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg border-b border-gray-700">
              {lang}
            </div>
          )}
          <pre className={`bg-gray-900 text-gray-100 text-xs p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}>
            {codeLines.join('\n')}
          </pre>
        </div>
      );
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push(<hr key={key++} className="my-4 border-gray-200" />);
      i++;
      continue;
    }

    // Heading
    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${level}`;
      blocks.push(
        <Tag key={key++} className={HEADING_CLASSES[level]}>
          {renderInline(heading[2], `h${key}`)}
        </Tag>
      );
      i++;
      continue;
    }

    // Table (GitHub-style)
    const nextTrimmed = i + 1 < lines.length ? lines[i + 1].trim() : '';
    if (
      trimmed.startsWith('|') && trimmed.endsWith('|') &&
      nextTrimmed.startsWith('|') && /^\|[\s\-:|]+\|$/.test(nextTrimmed)
    ) {
      const headers = trimmed.split('|').slice(1, -1).map((h) => h.trim());
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
        rows.push(lines[i].trim().split('|').slice(1, -1).map((c) => c.trim()));
        i++;
      }
      blocks.push(
        <div key={key++} className="my-3 overflow-x-auto">
          <table className="min-w-full text-xs border border-gray-200 rounded-lg overflow-hidden">
            <thead className="bg-gray-50">
              <tr>
                {headers.map((h, j) => (
                  <th key={j} className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200">{renderInline(h, `th${j}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j} className={j % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {row.map((cell, k) => (
                    <td key={k} className="px-3 py-2 text-gray-700 border-b border-gray-100">{renderInline(cell, `td${j}-${k}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // Blockquote
    if (trimmed.startsWith('>')) {
      const quoteLines = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      blocks.push(
        <blockquote key={key++} className="border-l-4 border-gray-200 pl-3 my-3 text-gray-600 italic">
          {renderInline(quoteLines.join(' '), `bq${key}`)}
        </blockquote>
      );
      continue;
    }

    // Unordered list
    if (/^[-*+]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
        i++;
      }
      blocks.push(
        <ul key={key++} className="list-disc pl-6 my-2 space-y-1 text-sm text-gray-700">
          {items.map((it, j) => <li key={j}>{renderInline(it, `ul${key}-${j}`)}</li>)}
        </ul>
      );
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      blocks.push(
        <ol key={key++} className="list-decimal pl-6 my-2 space-y-1 text-sm text-gray-700">
          {items.map((it, j) => <li key={j}>{renderInline(it, `ol${key}-${j}`)}</li>)}
        </ol>
      );
      continue;
    }

    // Blank line
    if (trimmed === '') {
      i++;
      continue;
    }

    // Paragraph — gather consecutive non-blank, non-special lines
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('```') &&
      !/^(#{1,6})\s+/.test(lines[i].trim()) &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !lines[i].trim().startsWith('>') &&
      !lines[i].trim().startsWith('|')
    ) {
      paraLines.push(lines[i].trim());
      i++;
    }
    if (paraLines.length) {
      blocks.push(
        <p key={key++} className="text-sm text-gray-700 leading-relaxed my-2">
          {renderInline(paraLines.join(' '), `p${key}`)}
        </p>
      );
    }
  }

  return <div className={className}>{blocks}</div>;
}

export default MarkdownRenderer;

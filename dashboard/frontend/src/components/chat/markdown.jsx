/**
 * The chat's own markdown-ish renderer: no dependency, because what a message
 * needs is code blocks, tables, bold and links, and a markdown library is a
 * hundred kilobytes for that.
 */
import { trimBubbleText } from '../../lib/chatText';
import { CopyButton } from './CopyButton';
import React from 'react';

// ---------------------------------------------------------------------------
// A structured-response block (<<<ui>>>{json}<<<end>>>). The backend strips it
// from the final response, but during streaming the raw tokens still carry it;
// strip it from any fallback content so the markers never show in the bubble.
const UI_BLOCK_RE = /<<<\s*ui\s*>>>[\s\S]*?<<<\s*\/?\s*end\s*>>>/gi;

const stripUiBlock = (s) => (s || '').replace(UI_BLOCK_RE, '').trim();

// Renders plain text segments with inline code and markdown table support
function renderTableAwareText(text, keyPrefix = '') {
  const lines = text.split('\n');
  const elements = [];
  let i = 0;
  let plainBuf = [];
  let elemIdx = 0;

  const renderInlineText = (str, key) => {
    const inlineParts = str.split(/(`[^`]+`)/g);
    return (
      <span key={key}>
        {inlineParts.map((ip, j) => {
          if (ip.startsWith('`') && ip.endsWith('`') && ip.length > 2) {
            return (
              <code key={j} className="bg-gray-100 text-indigo-700 px-1 py-0.5 rounded text-xs">
                {ip.slice(1, -1)}
              </code>
            );
          }
          return ip.split('\n').map((line, k, arr) => (
            <React.Fragment key={`${j}-${k}`}>
              {line}
              {k < arr.length - 1 && <br />}
            </React.Fragment>
          ));
        })}
      </span>
    );
  };

  const flushPlain = () => {
    if (!plainBuf.length) return;
    const content = plainBuf.join('\n');
    plainBuf = [];
    elements.push(renderInlineText(content, `${keyPrefix}p${elemIdx++}`));
  };

  while (i < lines.length) {
    const trimmed = lines[i].trim();
    const nextTrimmed = i + 1 < lines.length ? lines[i + 1].trim() : '';

    if (
      trimmed.startsWith('|') &&
      trimmed.endsWith('|') &&
      nextTrimmed.startsWith('|') &&
      /^\|[\s\-:|]+\|$/.test(nextTrimmed)
    ) {
      flushPlain();
      const headers = trimmed.split('|').slice(1, -1).map((h) => h.trim());
      i += 2; // skip header and separator rows
      const rows = [];
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (rowLine.startsWith('|') && rowLine.endsWith('|')) {
          rows.push(rowLine.split('|').slice(1, -1).map((c) => c.trim()));
          i++;
        } else {
          break;
        }
      }
      elements.push(
        <div key={`${keyPrefix}t${elemIdx++}`} className="my-3 overflow-x-auto">
          <table className="min-w-full text-xs border border-gray-200 rounded-lg overflow-hidden">
            <thead className="bg-gray-50">
              <tr>
                {headers.map((h, j) => (
                  <th key={j} className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j} className={j % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {row.map((cell, k) => (
                    <td key={k} className="px-3 py-2 text-gray-700 border-b border-gray-100">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    } else {
      plainBuf.push(lines[i]);
      i++;
    }
  }
  flushPlain();
  return elements;
}

function renderContent(text) {
  // Model output almost always ends with a newline; the plain-text branch below
  // turns it into a <br /> and the bubble grows an empty row under the last
  // sentence. Drop it here, at the point of display, so the stored transcript
  // keeps what the model actually produced.
  const parts = trimBubbleText(text).split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const lang = newline > 0 ? inner.slice(0, newline).trim() : '';
      const code = newline > 0 ? inner.slice(newline + 1) : inner;
      return (
        <div key={i} className="relative my-3">
          {lang && (
            <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg border-b border-gray-700">
              {lang}
            </div>
          )}
          <pre
            className={`bg-gray-900 text-gray-100 text-xs p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}
          >
            <CopyButton text={code} />
            {code}
          </pre>
        </div>
      );
    }
    // For non-code parts: handle tables and inline code
    return <React.Fragment key={i}>{renderTableAwareText(part, `${i}-`)}</React.Fragment>;
  });
}

export { stripUiBlock, renderTableAwareText, renderContent };

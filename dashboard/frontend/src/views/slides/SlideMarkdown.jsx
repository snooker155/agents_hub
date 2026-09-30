import React from 'react';
import { MONO, inlineRuns, parseMarkdown } from './markdown';

// Slide markdown, styled inline: the PDF export copies the slide's HTML into a
// bare print window where the app's CSS classes do not exist. Sizes are in em,
// so a FitText box around it scales everything by changing one font size.

const SAFE_LINK = /^(https?:|mailto:)/i;

function Inline({ text, accent, codeBg }) {
  return inlineRuns(text).map(([chunk, fmt], i) => {
    if (fmt.code) {
      return (
        <code key={i} style={{ fontFamily: MONO, fontSize: '0.88em', background: codeBg, padding: '0.05em 0.3em', borderRadius: 4 }}>
          {chunk}
        </code>
      );
    }
    if (fmt.link) {
      return SAFE_LINK.test(fmt.link)
        ? <a key={i} href={fmt.link} target="_blank" rel="noreferrer" style={{ color: accent, textDecoration: 'underline' }}>{chunk}</a>
        : <span key={i} style={{ color: accent }}>{chunk}</span>;
    }
    const style = {};
    if (fmt.bold) style.fontWeight = 700;
    if (fmt.italic) style.fontStyle = 'italic';
    return <span key={i} style={style}>{chunk}</span>;
  });
}

/**
 * Markdown blocks coloured from the slide theme `t`, sized in em of the
 * surrounding font size, or of `size` px when given.
 */
export default function SlideMarkdown({ md, size, t, accent, color, muted }) {
  const text = color || t.text;
  const soft = muted || t.muted;
  const blocks = parseMarkdown(md);
  const inline = (s, a = accent) => <Inline text={s} accent={a} codeBg={t.surface} />;
  return (
    <div style={{ fontSize: size || undefined, color: text, lineHeight: 1.3 }}>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'heading':
            return (
              <div key={i} style={{ fontSize: `${b.level <= 2 ? 1.3 : 1.12}em`, fontWeight: 700, lineHeight: 1.2, margin: '0 0 0.45em' }}>
                {inline(b.text)}
              </div>
            );
          case 'bullet': {
            const marker = b.ordered ? `${b.number}.` : (b.level % 2 === 0 ? '•' : '–');
            return (
              <div key={i} style={{ position: 'relative', paddingLeft: `${1.15 * (b.level + 1)}em`, marginBottom: '0.34em', lineHeight: 1.34 }}>
                <span style={{ position: 'absolute', left: `${1.15 * b.level}em`, color: accent, fontWeight: 700 }}>{marker}</span>
                {inline(b.text)}
              </div>
            );
          }
          case 'quote':
            return (
              <div key={i} style={{ borderLeft: `4px solid ${accent}`, paddingLeft: '0.8em', fontStyle: 'italic', color: soft, marginBottom: '0.5em' }}>
                {inline(b.text)}
              </div>
            );
          case 'code':
            return (
              <div key={i} style={{ fontFamily: MONO, fontSize: '0.8em', whiteSpace: 'pre-wrap', background: t.surface, border: `1px solid ${t.border}`, borderRadius: 8, padding: '0.5em 0.75em', marginBottom: '0.75em', lineHeight: 1.35 }}>
                {b.text}
              </div>
            );
          case 'table':
            return (
              <table key={i} style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8em', margin: '0.5em 0 0.6em' }}>
                <tbody>
                  {b.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((c, ci) => {
                        const Cell = ri === 0 ? 'th' : 'td';
                        return (
                          <Cell
                            key={ci}
                            style={{
                              textAlign: 'left',
                              padding: '0.35em 0.55em',
                              border: `1px solid ${t.border}`,
                              background: ri === 0 ? accent : (ri % 2 ? t.surface : t.bg),
                              color: ri === 0 ? t.heroText : text,
                              fontWeight: ri === 0 ? 700 : 400,
                            }}
                          >
                            {inline(c, ri === 0 ? t.heroText : accent)}
                          </Cell>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          case 'hr':
            return <div key={i} style={{ borderTop: `1px solid ${t.border}`, margin: '0.4em 0' }} />;
          default:
            return <div key={i} style={{ marginBottom: '0.5em' }}>{inline(b.text)}</div>;
        }
      })}
    </div>
  );
}

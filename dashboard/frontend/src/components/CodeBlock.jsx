/**
 * A block of code as every read-only surface shows it: a header naming the
 * language, with Copy (and whatever `actions` the caller adds) on the right,
 * over a highlighted body.
 *
 * Highlighting is synchronous once the language's grammar has loaded, so a
 * block that is still streaming recolours on the same render as the token
 * that grew it; the first block of a language shows plain text for the moment
 * its grammar takes to load. An unknown language stays plain text.
 */
import { useEffect, useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useI18n } from '../i18n';
import {
  highlightCodeSync, isKnownLanguage, isLanguageLoaded, normalizeLanguage, preloadLanguage,
} from '../lib/highlight';

// A trailing newline ends the last line, it does not start another.
const countLines = (text) => (text ? text.replace(/\n$/, '').split('\n').length : 1);

export function CodeCopyButton({ text }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {});
  };
  return (
    <button type="button" onClick={copy} className="hl-code-block__action" title={t('chat.copy')}>
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      <span>{copied ? t('chat.codeBlock.copied') : t('chat.copy')}</span>
    </button>
  );
}

// `lineNumbers`: a gutter of line numbers down the left, for a snippet read
// as a file (a code view) rather than as part of a reply.
export default function CodeBlock({
  language = '', code = '', actions = null, meta = null, bodyClassName = '', lineNumbers = false,
}) {
  const { t } = useI18n();
  const lang = normalizeLanguage(language);
  const known = Boolean(lang) && isKnownLanguage(lang);
  // Set when a grammar this block asked for arrives, to highlight what is
  // already on screen; a grammar loaded earlier is used on the first render.
  const [arrived, setArrived] = useState(null);
  const loaded = known && (arrived === lang || isLanguageLoaded(lang));
  useEffect(() => {
    if (!known || isLanguageLoaded(lang)) return undefined;
    let cancelled = false;
    preloadLanguage(lang).then(() => { if (!cancelled) setArrived(lang); });
    return () => { cancelled = true; };
  }, [known, lang]);
  const nodes = useMemo(
    () => (loaded ? highlightCodeSync(code, lang) : null),
    [loaded, code, lang],
  );

  return (
    <div className="hl-code-block" data-testid="code-block">
      <div className="hl-code-block__header">
        <span className="hl-code-block__lang">
          {lang || t('chat.codeBlock.plain')}
          {meta}
        </span>
        <span className="hl-code-block__actions">
          {actions}
          <CodeCopyButton text={code} />
        </span>
      </div>
      <pre className={`hl-code-block__body ${lineNumbers ? 'hl-code-block__body--numbered' : ''} ${bodyClassName}`}>
        {lineNumbers && (
          <span className="hl-code-block__gutter" aria-hidden="true" data-testid="code-line-numbers">
            {Array.from({ length: countLines(code) }, (_, i) => i + 1).join('\n')}
          </span>
        )}
        <code>{nodes || code}</code>
      </pre>
    </div>
  );
}

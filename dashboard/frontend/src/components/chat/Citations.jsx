/**
 * The sources a reply rests on (common/citation_sink.py): the numbered
 * passages a memory search showed the model, listed under the reply, and the
 * `[n]` markers in the reply text turned into links to them.
 *
 * A citation is `{n, pool_id, file_id, filename, chunk_idx, heading_path,
 * snippet, score, workspace_file_id, layer}`. A passage of a workspace file
 * links to that file (`/files?file=<id>`); anything else (a file uploaded
 * straight into a pool, a note) links to the memory page.
 *
 * Clicking `[n]` scrolls its source into view and highlights it for a moment.
 * The marker and the list find each other by DOM id (`<anchor>-cite-<n>`) and
 * a window event, so the reply text and the list stay separate pieces.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BookOpen, FileText, StickyNote } from 'lucide-react';
import { useI18n } from '../../i18n';
import ChatMarkdown from './ChatMarkdown';
import { CITATION_FOCUS_EVENT, citationDomId, citationHref, focusCitation } from './citationLinks';

/** The reply text as the chat renders it, with its [n] markers linked. */
export function CitedText({ content, citations, anchor, streaming = false }) {
  return (
    <ChatMarkdown
      content={content}
      streaming={streaming}
      citations={citations}
      onCite={(n) => focusCitation(anchor, n)}
    />
  );
}

function sourceLabel(c) {
  const heading = Array.isArray(c.heading_path) && c.heading_path.length ? c.heading_path.join(' › ') : '';
  return heading ? `${c.filename} › ${heading}` : (c.filename || c.file_id || '');
}

/** The numbered list of sources, under a reply or on a run page. */
export default function Citations({ citations, anchor, className = '' }) {
  const { t } = useI18n();
  const [focused, setFocused] = useState(null);

  useEffect(() => {
    const onFocus = (e) => {
      if (e.detail?.anchor !== anchor) return;
      setFocused(e.detail.n);
    };
    window.addEventListener(CITATION_FOCUS_EVENT, onFocus);
    return () => window.removeEventListener(CITATION_FOCUS_EVENT, onFocus);
  }, [anchor]);

  useEffect(() => {
    if (focused == null) return undefined;
    const id = setTimeout(() => setFocused(null), 1600);
    return () => clearTimeout(id);
  }, [focused]);

  if (!citations || !citations.length) return null;
  return (
    <div className={`mt-2 pt-2 border-t border-gray-100 ${className}`} data-testid="citations">
      <div className="flex items-center gap-1.5 mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
        <BookOpen className="w-3 h-3" />
        {t('files.citations.sources')}
      </div>
      <ol className="space-y-1">
        {citations.map((c) => {
          const isNote = c.layer === 'note';
          const Icon = isNote ? StickyNote : FileText;
          return (
            <li
              key={c.n}
              id={citationDomId(anchor, c.n)}
              className={`flex items-start gap-2 rounded-md px-1.5 py-1 transition-colors ${
                focused === c.n ? 'bg-amber-50 ring-1 ring-amber-300' : ''
              }`}
            >
              <span className="mt-0.5 inline-flex items-center justify-center min-w-[1.25rem] rounded bg-indigo-50 px-1 text-[11px] font-semibold text-indigo-700">
                {c.n}
              </span>
              <span className="min-w-0 flex-1">
                <Link
                  to={citationHref(c)}
                  title={c.workspace_file_id ? t('files.citations.openFile') : t('files.citations.openPool')}
                  className="inline-flex items-center gap-1 max-w-full text-xs font-medium text-gray-700 hover:text-indigo-700 hover:underline"
                >
                  <Icon className="w-3 h-3 shrink-0 text-gray-400" />
                  <span className="truncate">
                    {isNote ? `${t('files.citations.note')}: ${c.filename}` : sourceLabel(c)}
                  </span>
                </Link>
                {c.snippet && (
                  <span className="block text-[11px] leading-snug text-gray-500 line-clamp-2">{c.snippet}</span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

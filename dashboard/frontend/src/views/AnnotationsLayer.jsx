import React, { useMemo } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { MessageCircleQuestion } from 'lucide-react';
import { useI18n } from '../i18n';

// Interpretation layer — renders a view's `annotations` (labels, callouts,
// equations) as a floating panel over any view. Equation annotations bind KaTeX
// symbols to spec paths, so the shown values update live as controls or the
// simulation change (design §15). Every annotation carries an implicit
// "explain" action that sends it to the agent via onExplain.

function getAt(doc, path) {
  if (!path) return undefined;
  let node = doc;
  for (const seg of String(path).split('.')) {
    if (node && typeof node === 'object' && seg in node) node = node[seg];
    else return undefined;
  }
  return node;
}

function asList(coll) {
  if (Array.isArray(coll)) return coll;
  if (coll && typeof coll === 'object') return Object.entries(coll).map(([id, a]) => ({ id, ...(a || {}) }));
  return [];
}

function Equation({ ann, view }) {
  const html = useMemo(() => {
    try { return katex.renderToString(ann.latex || '', { throwOnError: false }); } catch { return null; }
  }, [ann.latex]);
  const values = ann.values || {};
  return (
    <div>
      {html && <div dangerouslySetInnerHTML={{ __html: html }} />}
      {Object.keys(values).length > 0 && (
        <div className="mt-1 text-[11px] font-mono text-gray-500 space-x-2">
          {Object.entries(values).map(([sym, path]) => {
            const v = getAt(view, path);
            return <span key={sym}>{sym}={typeof v === 'number' ? v.toFixed(2) : String(v ?? '—')}</span>;
          })}
        </div>
      )}
    </div>
  );
}

export default function AnnotationsLayer({ view, onExplain }) {
  const { t } = useI18n();
  const annotations = asList(view?.annotations);
  if (!annotations.length) return null;
  return (
    <div className="absolute top-2 right-2 z-10 max-w-[260px] space-y-2">
      {annotations.map((ann) => (
        <div key={ann.id} className="group bg-white/95 dark:bg-gray-800/95 backdrop-blur border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 shadow-sm text-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-gray-800 dark:text-gray-200">
              {ann.type === 'equation'
                ? <Equation ann={ann} view={view} />
                : <>
                    {ann.title && <div className="font-medium">{ann.title}</div>}
                    <div className="text-xs text-gray-600 dark:text-gray-300">{ann.text}</div>
                  </>}
            </div>
            {onExplain && (
              <button onClick={() => onExplain(ann)} title={t('viewAnnotationsLayer.explainThis')}
                className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-indigo-600 flex-shrink-0">
                <MessageCircleQuestion className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

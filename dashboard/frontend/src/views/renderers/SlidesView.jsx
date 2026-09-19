import React, { useState, useMemo, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight, FileDown } from 'lucide-react';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import { printHtml } from '../printView';
import { useI18n } from '../../i18n';

// Slides renderer — a pager over an ordered deck (one slide at a time, prev/next
// + keyboard arrows), with a PDF export that lays every slide out as its own
// page. Slides come as a keyed map (live op shape) or a list.

function toSlides(coll) {
  const list = Array.isArray(coll)
    ? coll.map((s, i) => ({ id: s?.id ?? String(i), ...s }))
    : Object.entries(coll || {}).map(([id, s]) => ({ id, ...(s || {}) }));
  return list.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

export default function SlidesView({ view }) {
  const { t } = useI18n();
  const slides = useMemo(() => toSlides(view?.spec?.slides), [view]);
  const [idx, setIdx] = useState(0);
  const n = slides.length;
  const cur = Math.min(idx, Math.max(0, n - 1));

  const go = useCallback((d) => setIdx((i) => Math.max(0, Math.min(n - 1, i + d))), [n]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'ArrowRight') go(1); else if (e.key === 'ArrowLeft') go(-1); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go]);

  const exportPdf = () => {
    // Render each slide to static HTML for the print window.
    const html = slides.map((s) => {
      const bodyEl = document.getElementById(`slide-body-${s.id}`);
      return `<section class="slide"><h2>${escapeHtml(s.title || '')}</h2>${bodyEl ? bodyEl.innerHTML : ''}</section>`;
    }).join('\n');
    printHtml(view?.title || 'Slides', html);
  };

  if (!n) return <div className="text-sm text-gray-500 p-4">{t('viewSlidesView.emptyDeckTheAgentWill')}</div>;
  const slide = slides[cur];

  return (
    <div className="flex flex-col h-full">
      {/* offscreen: rendered bodies used by PDF export */}
      <div className="hidden">
        {slides.map((s) => <div key={s.id} id={`slide-body-${s.id}`}><MarkdownRenderer content={s.body || ''} /></div>)}
      </div>

      <div className="flex-1 min-h-[260px] flex flex-col justify-center px-8 py-6 bg-white dark:bg-gray-900">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">{slide.title}</h2>
        <div className="text-gray-800 dark:text-gray-200"><MarkdownRenderer content={slide.body || ''} /></div>
      </div>

      <div className="flex items-center justify-between px-3 py-2 border-t border-gray-200 dark:border-gray-700">
        <button onClick={() => go(-1)} disabled={cur === 0}
          className="p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="text-xs text-gray-500 tabular-nums">{cur + 1} / {n}</span>
        <div className="flex items-center gap-2">
          <button onClick={exportPdf} title={t('viewSlidesView.exportPdf')}
            className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            <FileDown className="w-3.5 h-3.5" /> {t('viewSlidesView.pdf')}
          </button>
          <button onClick={() => go(1)} disabled={cur === n - 1}
            className="p-1.5 rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

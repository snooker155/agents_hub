import React, { useRef } from 'react';
import { FileDown } from 'lucide-react';
import MarkdownRenderer from '../../components/MarkdownRenderer';
import { printHtml } from '../printView';
import { useI18n } from '../../i18n';

// Document renderer — a paginated, print-styled article from markdown, with a
// PDF export button (browser print → Save as PDF). The view's optional `css`
// is applied both on screen (scoped) and in the print output.

export default function DocumentView({ view }) {
  const { t } = useI18n();
  const spec = view?.spec || {};
  const ref = useRef(null);
  const title = spec.title || view?.title || 'Document';

  if (!spec.markdown) {
    return <div className="text-sm text-gray-500 p-4">{t('viewDocumentView.emptyDocumentTheAgentWill')}</div>;
  }

  const exportPdf = () => printHtml(title, ref.current?.innerHTML || '', spec.css);

  return (
    <div className="relative">
      <button onClick={exportPdf} title={t('viewDocumentView.exportPdf')}
        className="absolute top-2 right-2 z-10 inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700">
        <FileDown className="w-3.5 h-3.5" /> {t('viewDocumentView.pdf')}
      </button>
      {spec.css && <style>{spec.css}</style>}
      <div ref={ref} className="mx-auto max-w-3xl px-6 py-4 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
        {spec.title && <h1 className="text-2xl font-bold mb-4">{spec.title}</h1>}
        <MarkdownRenderer content={spec.markdown} />
      </div>
    </div>
  );
}

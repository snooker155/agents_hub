import React, { useMemo } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { useI18n } from '../../i18n';

// LaTeX renderer — KaTeX in display mode. Code-split so katex's CSS/JS load only
// when a `latex` view is shown.

export default function LatexView({ view }) {
  const { t } = useI18n();
  const src = view?.spec?.latex || '';
  const html = useMemo(() => {
    try {
      return katex.renderToString(src, { displayMode: true, throwOnError: false, errorColor: '#dc2626' });
    } catch {
      return null;
    }
  }, [src]);

  if (!src) return <div className="text-sm text-gray-500">{t('viewLatexView.noLatexSource')}</div>;
  if (html == null) return <pre className="text-sm text-red-600 whitespace-pre-wrap">{src}</pre>;
  return <div className="py-4 overflow-x-auto text-gray-900 dark:text-gray-100" dangerouslySetInnerHTML={{ __html: html }} />;
}

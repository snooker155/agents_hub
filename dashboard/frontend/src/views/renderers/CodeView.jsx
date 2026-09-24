import React, { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { CopyButton } from '../../components/chat/CopyButton';
import { isKnownLanguage } from '../../lib/highlight';
import { useI18n } from '../../i18n';

// Code view renderer (`kind: "code"`). Shown inside a ViewCard in a message
// bubble (so it must read well at bubble width) and, full-size, on /views/:id.
// The spec is {language, filename, body, dependencies: [], version, description}
// (dashboard contract, routes/views_code.py); nothing here mutates it — editing
// lives in the Code panel (components/chat/CodePanel.jsx), which posts a new
// version and lets the message bubble pick it up as a fresh view.
export default function CodeView({ view }) {
  const { t } = useI18n();
  const spec = view?.spec || {};
  const { language = '', filename = '', body = '', dependencies = [], version, description = '' } = spec;
  const [nodes, setNodes] = useState(null);

  useEffect(() => {
    let cancelled = false;
    if (!language || !isKnownLanguage(language)) { setNodes(null); return undefined; }
    import('../../lib/highlight').then(({ highlightCode }) => highlightCode(body, language))
      .then((result) => { if (!cancelled) setNodes(result); })
      .catch(() => { if (!cancelled) setNodes(null); });
    return () => { cancelled = true; };
  }, [language, body]);

  const download = () => {
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename || 'snippet.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
        <div className="min-w-0 flex items-center gap-2">
          <span className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
            {filename || t('chat.code.untitled')}
          </span>
          {language && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
              {language}
            </span>
          )}
          {version != null && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-300">
              {t('chat.code.versionShort', { version })}
            </span>
          )}
        </div>
        <button
          onClick={download}
          title={t('chat.code.download')}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          <Download className="w-3.5 h-3.5" /> {t('chat.code.download')}
        </button>
      </div>

      {description && (
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">{description}</p>
      )}

      <div className="hl-code-block">
        {language && <div className="hl-code-block__header">{language}</div>}
        <pre className="hl-code-block__body max-h-96 overflow-y-auto">
          <code>{nodes || body}</code>
        </pre>
        <CopyButton text={body} />
      </div>

      {dependencies.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {dependencies.map((dep) => (
            <span
              key={dep}
              className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300"
            >
              {dep}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

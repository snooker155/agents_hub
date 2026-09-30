import { Download } from 'lucide-react';
import CodeBlock from '../../components/CodeBlock';
import { useI18n } from '../../i18n';

// Code view renderer (`kind: "code"`). Shown inside a ViewCard in a message
// bubble (so it must read well at bubble width) and, full-size, on /views/:id.
// The spec is {language, filename, body, dependencies: [], version, description}
// (dashboard contract, routes/views_code.py); nothing here mutates it — editing
// lives in the Code panel (components/chat/CodePanel.jsx), which posts a new
// version and lets the message bubble pick it up as a fresh view.
//
// The card's own header already names the view, so the block's header is the
// one line here: language and version on the left, Download and Copy on the
// right, the way every other code block in the app is drawn.
export default function CodeView({ view }) {
  const { t } = useI18n();
  const spec = view?.spec || {};
  const { language = '', filename = '', body = '', dependencies = [], version, description = '' } = spec;
  const download = () => {
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename || 'snippet.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // `code-view`: inside a ViewCard the block is the card body, flush with its
  // edges and as tall as the row made the card (index.css, .view-card-body).
  return (
    <div className="code-view h-full flex flex-col">
      {description && (
        <p className="code-view__description text-xs text-gray-500 dark:text-gray-400 mb-2">{description}</p>
      )}

      <CodeBlock
        language={language}
        code={body}
        bodyClassName="max-h-96 overflow-y-auto"
        lineNumbers
        meta={version != null ? (
          <span className="ml-2 px-1.5 py-0.5 rounded bg-indigo-500 text-white normal-case" data-testid="code-view-version">
            {t('chat.code.versionShort', { version })}
          </span>
        ) : null}
        actions={(
          <button type="button" onClick={download} className="hl-code-block__action" title={t('chat.code.download')}>
            <Download className="w-3.5 h-3.5" />
            <span>{t('chat.code.download')}</span>
          </button>
        )}
      />

      {dependencies.length > 0 && (
        <div className="code-view__deps flex flex-wrap gap-1 mt-2">
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

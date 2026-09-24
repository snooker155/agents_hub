import { useState } from 'react';
import { ArrowLeft, ArrowRight, RotateCw, X, Hand, HandMetal } from 'lucide-react';
import { useI18n } from '../../i18n';

const iconButton = 'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-40';

/**
 * The strip above a browser viewport. Every control is optional: a handler
 * that is not passed hides its button, so the same toolbar serves the read
 * only view in a run's live output and the full Browser page.
 *
 * The address bar keeps what the person is typing until Enter, and follows the
 * page's address otherwise, so a poll landing mid-edit does not overwrite it.
 */
export default function BrowserToolbar({
  url = '', title = '', sessionId = '', disabled = false,
  onBack, onForward, onReload, onNavigate,
  controlling = false, onToggleControl, onClose, children,
}) {
  const { t } = useI18n();
  // null while the person is not editing: the field then shows the page's
  // address as it is now.
  const [draft, setDraft] = useState(null);
  const value = draft ?? url;

  const submit = (e) => {
    e.preventDefault();
    const next = value.trim();
    if (!next || !onNavigate) return;
    const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(next) ? next : `https://${next}`;
    setDraft(null);
    onNavigate(withScheme);
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 py-1.5">
      {onBack && (
        <button type="button" className={iconButton} onClick={onBack} disabled={disabled}
          title={t('browser.back')} aria-label={t('browser.back')}>
          <ArrowLeft className="h-4 w-4" />
        </button>
      )}
      {onForward && (
        <button type="button" className={iconButton} onClick={onForward} disabled={disabled}
          title={t('browser.forward')} aria-label={t('browser.forward')}>
          <ArrowRight className="h-4 w-4" />
        </button>
      )}
      {onReload && (
        <button type="button" className={iconButton} onClick={onReload} disabled={disabled}
          title={t('browser.reload')} aria-label={t('browser.reload')}>
          <RotateCw className="h-4 w-4" />
        </button>
      )}
      {onNavigate ? (
        <form onSubmit={submit} className="min-w-[12rem] flex-1">
          <input
            type="text"
            value={value}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => setDraft(null)}
            placeholder={t('browser.addressPlaceholder')}
            aria-label={t('browser.address')}
            className="w-full rounded-md border border-gray-200 bg-gray-50 px-2 py-1 font-mono text-xs text-gray-700 focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-indigo-400"
          />
        </form>
      ) : (
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-500" title={url}>{url}</span>
      )}
      {title && (
        <span className="hidden max-w-[16rem] truncate text-xs text-gray-600 md:inline" title={title}>{title}</span>
      )}
      {sessionId && (
        <span className="font-mono text-[10px] text-gray-400" title={sessionId}>{String(sessionId).slice(0, 8)}</span>
      )}
      {children}
      {onToggleControl && (
        <button
          type="button"
          onClick={onToggleControl}
          disabled={disabled}
          aria-pressed={controlling}
          className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium disabled:opacity-40 ${
            controlling
              ? 'border-indigo-600 bg-indigo-600 text-white hover:bg-indigo-700'
              : 'border-gray-200 bg-white text-gray-700 hover:border-indigo-300 hover:text-indigo-700'
          }`}
        >
          {controlling ? <HandMetal className="h-3.5 w-3.5" /> : <Hand className="h-3.5 w-3.5" />}
          {controlling ? t('browser.release') : t('browser.takeControl')}
        </button>
      )}
      {onClose && (
        <button type="button" className={iconButton} onClick={onClose}
          title={t('browser.close')} aria-label={t('browser.close')}>
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

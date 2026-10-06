import { useState } from 'react';
import { Download, Share, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { install, useInstallMode } from '../lib/pwa';

/**
 * Install the dashboard as an app (docs/pwa.md), at the foot of the menu.
 *
 * Shown only when there is something to do: Chrome, Edge and Android offer an
 * install dialog of their own, which the button opens; Safari on an iPhone has
 * no such dialog, so the button explains the two taps instead. Installed
 * already, or a browser that offers neither: nothing is drawn.
 */
export default function InstallAppButton({ compact = false }) {
  const { t } = useI18n();
  const mode = useInstallMode();
  const [hint, setHint] = useState(false);
  if (!mode) return null;

  const onClick = () => {
    if (mode === 'prompt') install();
    else setHint((v) => !v);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        title={compact ? t('layout.install.button') : undefined}
        className={`w-full flex items-center py-2.5 text-sm font-medium text-indigo-600 hover:bg-indigo-50 transition-colors ${
          compact ? 'justify-center px-2' : 'px-6 gap-3'
        }`}
      >
        <Download className="w-5 h-5 shrink-0" />
        {!compact && <span>{t('layout.install.button')}</span>}
      </button>
      {hint && (
        <div
          role="note"
          className="mx-3 mb-2 rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-xs leading-relaxed text-gray-700"
        >
          <div className="flex items-start gap-2">
            <Share className="w-4 h-4 mt-0.5 shrink-0 text-indigo-600" />
            <p className="flex-1">{t('layout.install.iosHint')}</p>
            <button
              type="button"
              onClick={() => setHint(false)}
              aria-label={t('common.close')}
              className="shrink-0 text-gray-400 hover:text-gray-600"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

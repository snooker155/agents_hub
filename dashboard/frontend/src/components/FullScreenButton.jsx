import { Maximize2, Minimize2 } from 'lucide-react';
import { useI18n } from '../i18n';

/** The button that puts a pane over the whole screen and back (lib/fullScreen.js). */
export default function FullScreenButton({ on, onToggle, className = '' }) {
  const { t } = useI18n();
  const label = on ? t('common.exitFullScreen') : t('common.fullScreen');
  return (
    <button type="button" onClick={onToggle} aria-pressed={on} title={label} aria-label={label}
      className={`inline-flex items-center p-1.5 text-gray-600 border border-gray-200 rounded-lg hover:bg-white ${className}`}>
      {on ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
    </button>
  );
}

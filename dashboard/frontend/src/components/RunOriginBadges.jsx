import { Globe } from 'lucide-react';

import { explainRunOrigin, isExternalRun, isPartialImport, otelImport } from './runOrigin';
import { useI18n } from '../i18n';

/** For the lists that show every kind of run side by side. */
export function ExternalRunBadge({ run, className = '' }) {
  const { t } = useI18n();
  if (!isExternalRun(run)) return null;
  const partial = isPartialImport(run);
  return (
    <span
      title={explainRunOrigin(run, t)}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${
        partial ? 'bg-amber-100 text-amber-700' : 'bg-cyan-100 text-cyan-700'} ${className}`}
    >
      <Globe className="w-3 h-3" />
      {t('components.runOrigin.external')}
    </span>
  );
}

/**
 * For a connection's own page, where every run is external and saying so on
 * each row would be noise. What is worth saying there is the narrower thing:
 * this one was imported after it had finished, so it was never watchable, and
 * a trace that lost its root may be missing its ends.
 */
export function ImportedMark({ run }) {
  const { t } = useI18n();
  if (!otelImport(run)) return null;
  const partial = isPartialImport(run);
  return (
    <span
      title={partial ? t('components.runOrigin.partialHint') : t('components.runOrigin.importedHint')}
      className={`ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
        partial ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-500'}`}
    >
      {partial ? t('components.runOrigin.partial') : t('components.runOrigin.imported')}
    </span>
  );
}

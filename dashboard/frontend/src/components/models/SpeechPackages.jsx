import { useMemo, useState } from 'react';
import { Download, Loader } from 'lucide-react';
import { humanBytes } from './jobs';
import { useI18n } from '../../i18n';

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none';
// Past this many, a filter field helps (a voice collection lists hundreds).
const FILTER_FROM = 12;

/**
 * The speech models a listed repo holds (GET /hf/files `packages`): pick one
 * and download all its files at once. `selected` and `onSelect` are owned by
 * the parent so a preset can choose the package before the list arrives.
 */
export default function SpeechPackages({ packages, selected, onSelect, onDownload, downloading }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('');
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? packages.filter((p) => p.name.toLowerCase().includes(q)) : packages;
  }, [packages, filter]);
  const current = packages.find((p) => p.name === selected);

  return (
    <div className="mt-2 space-y-1.5" data-testid="speech-packages">
      <p className="text-xs text-gray-500">{t('localModels.speech.packagesFound', { count: packages.length })}</p>
      <div className="flex items-center gap-2 flex-wrap">
        {packages.length > FILTER_FROM && (
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('localModels.speech.filterPlaceholder')}
            className={`${inputCls} w-40`}
          />
        )}
        <select
          value={selected}
          onChange={(e) => onSelect(e.target.value)}
          className={`${inputCls} min-w-64 max-w-full`}
          data-testid="speech-package-select"
        >
          {current && !shown.includes(current) && <option value={current.name}>{current.name}</option>}
          {shown.map((p) => (
            <option key={p.name} value={p.name} disabled={p.downloaded}>
              {p.name} · {t(`localModels.speech.kinds.${p.kind}`)}{p.language ? ` · ${p.language}` : ''} ({humanBytes(p.size_bytes)})
              {p.downloaded ? ` · ${t('localModels.speech.alreadyHere')}` : ''}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => onDownload(selected)}
          disabled={downloading || !current || current.downloaded}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50"
          data-testid="speech-package-download"
        >
          {downloading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
          {t('localModels.runtime.download')}
        </button>
      </div>
    </div>
  );
}

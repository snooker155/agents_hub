import { useState } from 'react';
import { Loader, Search, Download, Heart, ExternalLink, Lock, X } from 'lucide-react';
import { searchRuntimeHf } from '../../api/localModels';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';
import { FitBadge, HardwareLine } from './FitBadge';
import { fmtParams, fmtContext } from './fit';
import { humanBytes } from './jobs';

const PURPOSES = ['chat', 'code', 'embeddings', 'speech', 'transcription', 'image', 'any'];
const LICENSES = ['any', 'permissive', 'apache-2.0', 'mit', 'llama', 'gemma'];
const SORTS = ['downloads', 'likes', 'trending', 'updated'];

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none';
// The filters show no focus ring: Chrome counts a click on a <select> as
// keyboard focus too, so focus-visible would not spare the mouse either.
const filterCls = 'border border-gray-300 rounded-lg px-2 py-1 text-xs focus:outline-none';

function fmtCount(n) {
  if (!n) return '0';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

/**
 * Search Hugging Face for GGUF models when the repo is not known: by text,
 * purpose, license and order. Each result says what it is (parameters, MoE,
 * license, context) and what it takes here: the best quantization that fits
 * this machine with its expected speed, and the same for each common
 * quantization. `onPick(repo)` hands a repo to the download form, which
 * lists its real files with their own estimates. `onShown(bool)` tells it
 * whether the hardware line is on screen here, so it is not shown twice.
 */
export default function ModelSearch({ onPick, onShown, disabled }) {
  const { t } = useI18n();
  const toast = useToast();
  const [q, setQ] = useState('');
  const [purpose, setPurpose] = useState('chat');
  const [license, setLicense] = useState('any');
  const [sort, setSort] = useState('downloads');
  const [results, setResults] = useState(null);
  const [hardware, setHardware] = useState(null);
  const [searching, setSearching] = useState(false);

  const run = async (e) => {
    e?.preventDefault();
    setSearching(true);
    try {
      const { data } = await searchRuntimeHf({ q: q.trim(), purpose, license, sort });
      const found = data?.results || [];
      // Speech results carry no estimates: nothing for the hardware line to explain.
      const estimated = found.some((r) => r.estimates);
      setResults(found);
      setHardware(estimated ? data?.hardware || null : null);
      onShown?.(Boolean(estimated && data?.hardware));
    } catch (err) {
      // A runtime started from code older than the search answers 404.
      toast.error(t('localModels.modelSearch.failed'),
        err?.response?.status === 404 ? t('localModels.modelSearch.outdated') : errorDetail(err));
    } finally {
      setSearching(false);
    }
  };

  // Empties the field and drops the results; the filters stay as they are.
  const clear = () => {
    setQ('');
    setResults(null);
    setHardware(null);
    onShown?.(false);
  };

  const select = (value, set, options, key) => (
    <label className="flex items-center gap-1.5 text-xs text-gray-500">
      {t(`localModels.modelSearch.${key}`)}
      <select value={value} onChange={(e) => set(e.target.value)} className={filterCls} data-testid={`model-search-${key}`}>
        {options.map((o) => <option key={o} value={o}>{t(`localModels.modelSearch.${key}s.${o}`)}</option>)}
      </select>
    </label>
  );

  return (
    <div className="mb-3 space-y-2" data-testid="model-search">
      <form onSubmit={run} className="flex items-stretch gap-2 flex-wrap">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t('localModels.modelSearch.placeholder')}
          className={`${inputCls} flex-1 min-w-48 max-w-md`}
          data-testid="model-search-q"
        />
        <button
          type="submit"
          disabled={searching || disabled}
          className="flex items-center gap-1.5 px-3 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50"
          data-testid="model-search-submit"
        >
          {searching ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
          {t('localModels.modelSearch.search')}
        </button>
        <button
          type="button"
          onClick={clear}
          disabled={searching || (!q && !results)}
          className="flex items-center gap-1.5 px-3 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium disabled:opacity-50"
          data-testid="model-search-clear"
        >
          <X className="w-3.5 h-3.5" />
          {t('common.clear')}
        </button>
      </form>
      <div className="flex items-center gap-3 flex-wrap">
        {select(purpose, setPurpose, PURPOSES, 'purpose')}
        {select(license, setLicense, LICENSES, 'license')}
        {select(sort, setSort, SORTS, 'sort')}
      </div>

      {results && (
        <div className="space-y-2">
          <HardwareLine hw={hardware} />
          {results.length === 0 ? (
            <p className="text-sm text-gray-400 py-1">{t('localModels.modelSearch.noResults')}</p>
          ) : (
            // Edge to edge in the runtime card: -mx-3 undoes its p-3.
            <div className="-mx-3 border-y border-gray-300 divide-y divide-gray-100 max-h-[28rem] overflow-y-auto">
              {results.map((r) => (
                <div key={r.repo} className="px-4 py-2 flex items-start gap-3" data-testid={`model-result-${r.repo}`}>
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="font-mono text-sm text-gray-800 truncate">{r.repo}</span>
                      <a
                        href={`https://huggingface.co/${r.repo}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-400 hover:text-indigo-600 shrink-0"
                        title="Hugging Face"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                    <div className="flex items-center gap-x-2.5 gap-y-0.5 flex-wrap text-xs text-gray-500">
                      {r.engine ? (
                        // A speech model: its engine and packages, sized; no quantizations.
                        <>
                          <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium text-indigo-700 bg-indigo-50 border-indigo-200">
                            {t(`localModels.speech.kinds.${r.kind}`)}
                          </span>
                          <span className="font-medium text-gray-700">{t(`localModels.speech.engines.${r.engine}`, { defaultValue: r.engine })}</span>
                          <span>
                            {t('localModels.modelSearch.packages', { count: r.packages })}
                            {r.size_max ? `, ${humanBytes(r.size_min) === humanBytes(r.size_max) ? humanBytes(r.size_max) : t('localModels.modelSearch.sizeRange', { min: humanBytes(r.size_min), max: humanBytes(r.size_max) })}` : ''}
                          </span>
                        </>
                      ) : (
                        <span className="font-medium text-gray-700">{r.params ? fmtParams(r.params) : t('localModels.modelSearch.noParams')}</span>
                      )}
                      {r.moe && <span className="rounded bg-indigo-50 text-indigo-700 px-1">{t('localModels.modelSearch.moe')}</span>}
                      {r.license && <span>{r.license}</span>}
                      {r.context_length && <span>{t('localModels.modelSearch.context', { n: fmtContext(r.context_length) })}</span>}
                      <span className="inline-flex items-center gap-0.5"><Download className="w-3 h-3" />{fmtCount(r.downloads)}</span>
                      <span className="inline-flex items-center gap-0.5"><Heart className="w-3 h-3" />{fmtCount(r.likes)}</span>
                      {r.gated && <span className="inline-flex items-center gap-0.5 text-amber-700"><Lock className="w-3 h-3" />{t('localModels.modelSearch.gated')}</span>}
                    </div>
                    {r.estimates && (
                      <div className="flex items-center gap-1 flex-wrap">
                        {r.estimates.map((e) => (
                          <FitBadge key={e.quant} fit={e} label={e.quant} compact />
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-1.5 shrink-0">
                    {r.best && (
                      <span className="text-right" title={t('localModels.modelSearch.best')}>
                        <FitBadge fit={r.best} label={r.best.quant} testId={`model-best-${r.repo}`} />
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => onPick(r.repo)}
                      disabled={disabled}
                      className="px-3 py-1 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-100 text-xs font-medium disabled:opacity-50"
                      data-testid={`model-pick-${r.repo}`}
                    >
                      {t('localModels.modelSearch.pick')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

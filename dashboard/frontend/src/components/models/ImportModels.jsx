import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Import, Loader } from 'lucide-react';
import {
  getRuntimeOllamaModels, importOllamaModel, getRuntimeLmStudioModels, importLmStudioModel,
} from '../../api/localModels';
import { humanBytes } from './jobs';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

// The apps whose downloaded models the runtime can take over, in the order
// they are shown.
const SOURCES = [
  { id: 'ollama', list: getRuntimeOllamaModels, take: importOllamaModel },
  { id: 'lmstudio', list: getRuntimeLmStudioModels, take: importLmStudioModel },
];

/**
 * Models Ollama and LM Studio already downloaded on the runtime's machine,
 * taken over without downloading them again: hard links to their files
 * (instant, no extra disk), or a copy as a job when the folders are on
 * different disks. Neither app has to be running. A source without a folder
 * there is left out, and the card is hidden when neither has one.
 */
export default function ImportModels({ refreshKey, onImported, onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const [data, setData] = useState({});
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState('');

  // Only reads; the callers store the answer, so the effect sets no state
  // until it arrives.
  const fetchSources = useCallback(async () => {
    const next = {};
    await Promise.all(SOURCES.map(async (src) => {
      try {
        const { data: d } = await src.list();
        if (d?.found && (d.models || []).length) next[src.id] = d;
      } catch {
        // An older runtime without the route: nothing to offer from there.
      }
    }));
    return next;
  }, []);

  const load = useCallback(() => fetchSources().then(setData), [fetchSources]);

  useEffect(() => {
    let cancelled = false;
    fetchSources().then((next) => { if (!cancelled) setData(next); });
    return () => { cancelled = true; };
  }, [fetchSources, refreshKey]);

  const shown = SOURCES.filter((src) => data[src.id]);
  if (shown.length === 0) return null;
  const count = (id) => data[id].models.length;
  // What can still be imported: not yet here, and a file this runtime runs.
  const waiting = shown.reduce(
    (n, src) => n + data[src.id].models.filter((m) => !m.imported && m.compatible !== false).length, 0);

  const doImport = async (src, m) => {
    setBusy(`${src.id}:${m.name}`);
    try {
      const { data: r } = await src.take(m.name);
      if (r?.job_id) {
        toast.success(t('localModels.modelImport.copying', { name: m.name }));
        onJobStarted?.();
      } else {
        toast.success(t('localModels.modelImport.imported', { name: m.name, file: r?.file || m.file }));
        onImported?.();
      }
      load();
    } catch (e) {
      toast.error(t('localModels.modelImport.failed'), errorDetail(e));
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="model-import">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-2 px-4 py-3 text-left bg-gray-50 hover:bg-gray-100 transition-colors focus:outline-none border-gray-100 ${open ? 'border-b' : ''}`}
        data-testid="model-import-toggle"
      >
        {open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
        <span className="text-sm font-semibold text-gray-800 shrink-0">{t('localModels.modelImport.title')}</span>
        <span className="text-xs text-gray-400">
          {shown.map((src) => t(`localModels.modelImport.sourceCount.${src.id}`, { count: count(src.id) })).join(', ')}
          {' · '}
          {t('localModels.modelImport.waiting', { count: waiting })}
        </span>
      </button>
      {open && (
        <div className="p-3 space-y-4">
          {shown.map((src) => (
            <div key={src.id} className="space-y-2" data-testid={`${src.id}-import`}>
              <div>
                <p className="text-xs font-semibold text-gray-600">{t(`localModels.modelImport.sources.${src.id}`)}</p>
                <p className="text-xs text-gray-500">{t(`localModels.modelImport.hints.${src.id}`, { dir: data[src.id].dir })}</p>
              </div>
              <div className="overflow-x-auto border border-gray-100 rounded-lg">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('localModels.modelImport.name')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.modelImport.family')}</th>
                      {src.id === 'lmstudio'
                        ? <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.format')}</th>
                        : <th className="px-2 py-1.5 font-medium">{t('localModels.modelImport.parameters')}</th>}
                      <th className="px-2 py-1.5 font-medium">{t('localModels.modelImport.quantization')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.size')}</th>
                      <th className="px-2 py-1.5 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {data[src.id].models.map((m) => (
                      <tr key={m.name} className="border-t border-gray-50 align-top" data-testid={`${src.id}-model-${m.name}`}>
                        <td className="px-2 py-1.5">
                          <span className="font-mono text-gray-700 break-all">{m.name}</span>
                          {m.note && <p className="text-[11px] text-amber-700 mt-0.5">{m.note}</p>}
                        </td>
                        <td className="px-2 py-1.5 text-gray-500">{m.family || ''}</td>
                        <td className="px-2 py-1.5 text-gray-500">
                          {src.id === 'lmstudio' ? (m.format || '').toUpperCase() : (m.parameter_size || '')}
                        </td>
                        <td className="px-2 py-1.5 text-gray-500">{m.quantization || ''}</td>
                        <td className="px-2 py-1.5 text-gray-500 tabular-nums whitespace-nowrap">{humanBytes(m.size_bytes)}</td>
                        <td className="px-2 py-1.5 text-right">
                          {m.imported ? (
                            <span className="text-xs text-green-700" title={m.file}>{t('localModels.modelImport.alreadyImported')}</span>
                          ) : m.compatible === false ? (
                            <span className="text-xs text-gray-400" title={m.note || ''}>{t(`localModels.modelImport.only.${src.id}`)}</span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => doImport(src, m)}
                              disabled={!!busy}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium disabled:opacity-50"
                              data-testid={`${src.id}-import-${m.name}`}
                            >
                              {busy === `${src.id}:${m.name}` ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Import className="w-3.5 h-3.5" />}
                              {t('localModels.modelImport.import')}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

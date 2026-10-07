import { useState } from 'react';
import { Check, Download, Loader } from 'lucide-react';
import { installRuntimeEngine } from '../../api/localModels';
import { ENGINES } from './speechPresets';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

/**
 * Which engines the runtime can run, with an Install button for a missing
 * one: llama.cpp's release build for chat models, a pip install into the
 * runtime's own Python for a speech engine; either is a job on the shared
 * list. `engines` is the runtime's { llama: bool, whisper: bool, ... }; a
 * runtime older than these sends none, and then this row is not shown.
 */
export default function SpeechEngines({ engines, jobs, onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const [starting, setStarting] = useState('');
  if (!engines) return null;

  const installing = (id) => (jobs || []).some((j) => j.kind === 'engine_install'
    && (j.meta?.engine === id) && (j.status === 'queued' || j.status === 'running'));

  const install = async (id) => {
    setStarting(id);
    try {
      await installRuntimeEngine(id);
      toast.success(t('localModels.speech.installStarted', { engine: t(`localModels.speech.engines.${id}`) }));
      onJobStarted?.();
    } catch (e) {
      toast.error(t('localModels.speech.installFailed'), errorDetail(e));
    } finally {
      setStarting('');
    }
  };

  return (
    <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap text-xs mb-3 pb-3 border-b border-gray-50" data-testid="speech-engines">
      <span className="font-semibold text-gray-500 uppercase tracking-wide">{t('localModels.speech.enginesTitle')}</span>
      {ENGINES.filter(({ id }) => id in engines).map(({ id, kind }) => (
        <span key={id} className="flex items-center gap-1.5 text-gray-600">
          <span>{t(`localModels.speech.engines.${id}`)}</span>
          <span className="text-gray-400">({t(`localModels.speech.kinds.${kind}`)})</span>
          {engines[id] ? (
            <Check className="w-3.5 h-3.5 text-green-600" aria-label={t('localModels.speech.installed')} />
          ) : installing(id) || starting === id ? (
            <Loader className="w-3.5 h-3.5 animate-spin text-indigo-500" />
          ) : (
            <button
              type="button"
              onClick={() => install(id)}
              data-testid={`install-engine-${id}`}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-100"
            >
              <Download className="w-3 h-3" />
              {t('localModels.speech.install')}
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

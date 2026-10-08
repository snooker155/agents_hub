import { useState } from 'react';
import { AlertCircle, Loader, Play, RotateCw, Square } from 'lucide-react';
import { startRuntime, stopRuntime, restartRuntime } from '../../api/localModels';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

const btn = 'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50 transition-colors';

/** Start, Restart and Stop for the runtime the hub runs itself. */
export function RuntimeButtons({ managed, onChanged }) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState('');
  if (!managed) return null;
  const state = managed.state;
  const working = state === 'preparing' || state === 'starting';

  const act = async (kind, call) => {
    if (kind === 'stop' && !window.confirm(t('localModels.runtime.confirmStop'))) return;
    setBusy(kind);
    try {
      await call();
      onChanged?.();
    } catch (e) {
      toast.error(t('localModels.runtime.controlFailed'), errorDetail(e));
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      {(state === 'stopped' || state === 'failed') && (
        <button type="button" onClick={() => act('start', startRuntime)} disabled={!!busy}
          className={`${btn} bg-indigo-600 text-white hover:bg-indigo-700`} data-testid="runtime-start">
          {busy === 'start' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          {t('localModels.runtime.start')}
        </button>
      )}
      {state === 'running' && (
        <button type="button" onClick={() => act('restart', restartRuntime)} disabled={!!busy}
          className={`${btn} border border-gray-200 text-gray-600 hover:bg-gray-100`} data-testid="runtime-restart">
          {busy === 'restart' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RotateCw className="w-3.5 h-3.5" />}
          {t('localModels.runtime.restart')}
        </button>
      )}
      {(state === 'running' || working) && (
        <button type="button" onClick={() => act('stop', stopRuntime)} disabled={!!busy}
          className={`${btn} border border-gray-200 text-gray-600 hover:bg-gray-100`} data-testid="runtime-stop">
          {busy === 'stop' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
          {t('localModels.runtime.stop')}
        </button>
      )}
    </div>
  );
}

/**
 * What the runtime the hub runs itself is doing when it is not simply
 * running: making its environment on the first start, starting, stopped by
 * someone, or failed (with the end of its log).
 */
export function RuntimeState({ managed, onChanged }) {
  const { t } = useI18n();
  if (!managed || managed.state === 'running') return null;
  const { state, message, log_tail: logTail } = managed;
  return (
    <div className="space-y-2" data-testid={`runtime-state-${state}`}>
      {(state === 'preparing' || state === 'starting') && (
        <div className="text-sm text-gray-700">
          <p className="flex items-center gap-2"><Loader className="w-4 h-4 animate-spin text-indigo-500" /> {t(`localModels.runtime.states.${state}`)}</p>
          {message && <p className="text-xs text-gray-500 mt-1 ml-6">{message}</p>}
          {state === 'preparing' && <p className="text-xs text-gray-400 mt-1 ml-6">{t('localModels.runtime.firstStartHint')}</p>}
        </div>
      )}
      {state === 'stopped' && (
        <div className="text-sm text-gray-600 flex items-center gap-3 flex-wrap">
          <span>{t('localModels.runtime.states.stopped')}</span>
          <RuntimeButtons managed={managed} onChanged={onChanged} />
        </div>
      )}
      {state === 'failed' && (
        <div className="text-sm space-y-2">
          <p className="flex items-start gap-1.5 text-red-700"><AlertCircle className="w-4 h-4 shrink-0 mt-0.5" /> {t('localModels.runtime.states.failed')}: {message}</p>
          <RuntimeButtons managed={managed} onChanged={onChanged} />
        </div>
      )}
      {logTail && (
        <details className="text-xs" open={state === 'failed'}>
          <summary className="cursor-pointer text-gray-500">{t('localModels.runtime.log')}</summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-gray-50 border border-gray-100 p-2 text-[11px] text-gray-600 whitespace-pre-wrap">{logTail}</pre>
        </details>
      )}
    </div>
  );
}

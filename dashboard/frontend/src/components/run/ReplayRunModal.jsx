import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader, Repeat, X, CheckCircle, AlertCircle } from 'lucide-react';

import { replayRun } from '../../api';
import { flattenModelCatalog, getModelsCatalog } from '../../api/agentLoop';
import { useI18n } from '../../i18n';
import { useStream } from '../stream';
import LiveRunStream from '../LiveRunStream';
import { DiffView } from '../chat/panels';

// A channel of its own per replay, so the panel shows this replay and nothing
// else. crypto.randomUUID is missing outside a secure context (plain http on a
// LAN address), hence the fallback.
const newChannel = () => `replay:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;

/**
 * Replay of a finished run (POST /api/runs/<id>/replay): the same
 * input sent to the agent again, optionally on another enabled model of the
 * catalog (the Models page decides which), with the two
 * outputs side by side and their diff. While it runs, its text, thinking and
 * tool calls stream into a LiveRunStream on a `replay:<id>` channel the server
 * subscribes this tab to before it starts. Opened from the run page's Replay
 * button; closing it drops the result, the replay itself stays a run of its
 * own behind the link.
 */
export default function ReplayRunModal({ run, onClose }) {
  const { t } = useI18n();
  const { clientId } = useStream();
  // `provider/model` of the picked catalog model, '' for the original's.
  const [model, setModel] = useState('');
  const [models, setModels] = useState([]);
  const [replaying, setReplaying] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  // The channel the current (or last) replay streamed on.
  const [channel, setChannel] = useState(null);

  useEffect(() => {
    let alive = true;
    getModelsCatalog()
      .then(({ data }) => { if (alive) setModels(flattenModelCatalog(data)); })
      .catch(() => { if (alive) setModels([]); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !replaying) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, replaying]);

  const handleReplay = async (e) => {
    e.preventDefault();
    if (replaying) return;
    setReplaying(true);
    setError('');
    setResult(null);
    const ch = newChannel();
    setChannel(ch);
    try {
      const picked = models.find((m) => m.id === model);
      const body = { client_id: clientId, channel: ch };
      if (picked) Object.assign(body, { provider: picked.provider, model: picked.model });
      const { data } = await replayRun(run.run_id, body);
      setResult(data);
    } catch (err) {
      setError(err.response?.data?.detail || t('messageDetails.replayFailed'));
    } finally {
      setReplaying(false);
    }
  };

  const original = [run?.provider, run?.model].filter(Boolean).join('/') || t('messageDetails.replaySameModel');
  const providers = [...new Set(models.map((m) => m.provider))];

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className={`bg-white rounded-xl shadow-2xl w-full ${channel ? 'max-w-4xl' : 'max-w-lg'} max-h-[90vh] flex flex-col`}>
        <div className="flex items-center justify-between p-5 border-b shrink-0">
          <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <Repeat className="w-4 h-4 text-indigo-600" />
            {t('messageDetails.replayThisRun')}
          </h2>
          <button type="button" onClick={onClose} disabled={replaying} className="text-gray-400 hover:text-gray-600 disabled:opacity-40">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleReplay} className="p-5 space-y-4 overflow-y-auto">
          <p className="text-sm text-gray-500">{t('messageDetails.reInvokesTheAgentOn')}</p>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">
              {t('messageDetails.replayModel')}
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none bg-white"
            >
              <option value="">{t('messageDetails.replayOriginalModel', { model: original })}</option>
              {providers.map((provider) => (
                <optgroup key={provider} label={provider}>
                  {models.filter((m) => m.provider === provider).map((m) => (
                    <option key={m.id} value={m.id}>{m.model}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-3 text-sm">{error}</div>}

          {channel && (
            <LiveRunStream key={channel} sessionId={channel} title={t('messageDetails.replayLive')}
                           className="border-gray-200" />
          )}

          {result && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 text-sm">
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${result.identical ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                  {result.identical ? <CheckCircle className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                  {result.identical ? t('messageDetails.outputIdentical') : t('messageDetails.outputDiffers')}
                </span>
                {!result.replay?.ok && <span className="text-xs text-red-600">{t('messageDetails.replayFailed')}: {result.replay?.error}</span>}
                <Link to={`/messages/${result.replay_run_id}`} onClick={onClose} className="text-xs text-indigo-600 hover:underline ml-auto">
                  {t('messageDetails.openReplayRun')}
                </Link>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">{t('messageDetails.original')} · {result.original?.model || '—'} · ${Number(result.original?.cost || 0).toFixed(4)}</div>
                  <pre className="text-xs whitespace-pre-wrap text-gray-800 max-h-64 overflow-auto">{result.original?.text || t('messageDetails.empty')}</pre>
                </div>
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                  <div className="text-xs text-gray-500 mb-1">{t('messageDetails.replay')} · {result.replay?.model || '—'} · ${Number(result.replay?.cost || 0).toFixed(4)} · {result.replay?.duration_ms || 0}ms</div>
                  <pre className="text-xs whitespace-pre-wrap text-gray-800 max-h-64 overflow-auto">{result.replay?.text || t('messageDetails.empty')}</pre>
                </div>
              </div>
              {result.diff && (
                <div className="border border-gray-200 rounded-lg overflow-hidden">
                  <div className="px-3 py-1.5 border-b border-gray-100 bg-gray-50 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                    {t('messageDetails.replayDiff')}
                  </div>
                  <div className="py-1 max-h-80 overflow-auto">
                    <DiffView diff={result.diff} wrap />
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={replaying}
              className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
            >
              {t('messageDetails.replayClose')}
            </button>
            <button
              type="submit"
              disabled={replaying}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {replaying ? <Loader className="w-4 h-4 animate-spin" /> : <Repeat className="w-4 h-4" />}
              {replaying ? t('common.running') : (result ? t('messageDetails.replayAgain') : t('messageDetails.runReplay'))}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

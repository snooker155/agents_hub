import React, { useEffect, useState } from 'react';
import { RotateCcw, Loader } from 'lucide-react';
import { getRunAgentVersion, rollbackRunAgent } from '../../api/agentVersions';
import { useI18n } from '../../i18n';

/**
 * The agent version a run built from (`run.agent_version`), with a
 * one-button rollback when it is not the live version any more. `compact`
 * is the form that sits beside the agent's name on the run page: a version
 * pill and a short status; the full form is RunLoopPanel's section line.
 * Renders nothing for a run that predates the version pin.
 */
export default function AgentVersion({ run, onChanged, compact = false }) {
  const { t } = useI18n();
  const runId = run?.run_id;
  const version = run?.agent_version;
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Another run (or another pinned version): forget what the last one showed.
  // Adjusted while rendering, not in an effect.
  const infoKey = `${runId}|${version}`;
  const [seenInfoKey, setSeenInfoKey] = useState(infoKey);
  if (infoKey !== seenInfoKey) {
    setSeenInfoKey(infoKey);
    setInfo(null);
    setError('');
  }

  useEffect(() => {
    if (!runId || version == null) return undefined;
    let cancelled = false;
    getRunAgentVersion(runId)
      .then(({ data }) => { if (!cancelled) setInfo(data); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [runId, version]);

  if (version == null) return null;

  const rollback = async () => {
    if (!runId) return;
    if (!window.confirm(t('runLoop.rollbackConfirm', { version }))) return;
    setBusy(true);
    setError('');
    try {
      await rollbackRunAgent(runId);
      const { data } = await getRunAgentVersion(runId);
      setInfo(data);
      if (onChanged) onChanged();
    } catch (err) {
      setError(err?.response?.data?.detail || t('runLoop.rollbackFailed'));
    } finally {
      setBusy(false);
    }
  };

  const label = () => {
    if (info?.is_current) return t('runLoop.versionLive', { version });
    if (info?.pinned) return t('runLoop.versionPinned', { version });
    return t('runLoop.versionRan', { version });
  };
  const status = () => {
    if (!info) return '';
    if (info.is_current) return t('runLoop.versionShort.live');
    if (info.pinned) return t('runLoop.versionShort.pinned');
    return t('runLoop.versionShort.older', { current: info.current_version });
  };

  const rollbackButton = info && !info.is_current && (
    <button
      type="button"
      onClick={rollback}
      disabled={busy}
      className={compact
        ? 'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-gray-300 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50'
        : 'inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50'}
    >
      {busy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
      {t('runLoop.rollback')}
    </button>
  );

  if (compact) {
    return (
      <span className="inline-flex items-center gap-2 flex-wrap" data-testid="agent-version">
        <span
          className="inline-flex items-center rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700"
          title={label()}
        >
          v{version}
        </span>
        {status() && <span className="text-xs text-gray-500">{status()}</span>}
        {rollbackButton}
        {error && <span className="text-xs text-red-600">{error}</span>}
      </span>
    );
  }

  return (
    <>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-sm text-gray-800">{label()}</span>
        {rollbackButton}
      </div>
      {error && <p className="text-sm text-red-600 mt-1">{error}</p>}
    </>
  );
}

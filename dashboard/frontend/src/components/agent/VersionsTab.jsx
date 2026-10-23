import { History, Loader, Lock, Unlock } from 'lucide-react';
import { fmtNodeDate } from './dateFormat';
import { useAgentPage } from './context';
import UnifiedDiff from '../diff/UnifiedDiff';
import { diffStats, parseUnifiedDiff } from '../diff/diffRows';

/**
 * The definition's version history: what changed in each stored version, the
 * diff against what is live now, and rolling one back (agents/versions.py,
 * docs/agents.md "Versions"). Its own tab, so the Config tab holds the
 * prompt files alone.
 */
export default function VersionsTab() {
  const {
    agentVersions, diffLoading, diffText, handlePin, handleRollback, handleToggleDiff, openDiffVersion,
    pinBusy, pinError, pinnedVersion,
    rollbackBusy, rollbackConfirmVersion, rollbackError, setRollbackConfirmVersion,
    setRollbackError, t, versionsError, versionsLoading,
  } = useAgentPage();
  return (
    <div className="bg-white p-6 shadow-md rounded-lg">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-lg font-bold flex items-center">
          <History className="w-5 h-5 mr-2 text-indigo-600" />
          {t('agentDetails.versions.title')}
        </h3>
        {versionsLoading && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
      </div>
      <p className="text-xs text-gray-500 mb-3">{t('agentDetails.versions.description')}</p>

      {/* The agent's own pin: every run builds from that version until it
          is cleared; a task or deployment pinned on its own still wins. */}
      {pinnedVersion != null ? (
        <div className="mb-3 flex items-start justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-amber-900 flex items-center gap-1.5">
              <Lock className="w-4 h-4" /> {t('agentDetails.versions.pinnedTitle', { n: pinnedVersion })}
            </p>
            <p className="text-xs text-amber-800 mt-1">{t('agentDetails.versions.pinnedHint')}</p>
          </div>
          <button
            type="button"
            disabled={pinBusy}
            onClick={() => handlePin(null)}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-amber-800 bg-white border border-amber-300 rounded hover:bg-amber-100 disabled:opacity-40 whitespace-nowrap flex-shrink-0"
          >
            {pinBusy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Unlock className="w-3.5 h-3.5" />}
            {t('agentDetails.versions.unpin')}
          </button>
        </div>
      ) : (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-2.5">
          <p className="text-xs text-gray-600">{t('agentDetails.versions.liveHint')}</p>
          <button
            type="button"
            disabled={pinBusy}
            onClick={() => handlePin('current')}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-gray-700 bg-white border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40 whitespace-nowrap flex-shrink-0"
          >
            {pinBusy ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Lock className="w-3.5 h-3.5" />}
            {t('agentDetails.versions.pinCurrent')}
          </button>
        </div>
      )}
      {pinError && <p className="text-xs text-red-600 mb-3">{pinError}</p>}

      {versionsError && <p className="text-xs text-red-600 mb-3">{versionsError}</p>}
      {!versionsLoading && agentVersions.length === 0 && !versionsError && (
        <p className="text-xs text-gray-500">{t('agentDetails.versions.empty')}</p>
      )}

      <div className="space-y-2">
        {[...agentVersions].reverse().map((v) => {
          const s = v.summary || {};
          const isDiffOpen = openDiffVersion === v.version;
          const diff = diffText[v.version];
          return (
            <div key={v.version} className="border border-gray-200 rounded-lg p-3">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                  <span className="text-sm font-semibold text-gray-800">
                    {t('agentDetails.versions.version', { n: v.version })}
                  </span>
                  {pinnedVersion === v.version && (
                    <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded align-middle">
                      <Lock className="w-2.5 h-2.5" /> {t('agentDetails.versions.pinnedBadge')}
                    </span>
                  )}
                  <span className="text-xs text-gray-400 ml-2">{fmtNodeDate(v.created_at)}</span>
                  {v.actor && (
                    <span className="text-xs text-gray-400 ml-2">
                      {t('agentDetails.versions.by', { actor: v.actor })}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {pinnedVersion !== v.version && (
                    <button
                      type="button"
                      disabled={pinBusy}
                      onClick={() => handlePin(v.version)}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-gray-700 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-40"
                    >
                      <Lock className="w-3 h-3" /> {t('agentDetails.versions.pin')}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleToggleDiff(v.version)}
                    className="px-2.5 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 rounded hover:bg-indigo-100"
                  >
                    {isDiffOpen ? t('agentDetails.versions.hideDiff') : t('agentDetails.versions.diff')}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setRollbackConfirmVersion(v.version); setRollbackError(''); }}
                    className="px-2.5 py-1 text-xs font-semibold text-amber-700 bg-amber-50 rounded hover:bg-amber-100"
                  >
                    {t('agentDetails.versions.rollback')}
                  </button>
                </div>
              </div>

              <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                {s.initial && (
                  <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                    {t('agentDetails.versions.initial')}
                  </span>
                )}
                {(s.tools_added || []).map(tid => (
                  <span key={`add-${tid}`} className="px-2 py-0.5 rounded-full bg-green-100 text-green-700">+{tid}</span>
                ))}
                {(s.tools_removed || []).map(tid => (
                  <span key={`rm-${tid}`} className="px-2 py-0.5 rounded-full bg-red-100 text-red-700">-{tid}</span>
                ))}
                {s.model_changed && (
                  <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                    {t('agentDetails.versions.modelChanged', { from: s.from_model || '—', to: s.to_model || '—' })}
                  </span>
                )}
                {(s.files_changed || []).map(f => (
                  <span key={`f-${f}`} className="px-2 py-0.5 rounded-full bg-purple-100 text-purple-700">{f}</span>
                ))}
              </div>

              {isDiffOpen && (
                <div className="mt-3">
                  {diffLoading === v.version ? (
                    <Loader className="w-4 h-4 animate-spin text-gray-400" />
                  ) : diff?.error ? (
                    <p className="text-xs text-red-600">{diff.error}</p>
                  ) : diff && Object.keys(diff).length === 0 ? (
                    <p className="text-xs text-gray-500">{t('agentDetails.versions.noDiff')}</p>
                  ) : (
                    <div className="space-y-3">
                      <p className="text-[11px] text-gray-500">{t('agentDetails.versions.against', { n: v.version })}</p>
                      {Object.entries(diff || {}).map(([part, text]) => {
                        const rows = parseUnifiedDiff(text);
                        const { added, removed } = diffStats(rows);
                        return (
                          <div key={part}>
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs font-semibold text-gray-700">
                                {t(`agentDetails.versions.parts.${part}`, { defaultValue: part })}
                              </span>
                              <span className="text-[11px] font-mono text-emerald-600">+{added}</span>
                              <span className="text-[11px] font-mono text-red-500">−{removed}</span>
                            </div>
                            <UnifiedDiff rows={rows} className="max-h-[28rem] overflow-y-auto" />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {rollbackConfirmVersion === v.version && (
                <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                  <p className="text-xs text-amber-800 mb-2">
                    {t('agentDetails.versions.rollbackConfirm', { n: v.version })}
                  </p>
                  {rollbackError && <p className="text-xs text-red-600 mb-2">{rollbackError}</p>}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={rollbackBusy}
                      onClick={() => handleRollback(v.version)}
                      className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-amber-600 rounded hover:bg-amber-700 disabled:opacity-40"
                    >
                      {rollbackBusy && <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />}
                      {t('agentDetails.versions.confirmRollback')}
                    </button>
                    <button
                      type="button"
                      disabled={rollbackBusy}
                      onClick={() => { setRollbackConfirmVersion(null); setRollbackError(''); }}
                      className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded hover:bg-gray-200"
                    >
                      {t('agentDetails.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

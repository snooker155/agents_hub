import { updateAgentEpisodicConfig, updateAgentMemoryWrite } from '../../api';
import { errorDetail } from '../toast';
import { MemoryPoolDetails } from './memoryPool';
import PersonalMemoryCard from './PersonalMemoryCard';
import { isPersonalPool, poolName } from '../memoryManager/helpers';
import { AlertCircle, Database, Loader, Save, Trash2, Wrench, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { InfoHint } from '../PageLayout';
import { useAgentPage } from './context';

/** What the agent remembers: its own store, the pools it shares, episodic recall. */
export default function MemoryTab() {
  const {
    addExtraPool, agent, connectedPool, episodicEffective, episodicMode, episodicSaving,
    fetchData, handleEraseMemory, handleUpdateMemory, id, isUpdatingMemory, loadingPool,
    markMemoryDraftDirty, memoryPools, memoryReadOnly, toggleReadOnly, memoryType,
    personalMemory, removePool,
    selectedWorkspace, setConnectedPool, setEpisodicEffective, setEpisodicMode,
    setEpisodicSaving, setMemoryType, setPrimaryPool,
    setPersonalMemory, setToolsSaving, sharedMemories, t, toast,
  } = useAgentPage();
  // The default workspace lists every workspace's pools: name the one a
  // personal pool belongs to.
  const onDefault = !selectedWorkspace || selectedWorkspace === 'default';
  // Both selectors keep a compact width instead of filling the card.
  const typeSelect = (
    <div className="w-full sm:w-72">
      <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.memoryType')}</label>
      <select
        value={memoryType}
        onChange={(e) => { setMemoryType(e.target.value); markMemoryDraftDirty(); if (e.target.value !== 'shared') setConnectedPool(null); }}
        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none"
      >
        <option value="none">{t('agentDetails.none')}</option>
        <option value="shared">{t('agentDetails.sharedMemoryPool')}</option>
      </select>
    </div>
  );
  return (
        <div className="space-y-5">
          {/* ── Configuration card ── */}
          <div className="bg-white rounded-xl border border-t-4 border-t-amber-500 border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
              <Database className="w-5 h-5 text-amber-500" /> {t('agentDetails.memoryConfiguration')}
            </h3>
            <p className="text-xs text-gray-400 mb-4">
              {t('agentDetails.memoryScope')} <span className="font-semibold text-gray-500">{selectedWorkspace || 'default'}</span> {t('agentDetails.memoryScopeAfter')}
            </p>
            <div className="space-y-4">
              <PersonalMemoryCard agentId={id} workspace={selectedWorkspace} t={t} toast={toast} onChange={setPersonalMemory} />
              {memoryType !== 'shared' && typeSelect}

              {memoryType === 'shared' && (() => {
                const poolNameById = Object.fromEntries(sharedMemories.map(m => [m.id, poolName(m, t, onDefault)]));
                const poolById = Object.fromEntries(sharedMemories.map(m => [m.id, m]));
                const poolDesc = (pid) => (poolById[pid]?.description || '').trim();
                const primary = memoryPools[0] || '';
                const extras = memoryPools.slice(1);
                // A personal pool is attached by the personal memory switch
                // above, for whoever runs the agent, so it is not offered as a
                // pool. One a record already holds still shows, to detach it.
                const pickable = sharedMemories.filter(m => !isPersonalPool(m) || m.id === primary);
                const unattached = sharedMemories.filter(m => !isPersonalPool(m) && !memoryPools.includes(m.id));
                return (
                  <>
                    {/* The type and the primary pool side by side, one row. */}
                    <div className="flex flex-wrap gap-4 items-start">
                    {typeSelect}
                    <div className="w-full sm:w-72">
                      <label className="flex items-center text-sm font-medium text-gray-700 mb-1">
                        {t('agentDetails.primaryPool')}
                        <InfoHint text={t('agentDetails.primaryPoolHint')} label={t('agentDetails.primaryPool')} className="ml-1.5 font-normal" />
                      </label>
                      {pickable.length > 0 ? (
                        <select
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value)}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none"
                        >
                          <option value="">{t('agentDetails.selectAMemoryPool')}</option>
                          {pickable.map(m => (
                            <option key={m.id} value={m.id}>
                              {poolName(m, t, onDefault)}  ({t('agentDetails.fileCount', { count: (m.files || []).length })})
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value.trim())}
                          placeholder={t('agentDetails.sharedMemoryPoolIdUuid')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none"
                        />
                      )}
                      {primary && (
                        <>
                          <label className="flex items-center gap-1.5 mt-1.5 text-xs text-gray-600 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={memoryReadOnly.has(primary)}
                              onChange={() => toggleReadOnly(primary)}
                              className="rounded border-gray-300 text-indigo-600"
                            />
                            {t('agentDetails.poolReadOnly')}
                          </label>
                        </>
                      )}
                    </div>
                    </div>

                    <div>
                      <label className="flex items-center text-sm font-medium text-gray-700 mb-1">
                        {t('agentDetails.additionalPools')}
                        <InfoHint text={t('agentDetails.additionalPoolsHint')} label={t('agentDetails.additionalPools')} className="ml-1.5 font-normal" />
                      </label>
                      {extras.length > 0 && (
                        <div className="space-y-1.5 mb-2">
                          {extras.map(pid => (
                            <div key={pid} className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5">
                              <span className="text-sm text-gray-700 truncate flex-1" title={pid}>
                                {poolNameById[pid] || pid}
                              </span>
                              {!poolDesc(pid) ? (
                                <span className="text-xs text-gray-400 shrink-0">{t('agentDetails.poolNoDescription')}</span>
                              ) : !memoryReadOnly.has(pid) && (
                                <span className="text-xs text-emerald-600 shrink-0" title={poolDesc(pid)}>{t('agentDetails.poolWrites')}</span>
                              )}
                              <label className="flex items-center gap-1 text-xs text-gray-500 shrink-0 cursor-pointer" title={t('agentDetails.poolReadOnlyHint')}>
                                <input
                                  type="checkbox"
                                  checked={memoryReadOnly.has(pid)}
                                  onChange={() => toggleReadOnly(pid)}
                                  className="rounded border-gray-300 text-indigo-600"
                                />
                                {t('agentDetails.readOnlyShort')}
                              </label>
                              <button type="button" onClick={() => setPrimaryPool(pid)}
                                className="text-xs text-indigo-600 hover:text-indigo-800 shrink-0">
                                {t('agentDetails.makePrimary')}
                              </button>
                              <button type="button" onClick={() => removePool(pid)}
                                className="text-gray-400 hover:text-red-500 shrink-0" title={t('agentDetails.detachPool')}>
                                <X className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      {unattached.length > 0 ? (
                        <select
                          value=""
                          onChange={(e) => addExtraPool(e.target.value)}
                          className="w-full sm:w-72 border border-dashed border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-500 focus:outline-none"
                        >
                          <option value="">{t('agentDetails.attachAnotherPool')}</option>
                          {unattached.map(m => (
                            <option key={m.id} value={m.id}>{poolName(m, t, onDefault)}</option>
                          ))}
                        </select>
                      ) : extras.length === 0 ? (
                        <p className="text-xs text-gray-400 italic">{t('agentDetails.noOtherPoolsAvailableTo')}</p>
                      ) : null}
                    </div>
                  </>
                );
              })()}


              {/* Small and on the right, like the Save button of the Tools tab. */}
              <div className="flex justify-end gap-2 pt-1">
                <button
                  onClick={handleEraseMemory}
                  disabled={isUpdatingMemory || agent.memory_type === 'none'}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-md hover:bg-red-100 disabled:opacity-50"
                >
                  <Trash2 className="w-3.5 h-3.5" /> {t('agentDetails.erase')}
                </button>
                <button
                  onClick={handleUpdateMemory}
                  disabled={isUpdatingMemory || (memoryType === 'shared' && memoryPools.length === 0)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
                >
                  {isUpdatingMemory ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                  {isUpdatingMemory ? t('common.saving') : t('common.save')}
                </button>
              </div>
            </div>
          </div>

          {/* ── Memory Tools ── shown whenever the agent has a pool to write to,
              its own or the user's personal memory. */}
          {((memoryType === 'shared' && memoryPools.length > 0) || personalMemory?.effective) && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-900 mb-3 flex items-center gap-2">
                <Wrench className="w-4 h-4 text-indigo-500" /> {t('agentDetails.memoryTools')}
              </h3>
              {/* The three switches in one row, two on a medium screen. */}
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {/* read_memory — always on */}
                <div className="p-3 border-2 border-green-200 bg-green-50 rounded-xl flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-gray-900">{t('agentDetails.readMemory2')}</div>
                    <div className="text-xs text-gray-500 mt-0.5">{t('agentDetails.readFilesNotesAndKey')}</div>
                  </div>
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold border bg-green-600 text-white border-green-600 shrink-0">
                    {t('agentDetails.alwaysOn')}
                  </span>
                </div>
                {/* Every memory write, for every pool (AgentSpec.memory_write). */}
                {(() => {
                  const enabled = agent.memory_write !== false;
                  return (
                    <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${enabled ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                      <div>
                        <div className="text-sm font-semibold text-gray-900">{t('agentDetails.writeMemory')}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{t('agentDetails.createOrUpdateFilesNotes')}</div>
                      </div>
                      <button
                        type="button"
                        aria-pressed={enabled}
                        aria-label={t('agentDetails.writeMemory')}
                        onClick={async () => {
                          setToolsSaving(true);
                          try { await updateAgentMemoryWrite(id, !enabled); await fetchData(); } catch (e) { toast.error(t('agentDetails.errors.updateTools'), errorDetail(e)); } finally { setToolsSaving(false); }
                        }}
                        className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? t('common.on') : t('common.off')}
                      </button>
                    </div>
                  );
                })()}
                {/* record_episode — episodic write tool, tri-state selector */}
                <div className={`p-3 border-2 rounded-xl flex flex-wrap items-center justify-between gap-3 transition-colors ${episodicEffective ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900 flex items-center">
                      {t('agentDetails.episodicWrite')}
                      <InfoHint
                        text={`${t('agentDetails.episodicHintBefore')} ${t('agentDetails.auto')} ${t('agentDetails.episodicHintAuto')}`}
                        label={t('agentDetails.episodicWrite')}
                        className="ml-1.5 font-normal"
                      />
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {t('agentDetails.currently')} <span className="font-semibold">{episodicEffective ? t('agentDetails.active') : t('agentDetails.inactive')}</span>.
                    </div>
                  </div>
                  <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden shrink-0">
                    {['auto', 'on', 'off'].map((m) => (
                      <button
                        key={m}
                        type="button"
                        disabled={episodicSaving}
                        onClick={async () => {
                          if (m === episodicMode) return;
                          const prev = episodicMode;
                          setEpisodicMode(m);
                          setEpisodicSaving(true);
                          try {
                            const val = m === 'on' ? true : m === 'off' ? false : null;
                            const { data } = await updateAgentEpisodicConfig(id, val);
                            const ev = data?.episodic_write_enabled;
                            setEpisodicMode(ev === true ? 'on' : ev === false ? 'off' : 'auto');
                            setEpisodicEffective(data?.effective !== false);
                            await fetchData();
                          } catch { setEpisodicMode(prev); }
                          finally { setEpisodicSaving(false); }
                        }}
                        className={`px-2.5 py-1 text-xs font-semibold capitalize ${
                          episodicMode === m ? 'bg-indigo-600 text-white' : 'bg-white text-gray-500 hover:bg-gray-50'
                        }`}
                      >
                        {t(`agentDetails.episodicModes.${m}`)}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ── Connected pool details ── */}
          {memoryType === 'shared' && (
            loadingPool ? (
              <div className="flex items-center justify-center h-32 bg-white rounded-xl border border-gray-200">
                <Loader className="w-5 h-5 animate-spin text-indigo-400 mr-2" />
                <span className="text-sm text-gray-500">{t('agentDetails.loadingPool')}</span>
              </div>
            ) : connectedPool ? (
              <div>
                {memoryPools.length > 1 && (
                  <p className="text-xs text-gray-400 mb-2">{t('agentDetails.showingThePrimaryPoolAdditional')} <Link to="/memory" className="text-indigo-600 hover:text-indigo-800">{t('agentDetails.sharedMemory')}</Link> {t('agentDetails.page')}</p>
                )}
                <MemoryPoolDetails pool={connectedPool} withWorkspace={onDefault} />
              </div>
            ) : memoryPools[0] ? (
              <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {t('agentDetails.poolNotFound')} <strong>{t('agentDetails.sharedMemory')}</strong>.
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center text-gray-400">
                <Database className="w-10 h-10 mb-3 opacity-20" />
                <p className="text-sm">{t('agentDetails.selectASharedMemoryPool')}</p>
              </div>
            )
          )}
        </div>
  );
}

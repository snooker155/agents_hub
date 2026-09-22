import { updateAgentEpisodicConfig, updateAgentTools } from '../../api';
import { errorDetail } from '../toast';
import { MemoryPoolDetails } from './memoryPool';
import { AlertCircle, Database, Loader, Save, Trash2, Wrench, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAgentPage } from './context';

/** What the agent remembers: its own store, the pools it shares, episodic recall. */
export default function MemoryTab() {
  const {
    addExtraPool, agent, connectedPool, episodicEffective, episodicMode, episodicSaving,
    fetchData, handleEraseMemory, handleUpdateMemory, id, isUpdatingMemory, loadingPool,
    markMemoryDraftDirty, memoryData, memoryPools, memoryType, removePool, selectedTools,
    selectedWorkspace, setConnectedPool, setEpisodicEffective, setEpisodicMode,
    setEpisodicSaving, setMemoryData, setMemoryType, setPrimaryPool, setSelectedTools,
    setToolsSaving, sharedMemories, t, toast,
  } = useAgentPage();
  return (
        <div className="space-y-5">
          {/* ── Configuration card ── */}
          <div className="bg-white rounded-xl border border-t-4 border-t-amber-500 border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
              <Database className="w-5 h-5 text-amber-500" /> {t('agentDetails.memoryConfiguration')}
            </h3>
            <p className="text-xs text-gray-400 mb-4">
              Memory is assigned per workspace — this configuration applies in <span className="font-semibold text-gray-500">{selectedWorkspace || 'default'}</span> {t('agentDetails.only')}
            </p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.memoryType')}</label>
                <select
                  value={memoryType}
                  onChange={(e) => { setMemoryType(e.target.value); markMemoryDraftDirty(); if (e.target.value !== 'shared') setConnectedPool(null); }}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="none">{t('agentDetails.none')}</option>
                  <option value="local">{t('agentDetails.localAgentSpecificOption')}</option>
                  <option value="shared">{t('agentDetails.sharedMemoryPool')}</option>
                </select>
              </div>

              {memoryType === 'shared' && (() => {
                const poolNameById = Object.fromEntries(sharedMemories.map(m => [m.id, m.name]));
                const primary = memoryPools[0] || '';
                const extras = memoryPools.slice(1);
                const unattached = sharedMemories.filter(m => !memoryPools.includes(m.id));
                return (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.primaryPool')}</label>
                      <p className="text-xs text-gray-400 mb-1.5">{t('agentDetails.primaryPoolHint')}</p>
                      {sharedMemories.length > 0 ? (
                        <select
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value)}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                          <option value="">{t('agentDetails.selectAMemoryPool')}</option>
                          {sharedMemories.map(m => (
                            <option key={m.id} value={m.id}>
                              {m.name}  ({t('agentDetails.fileCount', { count: (m.files || []).length })})
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={primary}
                          onChange={(e) => setPrimaryPool(e.target.value.trim())}
                          placeholder={t('agentDetails.sharedMemoryPoolIdUuid')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                      )}
                      {primary && (
                        <p className="text-xs text-gray-400 mt-1 truncate">ID: {primary}</p>
                      )}
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.additionalPools')}</label>
                      <p className="text-xs text-gray-400 mb-1.5">{t('agentDetails.additionalPoolsHint')}</p>
                      {extras.length > 0 && (
                        <div className="space-y-1.5 mb-2">
                          {extras.map(pid => (
                            <div key={pid} className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5">
                              <span className="text-sm text-gray-700 truncate flex-1" title={pid}>
                                {poolNameById[pid] || pid}
                              </span>
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
                          className="w-full border border-dashed border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                          <option value="">{t('agentDetails.attachAnotherPool')}</option>
                          {unattached.map(m => (
                            <option key={m.id} value={m.id}>{m.name}</option>
                          ))}
                        </select>
                      ) : extras.length === 0 ? (
                        <p className="text-xs text-gray-400 italic">{t('agentDetails.noOtherPoolsAvailableTo')}</p>
                      ) : null}
                    </div>
                  </>
                );
              })()}

              {memoryType === 'local' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.memoryContent')}</label>
                  <textarea
                    value={memoryData}
                    onChange={(e) => { setMemoryData(e.target.value); markMemoryDraftDirty(); }}
                    placeholder={t('agentDetails.enterMemoryContentOrConfiguration')}
                    rows={5}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              )}

              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleUpdateMemory}
                  disabled={isUpdatingMemory || (memoryType === 'shared' && memoryPools.length === 0)}
                  className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {isUpdatingMemory ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {isUpdatingMemory ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={handleEraseMemory}
                  disabled={isUpdatingMemory || agent.memory_type === 'none'}
                  className="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm font-medium hover:bg-red-100 flex items-center justify-center gap-2 disabled:opacity-50 border border-red-200"
                >
                  <Trash2 className="w-4 h-4" /> {t('agentDetails.erase')}
                </button>
              </div>
            </div>
          </div>

          {/* ── Memory Tools ── */}
          {memoryType === 'shared' && memoryPools.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-900 mb-3 flex items-center gap-2">
                <Wrench className="w-4 h-4 text-indigo-500" /> {t('agentDetails.memoryTools')}
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
                {/* write_memory — toggleable */}
                {(() => {
                  const enabled = selectedTools.includes('write_memory');
                  return (
                    <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${enabled ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                      <div>
                        <div className="text-sm font-semibold text-gray-900">{t('agentDetails.writeMemory')}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{t('agentDetails.createOrUpdateFilesNotes')}</div>
                      </div>
                      <button
                        type="button"
                        onClick={async () => {
                          const next = enabled
                            ? selectedTools.filter(t => t !== 'write_memory')
                            : [...selectedTools, 'write_memory'];
                          setSelectedTools(next);
                          setToolsSaving(true);
                          try { await updateAgentTools(id, { tools: next }); await fetchData(); } catch (e) { toast.error(t('agentDetails.errors.updateTools'), errorDetail(e)); } finally { setToolsSaving(false); }
                        }}
                        className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'On' : 'Off'}
                      </button>
                    </div>
                  );
                })()}
                {/* record_episode — episodic write tool, tri-state selector */}
                <div className={`p-3 border-2 rounded-xl flex items-center justify-between gap-3 transition-colors ${episodicEffective ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900">{t('agentDetails.episodicWrite')}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {t('agentDetails.episodicHintBefore')}
                      {' '}<span className="font-medium">{t('agentDetails.auto')}</span> {t('agentDetails.episodicHintAuto')}
                      {' '}{t('agentDetails.currently')} <span className="font-semibold">{episodicEffective ? t('agentDetails.active') : t('agentDetails.inactive')}</span>.
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
                <MemoryPoolDetails pool={connectedPool} />
              </div>
            ) : memoryPools[0] ? (
              <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                <AlertCircle className="w-4 h-4 shrink-0" />
                Pool not found. Check the ID or create a pool in <strong>{t('agentDetails.sharedMemory')}</strong>.
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

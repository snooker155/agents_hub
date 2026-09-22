import ImportedAgentPanel from '../ImportedAgentPanel';
import { Activity, Clock, Database, Globe, Lock, MessageSquare, Wrench } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAgentPage } from './context';

/** Everything this agent is, at a glance: readiness, description, sharing, and what it can reach. */
export default function OverviewTab() {
  const {
    activeTask, agent, availableTools, defaultChatMessage, defaultChatSaving, descDraft,
    descSaving, fetchData, handleSaveDescription, handleSaveWsCapacity,
    handleToggleDefaultChat, handleToggleShared, isDefaultChat, memoryData, memoryPools,
    memoryType, selectedTools, selectedWorkspace, setActiveTab, setDescDraft,
    setWsCapacityEdits, shared, sharingMessage, sharingSaving, t, wsCapacities,
    wsCapacityEdits, wsCapacitySaving,
  } = useAgentPage();
  return (
        <div className="space-y-6">

          {/* Imported agents lead with their readiness: for one that is not yet
              runnable, this panel is the whole remaining setup. */}
          <ImportedAgentPanel
            agent={agent}
            workspace={selectedWorkspace}
            onUpdated={fetchData}
          />

          {/* ── Agent Identity ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <Activity className="w-4 h-4 text-indigo-500" /> {t('agentDetails.agentIdentity')}
            </h3>
            <div className="mb-5">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.description')}</span>
                {descDraft === null && (
                  <button onClick={() => setDescDraft(agent.description || '')}
                    className="text-xs text-indigo-600 hover:text-indigo-800">{t('agentDetails.edit')}</button>
                )}
              </div>
              {descDraft === null ? (
                agent.description
                  ? <p className="text-base text-gray-600 leading-relaxed">{agent.description}</p>
                  : <p className="text-sm text-gray-400 italic">{t('agentDetails.noDescriptionYet')}</p>
              ) : (
                <div className="space-y-2">
                  <textarea value={descDraft} rows={3}
                    onChange={e => setDescDraft(e.target.value)}
                    placeholder={t('agentDetails.whatDoesThisAgentDo')}
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
                  />
                  <div className="flex items-center gap-2">
                    <button onClick={handleSaveDescription} disabled={descSaving}
                      className="text-xs text-white bg-indigo-600 hover:bg-indigo-700 px-2 py-1 rounded disabled:opacity-50">
                      {descSaving ? '...' : 'Save'}
                    </button>
                    <button onClick={() => setDescDraft(null)} disabled={descSaving}
                      className="text-xs text-gray-500 hover:text-gray-700">{t('agentDetails.cancel')}</button>
                  </div>
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">ID</span>
                <span className=" text-gray-700 text-xs break-all">{agent.id}</span>
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.domain')}</span>
                <span className=" text-gray-700 text-xs">{agent.domain || 'general'}</span>
              </div>
              {selectedWorkspace && selectedWorkspace !== 'default' && (
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('agentDetails.workspaceCapacity')}</span>
                  {(() => {
                    const effectiveCapacity = wsCapacities[selectedWorkspace] ?? 1;
                    const editVal = wsCapacityEdits[selectedWorkspace];
                    const isEditing = editVal !== undefined;
                    const isSaving = wsCapacitySaving === selectedWorkspace;
                    return isEditing ? (
                      <div className="flex items-center gap-2">
                        <input type="number" min="1" value={editVal}
                          onChange={e => setWsCapacityEdits(prev => ({ ...prev, [selectedWorkspace]: e.target.value }))}
                          className="w-24 border border-gray-300 rounded px-2 py-0.5 text-sm focus:ring-indigo-500 focus:border-indigo-500"
                        />
                        <button onClick={() => handleSaveWsCapacity(selectedWorkspace)} disabled={isSaving}
                          className="text-xs text-white bg-indigo-600 hover:bg-indigo-700 px-2 py-1 rounded disabled:opacity-50">
                          {isSaving ? '...' : 'Save'}
                        </button>
                        <button onClick={() => setWsCapacityEdits(prev => { const n = { ...prev }; delete n[selectedWorkspace]; return n; })}
                          className="text-xs text-gray-500 hover:text-gray-700">{t('agentDetails.cancel')}</button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{t('agentDetails.concurrentRuns', { count: effectiveCapacity })}</span>
                        <button onClick={() => setWsCapacityEdits(prev => ({ ...prev, [selectedWorkspace]: String(effectiveCapacity) }))}
                          className="text-xs text-indigo-600 hover:text-indigo-800">{t('agentDetails.edit')}</button>
                      </div>
                    );
                  })()}
                </div>
              )}
            </div>
          </div>

          {/* ── Marketplace publishing ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
              {shared ? <Globe className="w-4 h-4 text-indigo-500" /> : <Lock className="w-4 h-4 text-indigo-500" />}
              Marketplace
            </h3>
            {agent.system ? (
              <p className="text-sm text-gray-500 flex items-center gap-2">
                <Globe className="w-4 h-4 text-gray-400" />
                {t('agentDetails.systemAgentsAreAvailableIn')}
              </p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800">
                      {shared ? t('agentDetails.publishedToMarketplace') : t('agentDetails.privateToWorkspace')}
                    </p>
                    <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                      {shared
                        ? <>{t('agentDetails.visibleToEveryone')} <Link to="/marketplace" className="text-indigo-600 hover:text-indigo-800 font-semibold">{t('agentDetails.marketplace')}</Link> {t('agentDetails.andCanBeAddedTo')}</>
                        : agent.owner_workspace
                          ? <>{t('agentDetails.onlyVisibleIn')} <span className="font-semibold text-gray-700">{agent.owner_workspace}</span> {t('agentDetails.andCannotBeAddedTo')}</>
                          : t('agentDetails.notBoundToWorkspace')}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer flex-shrink-0 mt-0.5">
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={shared}
                      disabled={sharingSaving}
                      onChange={(e) => handleToggleShared(e.target.checked)}
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600" />
                  </label>
                </div>
                {sharingMessage && (
                  <p className="text-xs text-indigo-600">{sharingMessage}</p>
                )}
              </div>
            )}
          </div>

          {/* ── Tools & Memory stats ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Tools */}
            <div className="bg-white p-5 shadow-md rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                  <Wrench className="w-4 h-4 text-indigo-500" /> {t('agentDetails.tools')}
                </h3>
                <button type="button" onClick={() => setActiveTab('tools')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  {t('agentDetails.manage')}
                </button>
              </div>
              <div className="flex items-center gap-6 mb-4">
                <div className="text-center">
                  <p className="text-3xl font-bold text-indigo-700">{selectedTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.enabled')}</p>
                </div>
                <div className="text-center">
                  <p className="text-3xl font-bold text-gray-300">{availableTools.length}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.available')}</p>
                </div>
              </div>
              {selectedTools.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {selectedTools.slice(0, 8).map(tool => (
                    <span key={tool} className="text-xs bg-indigo-50 text-indigo-700 border border-indigo-100 px-2 py-0.5 rounded-full">{tool}</span>
                  ))}
                  {selectedTools.length > 8 && (
                    <button type="button" onClick={() => setActiveTab('tools')}
                      className="text-xs text-gray-400 hover:text-indigo-600 px-1 py-0.5">
                      +{selectedTools.length - 8} more
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-xs text-gray-400 italic">{t('agentDetails.noToolsEnabledClickManage')}</p>
              )}
            </div>

            {/* Memory */}
            <div className="bg-white p-5 shadow-md rounded-lg">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                  <Database className="w-4 h-4 text-amber-500" /> {t('agentDetails.memory')}
                </h3>
                <button type="button" onClick={() => setActiveTab('memory')}
                  className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded hover:bg-indigo-50">
                  {t('agentDetails.configure')}
                </button>
              </div>
              <div className="space-y-3">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold ${
                  memoryType === 'none'   ? 'bg-gray-100 text-gray-500' :
                  memoryType === 'local'  ? 'bg-blue-100 text-blue-700' :
                                            'bg-amber-100 text-amber-700'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${
                    memoryType === 'none'  ? 'bg-gray-400' :
                    memoryType === 'local' ? 'bg-blue-500' : 'bg-amber-500 animate-pulse'
                  }`} />
                  {memoryType === 'none' ? t('agentDetails.noMemory') : memoryType === 'local' ? t('agentDetails.localAgentSpecific') : t('agentDetails.sharedPool')}
                </span>
                {memoryType === 'shared' && memoryPools.length > 0 && (
                  <div className="text-xs text-gray-500 truncate">
                    {memoryPools.length === 1
                      ? t('agentDetails.poolSingle', { pool: memoryPools[0] })
                      : t('agentDetails.poolMulti', { count: memoryPools.length, pool: memoryPools[0] })}
                  </div>
                )}
                {memoryType === 'local' && memoryData && (
                  <div className="text-xs text-gray-500 italic line-clamp-2">{memoryData.slice(0, 120)}{memoryData.length > 120 ? '…' : ''}</div>
                )}
                {memoryType === 'none' && (
                  <p className="text-xs text-gray-400">{t('agentDetails.noMemoryPersistenceBetweenSessions')}</p>
                )}
              </div>
            </div>
          </div>

          {activeTask && (
            <div className="bg-indigo-50 p-4 rounded-lg border border-indigo-100">
              <h3 className="text-indigo-800 font-bold flex items-center mb-2">
                <Clock className="w-4 h-4 mr-2" /> {t('agentDetails.currentlyActive')}
              </h3>
              <p className="text-sm text-indigo-900 font-medium truncate mb-2">{t('agentDetails.taskId')}: {activeTask.task_id}</p>
              <Link to={`/tasks/${activeTask.task_id}`} className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 inline-block">
                {t('agentDetails.viewTaskDetails')}
              </Link>
            </div>
          )}

          {/* ── Chat Settings ── */}
          {(
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-1">
                <MessageSquare className="w-4 h-4 text-indigo-500" /> {t('agentDetails.chatSettings')}
              </h3>
              <p className="text-xs text-gray-500 mb-4">{t('agentDetails.configureHowThisAgentAppears')}</p>
              <label className="flex items-center gap-3 cursor-pointer select-none">
                <div className="relative">
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={isDefaultChat}
                    disabled={defaultChatSaving}
                    onChange={e => handleToggleDefaultChat(e.target.checked)}
                  />
                  <div className={`w-10 h-6 rounded-full transition-colors ${isDefaultChat ? 'bg-indigo-600' : 'bg-gray-300'} ${defaultChatSaving ? 'opacity-50' : ''}`} />
                  <div className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${isDefaultChat ? 'translate-x-4' : 'translate-x-0'}`} />
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-800">{t('agentDetails.defaultChatAgent')}</div>
                  <div className="text-xs text-gray-500">{t('agentDetails.preSelectThisAgentFor')}</div>
                </div>
              </label>
              {defaultChatMessage && (
                <p className="mt-3 text-xs text-indigo-600">{defaultChatMessage}</p>
              )}
            </div>
          )}
        </div>
  );
}

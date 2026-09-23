/**
 * Tab: Agent Connections — which agents are attached to which memory pools.
 */
import { useState, useEffect } from 'react';
import {
  Database, Save, X, Users, RefreshCw, CheckCircle, Link2, BarChart2,
} from 'lucide-react';
import { getAgents, updateAgentMemory } from '../../api';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { agentPools } from './helpers';

function AgentsTab({ memories, workspaceFilter }) {
  const { t } = useI18n();
  const toast = useToast();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(null);
  const [selectedPoolId, setSelectedPoolId] = useState('');   // primary (write) pool
  const [selectedExtraIds, setSelectedExtraIds] = useState([]); // read-only pools
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const resp = await getAgents(workspaceFilter);
        setAgents(resp.data);
      } catch (e) {
        toast.error(t('memoryManager.errors.loadAgents'), errorDetail(e));
      } finally { setLoading(false); }
    };
    load();
  }, [workspaceFilter, t, toast]);

  const poolById = Object.fromEntries(memories.map(m => [m.id, m]));

  const handleAssign = async () => {
    setSaving(true);
    try {
      const pools = selectedPoolId
        ? [selectedPoolId, ...selectedExtraIds.filter(p => p !== selectedPoolId)]
        : [];
      await updateAgentMemory(assigning.agentId, {
        memory_type: pools.length ? 'shared' : 'none',
        memory_data: pools.length === 0 ? null : pools.length === 1 ? pools[0] : pools,
        workspace: workspaceFilter || 'default',
      });
      const resp = await getAgents(workspaceFilter);
      setAgents(resp.data);
      setAssigning(null);
    } catch (e) {
      toast.error(t('memoryManager.errors.assignAgent'), errorDetail(e));
    } finally { setSaving(false); }
  };

  const agentsWithMemory = agents.filter(a => agentPools(a).length > 0);
  const poolUsage = {};
  agentsWithMemory.forEach(a => {
    agentPools(a).forEach(pid => {
      poolUsage[pid] = (poolUsage[pid] || 0) + 1;
    });
  });

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: t('memoryManager.stats.totalAgents'), value: agents.length, icon: Users, color: 'text-indigo-600 bg-indigo-50' },
          { label: t('memoryManager.stats.withMemory'), value: agentsWithMemory.length, icon: Link2, color: 'text-green-600 bg-green-50' },
          { label: t('memoryManager.stats.memoryPools'), value: memories.length, icon: Database, color: 'text-purple-600 bg-purple-50' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4">
            <div className={`p-3 rounded-xl ${color}`}><Icon className="w-5 h-5" /></div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value}</p>
              <p className="text-xs text-gray-500">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Pool usage summary */}
      {memories.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2"><BarChart2 className="w-4 h-4 text-indigo-500" /> {t('memoryManager.poolUsage')}</h3>
          </div>
          <div className="divide-y divide-gray-100">
            {memories.map(m => (
              <div key={m.id} className="px-5 py-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-800">{m.name}</p>
                  <p className="text-xs text-gray-400">{t('memoryManager.notesAndSlots', { notes: (m.notes || []).length, slots: Object.keys(m.structured_data || {}).length })}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-500">{t('memoryManager.agentCount', { count: poolUsage[m.id] || 0 })}</span>
                  <div className="w-24 bg-gray-100 rounded-full h-1.5">
                    <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${Math.min(100, ((poolUsage[m.id] || 0) / Math.max(1, agents.length)) * 100)}%` }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agent list */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
          <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2"><Users className="w-4 h-4 text-indigo-500" /> {t('memoryManager.agents')}</h3>
        </div>
        {agents.length === 0 ? (
          <p className="p-6 text-center text-gray-400 text-sm italic">{t('memoryManager.noAgentsRegistered')}</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                <th className="px-5 py-2 text-left">{t('memoryManager.agent')}</th>
                <th className="px-5 py-2 text-left">{t('memoryManager.memoryPool')}</th>
                <th className="px-5 py-2 text-left">{t('memoryManager.status')}</th>
                <th className="px-5 py-2 text-right">{t('memoryManager.action')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {agents.map(a => {
                const pools = agentPools(a);
                const primaryPool = pools[0] ? poolById[pools[0]] : null;
                const extraPools = pools.slice(1);
                return (
                  <tr key={a.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <p className="font-medium text-gray-900">{a.name || a.id}</p>
                      <p className="text-xs text-gray-400">{a.domain || '—'}</p>
                    </td>
                    <td className="px-5 py-3">
                      {pools.length > 0 ? (
                        <div>
                          <p className="font-medium text-indigo-700">
                            {primaryPool ? primaryPool.name : pools[0]}
                            {extraPools.length > 0 && (
                              <span className="ml-1.5 text-[10px] font-semibold text-amber-600 uppercase tracking-wider">{t('memoryManager.primary')}</span>
                            )}
                          </p>
                          {extraPools.length > 0 && (
                            <p className="text-xs text-gray-400 mt-0.5">
                              + {extraPools.map(pid => poolById[pid]?.name || pid).join(', ')} (read-only)
                            </p>
                          )}
                        </div>
                      ) : (
                        <span className="text-gray-400 text-xs italic">{t('memoryManager.none')}</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {pools.length > 0 ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full font-medium">
                          <CheckCircle className="w-3 h-3" /> Connected{pools.length > 1 ? ` ×${pools.length}` : ''}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-100 text-gray-500 text-xs rounded-full">
                          {t('memoryManager.noMemory')}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button
                        onClick={() => {
                          setAssigning({ agentId: a.id, agentName: a.name || a.id });
                          setSelectedPoolId(pools[0] || '');
                          setSelectedExtraIds(pools.slice(1));
                        }}
                        className="text-xs border border-indigo-200 text-indigo-600 px-2 py-1 rounded-lg hover:bg-indigo-50"
                      >
                        {pools.length > 0 ? 'Change' : 'Assign'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Assign Modal */}
      {assigning && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-sm w-full overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-indigo-50">
              <div>
                <h3 className="font-bold text-indigo-900">{t('memoryManager.assignMemory')} — {assigning.agentName}</h3>
                <p className="text-xs text-indigo-400 mt-0.5">{t('memoryManager.appliesInWorkspace', { workspace: workspaceFilter || 'default' })}</p>
              </div>
              <button onClick={() => setAssigning(null)} className="text-indigo-400 hover:text-indigo-600"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.primaryPool')}</label>
                <p className="text-xs text-gray-400 mb-2">{t('memoryManager.allMemoryWritesGoTo')}</p>
                <select value={selectedPoolId}
                  onChange={e => {
                    const pid = e.target.value;
                    setSelectedPoolId(pid);
                    if (pid) setSelectedExtraIds(prev => prev.filter(p => p !== pid));
                  }}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                  <option value="">{t('memoryManager.noneNoSharedMemory')}</option>
                  {memories.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </div>
              {selectedPoolId && memories.filter(m => m.id !== selectedPoolId).length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.additionalPools')}</label>
                  <p className="text-xs text-gray-400 mb-2">{t('memoryManager.additionalPoolsHint')}</p>
                  <div className="space-y-1.5 max-h-44 overflow-y-auto pr-1">
                    {memories.filter(m => m.id !== selectedPoolId).map(m => (
                      <label key={m.id} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={selectedExtraIds.includes(m.id)}
                          onChange={e => setSelectedExtraIds(prev =>
                            e.target.checked ? [...prev, m.id] : prev.filter(p => p !== m.id)
                          )}
                          className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                        />
                        <span className="truncate">{m.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex gap-3">
                <button onClick={() => setAssigning(null)} className="flex-1 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-200 font-medium text-sm">{t('memoryManager.cancel')}</button>
                <button onClick={handleAssign} disabled={saving} className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-2">
                  {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export { AgentsTab };

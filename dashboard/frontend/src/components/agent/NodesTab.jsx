import NodeStatusBadge from './NodeStatusBadge';
import { fmtNodeDate, nodeUptime } from './nodeStatus';
import { FileText, Loader, Play, Server, Square, Trash2 } from 'lucide-react';
import { useAgentPage } from './context';

/** The nodes carrying this agent, and starting or stopping one. */
export default function NodesTab() {
  const {
    handleDeleteNode, handleStopNode, nodeBusy, nodes, nodesOverWsCap, openNodeLogs,
    selectedWorkspace, setShowStartNodeModal, setStartWorkspace, t, wsSessionCap,
  } = useAgentPage();
  return (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" />
              {t('agentDetails.agentNodes')}
            </h3>
            <div className="flex items-center gap-2">
              {nodesOverWsCap && (
                <span className="text-xs text-orange-600 font-medium">{t('agentDetails.nodeLimitReached', { limit: wsSessionCap })}</span>
              )}
              <button
                type="button"
                disabled={nodesOverWsCap}
                onClick={() => { setStartWorkspace(selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : ''); setShowStartNodeModal(true); }}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Play className="w-3.5 h-3.5 mr-1" />
                {t('agentDetails.startNode')}
              </button>
            </div>
          </div>
          {nodes.length === 0 ? (
            <p className="text-sm text-gray-500 italic">{t('agentDetails.noNodesFoundForThis')}</p>
          ) : (
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.status')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.nodeId')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.label')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.workspace')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.started')}</th>
                    <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.uptime')}</th>
                    <th className="text-right px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {nodes.map((node) => {
                    const nodeId = node.node_id || node.id;
                    const isActive = node.status === 'running' || node.status === 'starting';
                    const busy = nodeBusy[nodeId];
                    return (
                      <tr key={nodeId} className="hover:bg-gray-50 transition-colors">
                        <td className="px-5 py-3">
                          <NodeStatusBadge status={node.status} />
                        </td>
                        <td className="px-5 py-3">
                          <span className=" text-xs text-gray-600">
                            {String(nodeId).slice(0, 8)}
                            <span className="text-gray-400">…</span>
                          </span>
                        </td>
                        <td className="px-5 py-3 text-gray-600 text-xs">{node.label || '—'}</td>
                        <td className="px-5 py-3">
                          {node.workspace
                            ? <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{node.workspace}</span>
                            : <span className="text-gray-400 text-xs">—</span>}
                        </td>
                        <td className="px-5 py-3 text-gray-500 text-xs whitespace-nowrap">{fmtNodeDate(node.started_at)}</td>
                        <td className="px-5 py-3 text-gray-600 text-xs whitespace-nowrap">
                          {nodeUptime(node.started_at, node.finished_at)}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => openNodeLogs(node)}
                              title={t('agentDetails.viewLogs')}
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                            >
                              <FileText className="w-4 h-4" />
                            </button>
                            {isActive ? (
                              <button
                                type="button"
                                onClick={() => handleStopNode(nodeId)}
                                disabled={!!busy}
                                title={t('agentDetails.stopNode')}
                                className="p-1.5 rounded text-gray-400 hover:text-orange-600 hover:bg-orange-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                              </button>
                            ) : (
                              <button
                                type="button"
                                onClick={() => handleDeleteNode(nodeId)}
                                disabled={!!busy}
                                title={t('agentDetails.removeRecord')}
                                className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                              >
                                {busy === 'deleting' ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
  );
}

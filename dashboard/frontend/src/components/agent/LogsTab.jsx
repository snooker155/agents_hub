import NodeStatusBadge from './NodeStatusBadge';
import { FileText, MessageSquare, Server, Terminal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAgentPage } from './context';

/** The raw run and node logs behind the history. */
export default function LogsTab() {
  const { agentLogsData, openNodeLogs, t } = useAgentPage();
  return (
        <div className="space-y-6">
          <div className="bg-indigo-50 border border-indigo-100 rounded-lg p-4">
            <div className="text-sm font-semibold text-indigo-900 mb-1">{t('agentDetails.nodeScopedLogs')}</div>
            <div className="text-xs text-indigo-700">
              {t('agentDetails.nodeScopedLogsHint')} <code>agents/state/node_runs/&lt;node_id&gt;/</code>.
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <FileText className="w-5 h-5 mr-2 text-indigo-600" /> {t('agentDetails.runLogs')}
            </h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.status')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.node')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.task')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.logFile')}</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {(agentLogsData.runs || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noRunLogsFoundFor')}</td>
                    </tr>
                  ) : (
                    (agentLogsData.runs || []).map((run) => (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 whitespace-nowrap text-sm capitalize">{run.status || '—'}</td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs">
                          {run.node_id ? (
                            <Link to={`/nodes/${run.node_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.node_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs">
                          {run.task_id ? (
                            <Link to={`/tasks/${run.task_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.task_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 text-xs text-gray-500 max-w-[500px] truncate">{run.log_file || '—'}</td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-3">
                            <Link to={`/messages/${run.run_id}`} className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center gap-1">
                              <MessageSquare className="w-3 h-3" /> {t('agentDetails.message')}
                            </Link>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <Server className="w-5 h-5 mr-2 text-indigo-600" /> {t('agentDetails.nodeProcessLogs')}
            </h3>
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.node')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.status')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.workspace')}</th>
                    <th className="text-left px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.logFile')}</th>
                    <th className="text-right px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {(agentLogsData.nodes || []).length === 0 ? (
                    <tr>
                      <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noNodeLogsFoundFor')}</td>
                    </tr>
                  ) : (
                    (agentLogsData.nodes || []).map((node) => {
                      const nodeId = node.node_id || node.id;
                      return (
                        <tr key={nodeId} className="hover:bg-gray-50">
                          <td className="px-4 py-2 text-xs">
                            <Link to={`/nodes/${nodeId}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(nodeId || '').slice(0, 8)}…
                            </Link>
                          </td>
                          <td className="px-4 py-2"><NodeStatusBadge status={node.status} /></td>
                          <td className="px-4 py-2 text-xs text-gray-600">{node.workspace || '—'}</td>
                          <td className="px-4 py-2 text-xs text-gray-500 max-w-[500px] truncate">{node.log_file || '—'}</td>
                          <td className="px-4 py-2 text-right">
                            <button
                              onClick={() => openNodeLogs(node)}
                              className="text-indigo-600 hover:text-indigo-900 text-xs font-medium inline-flex items-center justify-end"
                            >
                              <Terminal className="w-3 h-3 mr-1" /> {t('agentDetails.open')}
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
  );
}

import { AlertCircle, CheckCircle, Clock, History } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAgentPage } from './context';

/** Every run this agent has done. */
export default function HistoryTab() {
  const { history, t } = useAgentPage();
  return (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <History className="w-5 h-5 mr-2" /> {t('agentDetails.executionHistory')}
            </h3>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.status')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.taskId')}</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('agentDetails.startedAt')}</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase">{t('agentDetails.actions')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {history.length === 0 ? (
                    <tr>
                      <td colSpan="4" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noExecutionHistoryFoundFor')}</td>
                    </tr>
                  ) : (
                    history.map((run) => (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 whitespace-nowrap">
                          <div className="flex items-center">
                            {run.status === 'completed' && <CheckCircle className="w-4 h-4 text-green-500 mr-2" />}
                            {run.status === 'failed' && <AlertCircle className="w-4 h-4 text-red-500 mr-2" />}
                            {run.status === 'running' && <Clock className="w-4 h-4 text-blue-500 mr-2 animate-spin" />}
                            <span className="text-sm capitalize">{run.status}</span>
                          </div>
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-600">
                          {run.task_id ? (
                            <Link to={`/tasks/${run.task_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.task_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs text-gray-500">
                          {new Date(run.started_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-right">
                          <div className="flex items-center justify-end gap-3">
                            {run.session_id && (
                              <Link to={`/sessions/${run.session_id}`} className="text-indigo-600 hover:text-indigo-900 text-xs font-medium flex items-center gap-1">
                                <History className="w-3 h-3" /> {t('agentDetails.session')}
                              </Link>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

        </div>
  );
}

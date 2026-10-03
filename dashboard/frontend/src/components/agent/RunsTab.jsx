import { MessageSquare } from 'lucide-react';
import { Link } from 'react-router-dom';
import RunStatusBadge from '../RunStatusBadge';
import { useFormatters } from '../../i18n';
import { useAgentPage } from './context';
import { duration, runSource } from './runLinks';

const TH = 'px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase';

/**
 * This agent's runs, newest first, from the same paged query as the Messages
 * list (GET /api/messages?agent_id=). A carrier run (started by a resident
 * instance's own process) links back to that instance's page.
 */
export default function RunsTab() {
  const { runs, t } = useAgentPage();
  const { formatDate } = useFormatters();
  return (
        <div className="space-y-6">
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold mb-4 flex items-center">
              <MessageSquare className="w-5 h-5 mr-2 text-indigo-600" /> {t('agentDetails.runsTab.heading')}
            </h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className={TH}>{t('agentDetails.runsTab.title')}</th>
                    <th className={TH}>{t('agentDetails.status')}</th>
                    <th className={TH}>{t('agentDetails.runsTab.source')}</th>
                    <th className={TH}>{t('agentDetails.runsTab.instance')}</th>
                    <th className={TH}>{t('agentDetails.startedAt')}</th>
                    <th className={TH}>{t('agentDetails.runsTab.duration')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {runs.items.length === 0 ? (
                    <tr>
                      <td colSpan="6" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.noRunLogsFoundFor')}</td>
                    </tr>
                  ) : runs.items.map((run) => {
                    const source = runSource(run);
                    return (
                      <tr key={run.run_id} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-sm max-w-md">
                          <Link to={`/messages/${run.run_id}`} className="font-medium text-indigo-600 hover:text-indigo-900 hover:underline flex items-center gap-1.5 min-w-0">
                            <MessageSquare className="w-3.5 h-3.5 shrink-0" />
                            <span className="truncate">{run.task_title || t('agentDetails.runsTab.untitled')}</span>
                          </Link>
                          <span className="text-xs text-gray-400 font-mono">{String(run.run_id).slice(0, 8)}</span>
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap"><RunStatusBadge status={run.status} awaiting={run.awaiting} /></td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm">
                          {source ? (
                            <Link to={source.to} className="text-indigo-600 hover:text-indigo-900 hover:underline">
                              {t(`agentDetails.runsTab.sources.${source.kind}`)}
                            </Link>
                          ) : run.session_id ? (
                            <Link to={`/sessions/${run.session_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline">
                              {t('agentDetails.runsTab.sources.session')}
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-xs">
                          {run.instance_id ? (
                            <Link to={`/instances/${run.instance_id}`} className="text-indigo-600 hover:text-indigo-900 hover:underline font-mono">
                              {String(run.instance_id).slice(0, 8)}…
                            </Link>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500">{formatDate(run.started_at) || '—'}</td>
                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500">{duration(run.started_at, run.finished_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {runs.total > runs.items.length && (
              <p className="text-xs text-gray-500 mt-3">
                {t('agentDetails.runsTab.more', { shown: runs.items.length, total: runs.total })}{' '}
                <Link to="/messages" className="text-indigo-600 hover:text-indigo-800">{t('agentDetails.runsTab.openAll')}</Link>
              </p>
            )}
          </div>
        </div>
  );
}

import { History } from 'lucide-react';
import { Link } from 'react-router-dom';
import RunStatusBadge from '../RunStatusBadge';
import { useFormatters } from '../../i18n';
import { useAgentPage } from './context';

const TH = 'px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase';

/** The sessions this agent took part in, newest first (GET /api/sessions?agent_id=). */
export default function SessionsTab() {
  const { sessions, t } = useAgentPage();
  const { formatDate } = useFormatters();
  return (
    <div className="bg-white p-6 shadow-md rounded-lg">
      <h3 className="text-lg font-bold mb-4 flex items-center">
        <History className="w-5 h-5 mr-2" /> {t('agentDetails.sessionsTab.heading')}
      </h3>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className={TH}>{t('agentDetails.sessionsTab.title')}</th>
              <th className={TH}>{t('agentDetails.status')}</th>
              <th className={TH}>{t('agentDetails.sessionsTab.messages')}</th>
              <th className={TH}>{t('agentDetails.workspace')}</th>
              <th className={TH}>{t('agentDetails.startedAt')}</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {sessions.items.length === 0 ? (
              <tr>
                <td colSpan="5" className="px-4 py-8 text-center text-gray-500 italic">{t('agentDetails.sessionsTab.empty')}</td>
              </tr>
            ) : sessions.items.map((s) => (
              <tr key={s.session_id} className="hover:bg-gray-50">
                <td className="px-4 py-2 text-sm max-w-md">
                  <Link to={`/sessions/${s.session_id}`} className="font-medium text-indigo-600 hover:text-indigo-900 hover:underline block truncate">
                    {s.title || t('agentDetails.sessionsTab.untitled')}
                  </Link>
                  <span className="text-xs text-gray-400 font-mono">{String(s.session_id).slice(0, 8)}</span>
                </td>
                <td className="px-4 py-2 whitespace-nowrap"><RunStatusBadge status={s.status} /></td>
                <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-700">{s.message_count ?? 0}</td>
                <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-700">{s.workspace || '—'}</td>
                <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500">{formatDate(s.created_at) || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sessions.total > sessions.items.length && (
        <p className="text-xs text-gray-500 mt-3">
          {t('agentDetails.sessionsTab.more', { shown: sessions.items.length, total: sessions.total })}{' '}
          <Link to="/sessions" className="text-indigo-600 hover:text-indigo-800">{t('agentDetails.sessionsTab.openAll')}</Link>
        </p>
      )}
    </div>
  );
}

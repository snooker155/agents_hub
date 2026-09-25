/**
 * The Conversations tab: every visitor's threads, and a thread's transcript
 * with a link to the run behind each reply. The owner sees what visitors
 * deleted (marked) and what the preview started (flagged); deleting here is
 * for good, while the runs stay on the Messages page.
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink, Loader, MessagesSquare, Trash2 } from 'lucide-react';
import { deleteWidgetThread, getWidgetThread, getWidgetThreads } from '../../api/widgets';
import { useFormatters, useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';
import { shortVisitor } from './widgetUtils';

function Badge({ children, tone = 'gray' }) {
  const tones = {
    gray: 'bg-gray-100 text-gray-600',
    amber: 'bg-amber-50 text-amber-700 border border-amber-200',
    indigo: 'bg-indigo-50 text-indigo-700 border border-indigo-200',
  };
  return <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${tones[tone]}`}>{children}</span>;
}

function Transcript({ data, t, formatDate }) {
  const messages = data?.messages || [];
  return (
    <ol className="space-y-3" aria-label={data?.thread?.title || ''}>
      {messages.map((m) => {
        const mine = m.role === 'user';
        return (
          <li key={m.message_id}>
            <div className={`rounded-lg border p-3 text-sm ${mine ? 'bg-gray-50 border-gray-200' : 'bg-white border-indigo-100'}`}>
              <div className="flex flex-wrap items-center gap-2 mb-1 text-xs text-gray-500">
                <span className="font-semibold text-gray-700">
                  {mine ? t('widgets.conversations.visitorRole') : (m.agent_name || m.agent_id)}
                </span>
                <span>{formatDate(m.created_at)}</span>
                {!mine && m.status === 'stopped' && <Badge tone="amber">{t('widgets.conversations.stopped')}</Badge>}
                {!mine && m.status === 'failed' && <Badge tone="amber">{t('widgets.conversations.failed')}</Badge>}
                {!mine && m.tokens > 0 && <span>{t('widgets.conversations.tokens', { count: m.tokens })}</span>}
                {m.run_id && (
                  <Link to={`/messages/${m.run_id}`} className="ml-auto inline-flex items-center gap-1 text-indigo-600 hover:underline">
                    <ExternalLink className="w-3 h-3" /> {t('widgets.conversations.openRun')}
                  </Link>
                )}
              </div>
              <p className="whitespace-pre-wrap break-words text-gray-800">{m.text}</p>
              {m.attachments?.length > 0 && (
                <p className="mt-1 text-xs text-gray-500">
                  {t('widgets.conversations.attachments', { names: m.attachments.join(', ') })}
                </p>
              )}
              {m.citations?.length > 0 && (
                <div className="mt-2 border-t border-gray-100 pt-2 text-xs text-gray-500">
                  <span className="font-semibold">{t('widgets.conversations.sources')}:</span>{' '}
                  {m.citations.map((c) => `[${c.n}] ${c.title}`).join('; ')}
                </div>
              )}
            </div>
            {m.handoff && (
              <p className="my-2 text-center text-xs text-gray-400">
                {t('widgets.conversations.handoff', { name: m.handoff.to_agent_name || m.handoff.to_agent_id })}
              </p>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export default function WidgetConversations({ widget }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const toast = useToast();
  const [threads, setThreads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [transcript, setTranscript] = useState(null);
  const [reading, setReading] = useState(false);

  const loadThreads = useCallback(async () => {
    try {
      const { data } = await getWidgetThreads(widget.widget_id);
      setThreads(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(t('widgets.loadFailed'), errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [widget.widget_id, toast, t]);

  useEffect(() => { loadThreads(); }, [loadThreads]);

  const open = async (threadId) => {
    setSelected(threadId);
    setReading(true);
    try {
      const { data } = await getWidgetThread(widget.widget_id, threadId);
      setTranscript(data);
    } catch (err) {
      setTranscript(null);
      toast.error(t('widgets.conversations.loadFailed'), errorDetail(err));
    } finally {
      setReading(false);
    }
  };

  const remove = async (threadId) => {
    if (!window.confirm(t('widgets.conversations.confirmDelete'))) return;
    try {
      await deleteWidgetThread(widget.widget_id, threadId);
      toast.success(t('widgets.conversations.deleted'));
      if (selected === threadId) { setSelected(null); setTranscript(null); }
      loadThreads();
    } catch (err) {
      toast.error(t('widgets.actionFailed'), errorDetail(err));
    }
  };

  if (loading) {
    return <div className="flex justify-center py-12"><Loader className="w-5 h-5 animate-spin text-indigo-500" /></div>;
  }
  if (!threads.length) {
    return (
      <div className="text-center py-12 text-sm text-gray-500">
        <MessagesSquare className="w-8 h-8 text-gray-300 mx-auto mb-2" />
        {t('widgets.conversations.empty')}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] gap-4">
      <ul className="space-y-1 max-h-[600px] overflow-y-auto" aria-label={t('widgets.tabs.conversations')}>
        {threads.map((th) => (
          <li key={th.thread_id} className={`group flex items-start gap-1 rounded-lg ${selected === th.thread_id ? 'bg-indigo-50' : 'hover:bg-gray-50'}`}>
            <button type="button" onClick={() => open(th.thread_id)} aria-current={selected === th.thread_id ? 'true' : undefined}
              className="flex-1 min-w-0 text-left px-3 py-2">
              <span className="block text-sm font-medium text-gray-900 truncate">
                {th.title || t('widgets.conversations.visitor', { id: shortVisitor(th.visitor_id) })}
              </span>
              <span className="flex flex-wrap items-center gap-1.5 text-xs text-gray-500 mt-0.5">
                <span>{t('widgets.conversations.visitor', { id: shortVisitor(th.visitor_id) })}</span>
                <span>{t('widgets.conversations.messages', { count: th.message_count || 0 })}</span>
                <span>{formatDate(th.updated_at)}</span>
                {th.preview && <Badge tone="indigo">{t('widgets.conversations.preview')}</Badge>}
                {th.visitor_deleted_at && <Badge tone="amber">{t('widgets.conversations.deletedByVisitor')}</Badge>}
              </span>
            </button>
            <button type="button" onClick={() => remove(th.thread_id)} title={t('widgets.conversations.delete')}
              aria-label={t('widgets.conversations.delete')}
              className="p-1.5 mt-1.5 mr-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50">
              <Trash2 className="w-4 h-4" />
            </button>
          </li>
        ))}
      </ul>
      <div className="min-w-0">
        {reading ? (
          <div className="flex justify-center py-12"><Loader className="w-5 h-5 animate-spin text-indigo-500" /></div>
        ) : transcript ? (
          <Transcript data={transcript} t={t} formatDate={formatDate} />
        ) : (
          <p className="text-sm text-gray-500 py-12 text-center">{t('widgets.conversations.select')}</p>
        )}
      </div>
    </div>
  );
}

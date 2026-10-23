import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { AlertCircle, CheckCircle, Loader, X } from 'lucide-react';
import { getDecomposition } from '../../api';
import { useI18n } from '../../i18n';
import { useStreamEvent } from '../stream';
import LiveRunStream from '../LiveRunStream';
import { forwardBack } from './taskBack';

// While a pass runs the tree is refetched this often: the subtasks the
// decomposer adds are its tool calls, and this is the plain way to follow them.
const POLL_MS = 1500;

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * A decomposition followed live (routes/task_decompose.py): which pass is on,
 * the tasks created so far as key and title in their tree, and the
 * decomposer's own output (text, thinking, tool calls) streamed on `channel`.
 *
 * `task` is null while the task itself is still being created; the stream is
 * mounted from the start anyway, so nothing the decomposer says before the
 * create request returns is missed.
 */
export default function DecomposeProgress({ task, channel, onNavigate }) {
  const { t } = useI18n();
  const [status, setStatus] = useState(null);
  const [tree, setTree] = useState([]);
  const taskId = task?.id;
  const linkState = forwardBack(useLocation());

  const refresh = useCallback(() => {
    if (!taskId) return;
    getDecomposition(taskId)
      // The poll answers the same thing most of the time; keeping the old
      // object then spares a re-render of the list and the stream under it.
      .then((r) => {
        const nextTree = r.data?.tree || [];
        setTree((prev) => (sameJson(prev, nextTree) ? prev : nextTree));
        if (r.data?.status) setStatus((prev) => (sameJson(prev, r.data.status) ? prev : r.data.status));
      })
      .catch(() => {});
  }, [taskId]);

  useStreamEvent(channel, 'decompose_status', (ev) => { setStatus(ev); refresh(); });

  const running = !status || status.state === 'running';
  useEffect(() => {
    refresh();
    if (!taskId || !running) return undefined;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [taskId, running, refresh]);

  let caption;
  let Icon = Loader;
  let tone = 'text-indigo-700 bg-indigo-50 border-indigo-200';
  if (!task) caption = t('createTaskModal.decomposeCreating');
  else if (status?.state === 'done') {
    caption = t('createTaskModal.decomposeDone', { count: tree.length });
    Icon = CheckCircle;
    tone = 'text-emerald-700 bg-emerald-50 border-emerald-200';
  } else if (status?.state === 'failed') {
    caption = status.error ? `${t('createTaskModal.decomposeFailed')}: ${status.error}` : t('createTaskModal.decomposeFailed');
    Icon = AlertCircle;
    tone = 'text-red-700 bg-red-50 border-red-200';
  } else caption = status?.pass === 'review' ? t('createTaskModal.decomposeReview') : t('createTaskModal.decomposeSplit');

  return (
    <div className="space-y-4">
      {task && (
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-gray-400">{task.key}</span>
          <span className="text-base font-semibold text-gray-800">{task.title}</span>
        </div>
      )}

      <div className={`flex items-center gap-2 border rounded-lg px-3 py-2 text-sm ${tone}`}>
        <Icon className={`w-4 h-4 flex-shrink-0 ${Icon === Loader ? 'animate-spin' : ''}`} />
        <span>{caption}</span>
      </div>

      <div>
        <h4 className="text-sm font-semibold text-gray-800 mb-2">{t('createTaskModal.decomposeCreated')}</h4>
        {tree.length === 0 ? (
          <p className="text-sm text-gray-400 italic">{t('createTaskModal.decomposeNothingYet')}</p>
        ) : (
          <ul className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-64 overflow-y-auto">
            {tree.map((node) => (
              <li key={node.id} className="py-1.5 pr-3 text-sm" style={{ paddingLeft: `${0.75 + (node.depth - 1) * 1.25}rem` }}>
                <Link to={`/tasks/${node.id}`} state={linkState} onClick={onNavigate} className="flex items-baseline gap-2 hover:text-indigo-700">
                  <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{node.key}</span>
                  <span className="text-gray-800 truncate">{node.title}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <LiveRunStream key={channel} sessionId={channel} title={t('createTaskModal.decomposeAgent')}
                     timeline bare />

      {running && task && <p className="text-xs text-gray-400">{t('createTaskModal.decomposeBackground')}</p>}
    </div>
  );
}

/**
 * DecomposeProgress in a modal with Close and Open task, for the create form
 * and the task page's Decompose button. `openTask` is hidden on the page of
 * that very task (`showOpenTask={false}`).
 */
export function DecomposeModal({ task, channel, onClose, showOpenTask = true }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const openTask = () => { onClose(); navigate(`/tasks/${task.id}`); };
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-3xl w-full max-h-[90vh] overflow-y-auto p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xl font-bold text-gray-800">{t('createTaskModal.decomposeTitle')}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <DecomposeProgress task={task} channel={channel} onNavigate={onClose} />
        <div className="flex justify-end gap-3 mt-6">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('createTaskModal.close')}</button>
          {showOpenTask && (
            <button
              type="button" onClick={openTask} disabled={!task}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {t('createTaskModal.openTask')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

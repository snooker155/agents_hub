/**
 * Subtasks: the add-subtask modal and one subtask row.
 */
import { ExternalLink, Trash2, UserPlus, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createTask } from '../../api';
import { PriorityBadge, StatusDropdown } from './TaskFields';
import { executorLabel } from './taskUtils';
import { useI18n } from '../../i18n';

export function AddSubtaskModal({ parentId, parentWorkspace, onCreated, onCancel }) {
  const { t } = useI18n();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!title.trim()) return;
    setLoading(true);
    try {
      await createTask({
        title: title.trim(),
        description,
        parent_id: parentId,
        workspace_name: parentWorkspace || '',
        should_decompose: false,
      });
      onCreated();
    } catch (err) {
      alert(`${t('taskDetails.errors.createSubtask')}: ` + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">{t('taskDetails.addSubtask')}</h3>
          <button onClick={onCancel} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskDetails.title')}</label>
            <input
              ref={inputRef}
              type="text"
              required
              placeholder={t('taskDetails.subtaskTitle')}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
              value={title}
              onChange={e => setTitle(e.target.value)}
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskDetails.description')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
            <textarea
              rows={3}
              placeholder={t('taskDetails.describeWhatNeedsToBe')}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none"
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
              {t('taskDetails.cancel')}
            </button>
            <button
              type="submit"
              disabled={loading || !title.trim()}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? t('taskDetails.adding') : t('taskDetails.addSubtask')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function SubtaskRow({ st, onDelete, onStatusChange, onAssign, deleting }) {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <div className="flex items-start gap-3 p-3 border border-gray-100 rounded-lg hover:bg-gray-50 transition-colors">
      {/* Status toggle */}
      <div className="pt-0.5 flex-shrink-0">
        <StatusDropdown current={st.status} onChange={(v) => onStatusChange(st.id, v)} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 cursor-pointer" onClick={() => navigate(`/tasks/${st.id}`)}>
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">{st.title}</span>
          {st.priority && <PriorityBadge priority={st.priority} />}
        </div>
        {st.description && (
          <p className="text-xs text-gray-500 truncate mt-0.5">{st.description}</p>
        )}
        {st.assigned_agent_type && (
          <span className="inline-flex items-center gap-1 text-xs text-gray-400 mt-0.5">
            {(() => { const { Icon } = executorLabel(st); return <Icon className="w-3 h-3" />; })()}
            {executorLabel(st).id}
            {st.agent_state && st.agent_state !== 'none' && (
              <span className={`ml-1 px-1 py-0.5 rounded text-xs ${
                st.agent_state === 'running' ? 'bg-blue-100 text-blue-700' :
                st.agent_state === 'completed' ? 'bg-green-100 text-green-700' :
                st.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                st.agent_state === 'pending' ? 'bg-yellow-100 text-yellow-700' :
                'bg-gray-100 text-gray-600'
              }`}>{st.agent_state === 'pending_approval' ? t('taskDetails.awaitingApproval') : st.agent_state}</span>
            )}
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          onClick={() => navigate(`/tasks/${st.id}`)}
          className="p-1 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded"
          title={t('taskDetails.openDetails')}
        >
          <ExternalLink className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onAssign(st)}
          className="p-1 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded"
          title={t('taskDetails.assignAgent')}
        >
          <UserPlus className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onDelete(st.id)}
          disabled={deleting}
          className="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-50"
          title={t('taskDetails.delete')}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

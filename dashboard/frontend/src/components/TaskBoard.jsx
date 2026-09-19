import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import { useLiveRefetch } from './stream';
import {
  Plus, CheckCircle, Clock, AlertCircle, StopCircle, Loader,
  ExternalLink, Trash2, List, Columns, UserPlus, ChevronRight,
  GitBranch, User, X, ThumbsUp, ThumbsDown, Github, Gitlab, Workflow, Folder, HelpCircle
} from 'lucide-react';
import { getTasks, deleteTask, getAgents, assignAgent, approveAssignment, rejectAssignment, updateTask, listFlows, runFlow, getProjects } from '../api';
import CreateTaskModal from './CreateTaskModal';
import { useI18n, statusLabel } from '../i18n';

// ─── Status configuration ────────────────────────────────────────────────────
const STATUS_CONFIG = {
  todo:        { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Clock,        },
  ready:       { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: CheckCircle,  },
  pending:     { bg: 'bg-amber-100',  text: 'text-amber-700',  icon: Clock,        },
  awaiting_input: { bg: 'bg-amber-100', text: 'text-amber-700', icon: HelpCircle, },
  in_progress: { bg: 'bg-yellow-100', text: 'text-yellow-700', icon: Loader,       },
  blocked:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: AlertCircle,  },
  stopped:     { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: StopCircle,   },
  resolved:    { bg: 'bg-purple-100', text: 'text-purple-700', icon: CheckCircle,  },
  reviewing:   { bg: 'bg-cyan-100',   text: 'text-cyan-700',   icon: Loader,       },
  reviewed:    { bg: 'bg-teal-100',   text: 'text-teal-700',   icon: CheckCircle,  },
  done:        { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle,  },
};

// ─── Kanban columns: each column maps to one or more backend statuses ────────
const KANBAN_COLUMNS = [
  {
    id: 'todo', labelKey: 'taskBoard.columns.todo', targetStatus: 'todo',
    statuses: ['todo'],
    headerBg: 'bg-gray-200', headerText: 'text-gray-800',
    dotColor: 'bg-gray-500', dropBorder: 'border-gray-300',
  },
  {
    id: 'ready', labelKey: 'taskBoard.columns.ready', targetStatus: 'ready',
    statuses: ['ready'],
    headerBg: 'bg-blue-50', headerText: 'text-blue-700',
    dotColor: 'bg-blue-500', dropBorder: 'border-blue-300',
  },
  {
    // Anything needing a human: assignment approval (pending) or an answer to the
    // agent's question (awaiting_input). Not droppable — these are entered/left by
    // the system or via the task's own actions, not by dragging.
    id: 'waiting_approval', labelKey: 'taskBoard.columns.waiting', targetStatus: 'pending',
    statuses: ['pending', 'awaiting_input'],
    headerBg: 'bg-amber-50', headerText: 'text-amber-700',
    dotColor: 'bg-amber-500', dropBorder: 'border-amber-300',
    droppable: false,
  },
  {
    id: 'in_progress', labelKey: 'taskBoard.columns.in_progress', targetStatus: 'in_progress',
    statuses: ['in_progress'],
    headerBg: 'bg-yellow-50', headerText: 'text-yellow-700',
    dotColor: 'bg-yellow-500', dropBorder: 'border-yellow-300',
  },
  {
    id: 'blocked', labelKey: 'taskBoard.columns.blocked', targetStatus: 'blocked',
    statuses: ['blocked'],
    headerBg: 'bg-red-50', headerText: 'text-red-700',
    dotColor: 'bg-red-500', dropBorder: 'border-red-300',
  },
  {
    id: 'resolved', labelKey: 'taskBoard.columns.resolved', targetStatus: 'resolved',
    statuses: ['stopped', 'resolved'],
    headerBg: 'bg-purple-50', headerText: 'text-purple-700',
    dotColor: 'bg-purple-500', dropBorder: 'border-purple-300',
  },
  {
    id: 'reviewing', labelKey: 'taskBoard.columns.reviewing', targetStatus: 'reviewing',
    statuses: ['reviewing'],
    headerBg: 'bg-cyan-50', headerText: 'text-cyan-700',
    dotColor: 'bg-cyan-500', dropBorder: 'border-cyan-300',
    droppable: false,
  },
  {
    id: 'reviewed', labelKey: 'taskBoard.columns.reviewed', targetStatus: 'reviewed',
    statuses: ['reviewed'],
    headerBg: 'bg-teal-50', headerText: 'text-teal-700',
    dotColor: 'bg-teal-500', dropBorder: 'border-teal-300',
  },
  {
    id: 'done', labelKey: 'taskBoard.columns.done', targetStatus: 'done',
    statuses: ['done'],
    headerBg: 'bg-green-50', headerText: 'text-green-700',
    dotColor: 'bg-green-500', dropBorder: 'border-green-300',
  },
];

// ─── Small reusable components ───────────────────────────────────────────────
function StatusBadge({ status }) {
  const { t } = useI18n();
  const s = STATUS_CONFIG[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'in_progress' ? 'animate-spin' : ''}`} />
      {statusLabel(status, t)}
    </span>
  );
}

// Badge linking a task back to its source GitHub/GitLab issue
function IssueSourceBadge({ source }) {
  if (!source?.number) return null;
  const Icon = source.provider === 'gitlab' ? Gitlab : Github;
  const label = `#${source.number}`;
  const cls = "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-gray-50 text-gray-600 border border-gray-200";
  if (!source.url) return <span className={cls}><Icon className="w-3 h-3" /> {label}</span>;
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noreferrer"
      onClick={e => e.stopPropagation()}
      className={`${cls} hover:text-indigo-600 hover:border-indigo-200`}
      title={`Open ${source.provider} issue ${label}`}
    >
      <Icon className="w-3 h-3" /> {label}
    </a>
  );
}

// Badge showing the project a task belongs to
function ProjectBadge({ name }) {
  if (!name) return null;
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-indigo-50 text-indigo-700 border border-indigo-100 max-w-[160px]"
      title={`Project: ${name}`}
    >
      <Folder className="w-3 h-3 flex-shrink-0" />
      <span className="truncate">{name}</span>
    </span>
  );
}

function ProgressBar({ total, done }) {
  const { t } = useI18n();
  if (total === 0) return null;
  const pct = Math.round((done / total) * 100);
  return (
    <div className="mt-2">
      <div className="w-full bg-gray-200 rounded-full h-1.5">
        <div className="bg-indigo-500 h-1.5 rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400 mt-0.5 block">{pct}% ({t('taskBoard.subtaskCount', { done, total })})</span>
    </div>
  );
}

// ─── Kanban card ─────────────────────────────────────────────────────────────
function KanbanCard({ task, allTasks, onDelete, onAssign, onApprove, onReject, deletingById, projectName, showWorkspace }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const parentTask = task.parent_id ? allTasks.find(t => t.id === task.parent_id) : null;
  const subtasks = allTasks.filter(t => t.parent_id === task.id);
  const doneSubtasks = subtasks.filter(t => t.status === 'done').length;

  const handleDragStart = (e) => {
    e.dataTransfer.setData('taskId', task.id);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      className="bg-white rounded-lg border border-gray-200 p-3 shadow-sm hover:shadow-md transition-shadow cursor-grab active:cursor-grabbing group"
    >
      {/* Header: breadcrumb + title on the left, actions on the right */}
      <div className="flex items-start gap-2 mb-1">
        <div className="flex-1 min-w-0">
          {/* Parent task breadcrumb */}
          {parentTask && (
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1.5">
              <GitBranch className="w-3 h-3 flex-shrink-0" />
              <Link
                to={`/tasks/${parentTask.id}`}
                className="truncate hover:text-indigo-500 max-w-[180px]"
                onClick={e => e.stopPropagation()}
                title={parentTask.title}
              >
                {parentTask.title}
              </Link>
              <ChevronRight className="w-3 h-3 flex-shrink-0" />
            </div>
          )}

          {/* Title */}
          <div
            className="text-sm font-medium text-gray-800 leading-snug cursor-pointer hover:text-indigo-600"
            onClick={() => navigate(`/tasks/${task.id}`)}
            title={task.title}
          >
            {task.key && <span className="text-xs font-semibold text-gray-400 mr-1.5">{task.key}</span>}
            {task.title}
            {projectName && <span className="ml-1.5 align-middle"><ProjectBadge name={projectName} /></span>}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={() => onAssign(task)}
            className="p-1 text-indigo-500 hover:text-indigo-700 hover:bg-indigo-50 rounded"
            title={t('taskBoard.assignAgent')}
          >
            <UserPlus className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onDelete(task.id, task.title)}
            disabled={!!deletingById[task.id]}
            className="p-1 text-red-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-40"
            title={t('taskBoard.deleteTask')}
          >
            {deletingById[task.id] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Description */}
      {task.description && (
        <p className="text-xs text-gray-400 line-clamp-2 mb-2">{task.description}</p>
      )}

      {/* Blocked reason */}
      {task.blocked_reason && (
        <div className="flex items-start gap-1 bg-red-50 border border-red-100 rounded px-2 py-1 mb-2">
          <AlertCircle className="w-3 h-3 text-red-500 mt-0.5 flex-shrink-0" />
          <span className="text-xs text-red-600 line-clamp-2">{task.blocked_reason}</span>
        </div>
      )}

      {/* Meta row */}
      <div className="flex flex-wrap gap-1.5 mb-2 empty:hidden">
        {showWorkspace && task.workspace && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-gray-50 text-gray-500 border border-gray-200">
            {task.workspace}
          </span>
        )}
        {task.external_source ? (
          <IssueSourceBadge source={task.external_source} />
        ) : task.created_by === 'external' && (
          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-violet-50 text-violet-600 border border-violet-200">
            {t('taskBoard.external')}
          </span>
        )}
      </div>

      {/* Agent / Flow info */}
      {task.assigned_agent_type && (
        <div className="flex items-center gap-1 text-xs text-gray-500 mb-1">
          {task.assigned_agent_params?.flow_id ? (
            <Workflow className="w-3 h-3 text-purple-500" />
          ) : (
            <User className="w-3 h-3" />
          )}
          <span className="truncate">{task.assigned_agent_type}</span>
          {task.assigned_agent_params?.flow_id && (
            <span className="px-1 py-0.5 rounded text-[10px] font-medium bg-purple-50 text-purple-600 border border-purple-100">{t('taskBoard.flow')}</span>
          )}
          {task.agent_state && task.agent_state !== 'none' && (
            <span className={`ml-auto px-1.5 py-0.5 rounded text-xs font-medium ${
              task.agent_state === 'running' ? 'bg-green-100 text-green-700' :
              task.agent_state === 'completed' ? 'bg-blue-100 text-blue-700' :
              task.agent_state === 'failed' ? 'bg-red-100 text-red-700' :
              task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
              'bg-gray-100 text-gray-600'
            }`}>
              {task.agent_state === 'pending_approval' ? t('taskBoard.awaitingApproval') : task.agent_state}
            </span>
          )}
        </div>
      )}

      {/* Pending approval actions */}
      {task.agent_state === 'pending_approval' && (
        <div className="flex gap-1 mb-1">
          <button
            onClick={(e) => { e.stopPropagation(); onApprove(task.id); }}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-1 text-xs bg-green-50 text-green-700 border border-green-200 rounded hover:bg-green-100"
          >
            <ThumbsUp className="w-3 h-3" /> {t('taskBoard.approve')}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onReject(task.id); }}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-1 text-xs bg-red-50 text-red-700 border border-red-200 rounded hover:bg-red-100"
          >
            <ThumbsDown className="w-3 h-3" /> {t('taskBoard.reject')}
          </button>
        </div>
      )}

      {/* Subtask progress */}
      <ProgressBar total={subtasks.length} done={doneSubtasks} />
    </div>
  );
}

// ─── Kanban column ────────────────────────────────────────────────────────────
function KanbanColumn({ column, tasks, allTasks, onDrop, onDelete, onAssign, onApprove, onReject, deletingById, onAddTask, isFirst, projectNameForTask, showWorkspace }) {
  const { t } = useI18n();
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDragOver = (e) => {
    if (column.droppable === false) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setIsDragOver(true);
  };

  const handleDragLeave = () => setIsDragOver(false);

  const handleDrop = (e) => {
    if (column.droppable === false) return;
    e.preventDefault();
    setIsDragOver(false);
    const taskId = e.dataTransfer.getData('taskId');
    if (taskId) onDrop(taskId, column.targetStatus);
  };

  return (
    <div className="w-[280px] shrink-0 flex flex-col">
      {/* Column header */}
      <div className={`flex items-center justify-between px-3 py-2 rounded-t-lg ${column.headerBg}`}>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${column.dotColor}`} />
          <span className={`text-sm font-semibold ${column.headerText}`}>{t(column.labelKey)}</span>
        </div>
        <div className="flex items-center gap-1">
          <span className={`text-xs px-1.5 py-0.5 rounded-full bg-white bg-opacity-70 font-medium ${column.headerText}`}>
            {tasks.length}
          </span>
          {isFirst && (
            <button
              onClick={() => onAddTask(column.targetStatus)}
              className={`p-0.5 rounded hover:bg-white hover:bg-opacity-50 ${column.headerText} opacity-60 hover:opacity-100`}
              title={t('taskBoard.addNewTask')}
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`flex-1 p-2 pb-4 rounded-b-lg border-2 transition-colors flex flex-col gap-2 ${
          isDragOver && column.droppable !== false
            ? `border-dashed ${column.dropBorder} bg-opacity-20`
            : 'border-transparent bg-gray-50'
        }`}
      >
        {tasks.length === 0 && !isDragOver && (
          <div className="text-center text-xs text-gray-400 py-6 select-none">{t('taskBoard.noTasks')}</div>
        )}
        {tasks.map(task => (
          <KanbanCard
            key={task.id}
            task={task}
            allTasks={allTasks}
            onDelete={onDelete}
            onAssign={onAssign}
            onApprove={onApprove}
            onReject={onReject}
            deletingById={deletingById}
            projectName={projectNameForTask(task)}
            showWorkspace={showWorkspace}
          />
        ))}
      </div>
    </div>
  );
}

// ─── Assign Agent Modal ───────────────────────────────────────────────────────
function AssignAgentModal({ task, agents, flows, onClose, onAssigned }) {
  const { t } = useI18n();
  const [target, setTarget] = useState('agent'); // 'agent' | 'flow'
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedFlow, setSelectedFlow] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const selection = target === 'flow' ? selectedFlow : selectedAgent;

  const handleAssign = async () => {
    if (!selection) return;
    setLoading(true);
    setError('');
    try {
      if (target === 'flow') {
        await runFlow(selectedFlow, { task_id: task.id, workspace: task.workspace });
      } else {
        await assignAgent(task.id, { agent_id: selectedAgent });
      }
      onAssigned();
      onClose();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  const tabClass = (id) => `flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
    target === id ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
  }`;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-sm w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">{t('taskBoard.assignToTask')}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-gray-600 mb-4 truncate">{t('taskBoard.task')} <span className="font-medium">{task.title}</span></p>

        {/* Agent / Flow toggle */}
        <div className="flex items-center bg-gray-100 rounded-lg p-1 mb-4">
          <button type="button" onClick={() => { setTarget('agent'); setError(''); }} className={tabClass('agent')}>
            <User className="w-4 h-4" />
            {t('taskBoard.agent')}
          </button>
          <button type="button" onClick={() => { setTarget('flow'); setError(''); }} className={tabClass('flow')}>
            <Workflow className="w-4 h-4" />
            {t('taskBoard.flow')}
          </button>
        </div>

        {target === 'agent' ? (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskBoard.selectAgent')}</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={selectedAgent}
              onChange={e => setSelectedAgent(e.target.value)}
            >
              <option value="">{t('taskBoard.chooseAnAgent')}</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name || a.id}</option>
              ))}
            </select>
          </div>
        ) : (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskBoard.selectFlow')}</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={selectedFlow}
              onChange={e => setSelectedFlow(e.target.value)}
            >
              <option value="">{t('taskBoard.chooseAFlow')}</option>
              {flows.map(f => (
                <option key={f.id} value={f.id}>{f.name || f.id}</option>
              ))}
            </select>
            {flows.length === 0 && (
              <p className="text-xs text-gray-400 mt-1">{t('taskBoard.noFlowsAuthorizedForThis')}</p>
            )}
          </div>
        )}

        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">{t('taskBoard.cancel')}</button>
          <button
            onClick={handleAssign}
            disabled={!selection || loading}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? t('taskBoard.assigning') : (target === 'flow' ? t('taskBoard.runFlow') : t('taskBoard.assign'))}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Reusable Task board (list + kanban) ──────────────────────────────────────
// Renders the full task management UI (drag-and-drop kanban, list view, assign,
// create, approve/reject, delete). Scope it with `workspace` and/or `projectId`.
//
// Props:
//   workspace          – workspace name to fetch tasks/agents/flows for (or undefined for all)
//   projectId          – when set, only tasks of this project are shown / created
//   showWorkspaceColumn – show the workspace column (list) and badge (kanban)
//   selectedWorkspace  – workspace stored on newly created tasks + localStorage key
//   liveUpdates        – subscribe to live "tasks.changed" refetch events
//   title              – optional heading shown on the left of the toolbar
//   toolbarTarget      – DOM node to portal the toolbar into (e.g. a tab row).
//                        When provided, the inline header is not rendered.
export default function TaskBoard({
  workspace,
  projectId = null,
  showWorkspaceColumn = false,
  selectedWorkspace,
  liveUpdates = false,
  title = null,
  toolbarTarget = undefined,
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [allTasks, setAllTasks] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [flows, setFlows] = useState([]);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);

  const [viewMode, setViewMode] = useState('kanban'); // 'list' | 'kanban'

  // Persist showSubtasks per workspace in localStorage
  const _subtasksKey = `task_manager_show_subtasks_${selectedWorkspace || 'default'}`;
  const [showSubtasks, setShowSubtasks] = useState(() => {
    try { return localStorage.getItem(_subtasksKey) === 'true'; } catch { return false; }
  });

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createDefaultStatus, setCreateDefaultStatus] = useState('todo');
  const [assignModal, setAssignModal] = useState(null); // task object or null

  const [deletingById, setDeletingById] = useState({});

  // Fetch everything
  const fetchData = async () => {
    try {
      const [tasksResp, agentsResp, flowsResp, projectsResp] = await Promise.all([
        getTasks(workspace),
        getAgents(workspace).catch(() => ({ data: [] })),
        listFlows(workspace).catch(() => ({ data: [] })),
        getProjects(workspace).catch(() => ({ data: [] })),
      ]);

      let raw = tasksResp.data;
      // Scope to a single project (keep subtasks of matched tasks too)
      if (projectId) {
        const directIds = new Set(raw.filter(t => t.project_id === projectId).map(t => t.id));
        raw = raw.filter(t => t.project_id === projectId || (t.parent_id && directIds.has(t.parent_id)));
      }
      setAllTasks(raw);
      setAgents(agentsResp.data);
      setFlows(flowsResp.data || []);
      setProjects(projectsResp.data || []);

      // Enhanced top-level tasks with subtask counts
      const topLevel = raw.filter(t => !t.parent_id).map(task => {
        const subs = raw.filter(s => s.parent_id === task.id);
        return { ...task, _subtasks: subs };
      });
      setTasks(topLevel);
    } catch (err) {
      console.error('Error fetching tasks:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, projectId, liveUpdates]);
  useLiveRefetch(fetchData, { type: 'tasks.changed', enabled: liveUpdates });

  // Reload showSubtasks preference when workspace changes
  useEffect(() => {
    try {
      const key = `task_manager_show_subtasks_${selectedWorkspace || 'default'}`;
      setShowSubtasks(localStorage.getItem(key) === 'true');
    } catch { /* storage unavailable — subtasks stay hidden */ }
  }, [selectedWorkspace]);

  // Delete
  const handleDelete = async (taskId, taskTitle) => {
    if (!window.confirm(`Delete task "${taskTitle}"? This cannot be undone.`)) return;
    setDeletingById(prev => ({ ...prev, [taskId]: true }));
    try {
      await deleteTask(taskId, { cascade: true });
      fetchData();
    } catch (err) {
      alert(`${t('taskBoard.errors.delete')}: ` + (err.response?.data?.detail || err.message));
    } finally {
      setDeletingById(prev => ({ ...prev, [taskId]: false }));
    }
  };

  // Status change (drag-and-drop or direct)
  const handleStatusChange = async (taskId, newStatus) => {
    if (newStatus === 'pending') return;
    const task = allTasks.find(t => t.id === taskId);
    if (!task || task.status === newStatus) return;
    // Optimistic update
    setAllTasks(prev => prev.map(t => t.id === taskId ? { ...t, status: newStatus } : t));
    try {
      await updateTask(taskId, { status: newStatus });
      fetchData();
    } catch (err) {
      console.error('Failed to update status:', err);
      fetchData(); // revert
    }
  };

  // Assign agent modal
  const handleAssign = (task) => setAssignModal(task);
  const handleAssigned = () => { setAssignModal(null); fetchData(); };

  // Approve / reject pending assignments
  const handleApprove = async (taskId) => {
    try { await approveAssignment(taskId); fetchData(); }
    catch (err) { alert(`${t('taskBoard.errors.approve')}: ` + (err.response?.data?.detail || err.message)); }
  };
  const handleReject = async (taskId) => {
    try { await rejectAssignment(taskId); fetchData(); }
    catch (err) { alert(`${t('taskBoard.errors.reject')}: ` + (err.response?.data?.detail || err.message)); }
  };

  // Add task from column header
  const handleAddTask = (status) => {
    setCreateDefaultStatus(status);
    setShowCreateModal(true);
  };

  // Resolve a human-readable project name for a task (linked Project record by
  // id, falling back to the task's stored project folder name). When the board
  // is already scoped to a single project (project page Tasks tab), the project
  // badge is redundant, so suppress it — it stays on the main/workspace boards.
  const projectsById = React.useMemo(
    () => Object.fromEntries(projects.map(p => [p.id, p.name])),
    [projects]
  );
  const projectNameForTask = (task) =>
    projectId ? null : ((task.project_id && projectsById[task.project_id]) || task.project || null);

  // ── Kanban: which tasks appear in each column ─────────────────────────────
  const tasksForKanban = showSubtasks ? allTasks : allTasks.filter(t => !t.parent_id);

  const getColumnTasks = (column) => tasksForKanban.filter((t) => {
    // Human-interaction statuses live only in the Waiting column.
    if (t.status === 'pending' || t.status === 'awaiting_input') return column.id === 'waiting_approval';
    return column.statuses.includes(t.status);
  });

  // ── List view helpers ─────────────────────────────────────────────────────
  const topLevelWithProgress = tasks.map(task => {
    const subs = allTasks.filter(s => s.parent_id === task.id);
    const done = subs.filter(s => s.status === 'done').length;
    return { ...task, subtasks: subs, doneSubtasks: done, progress: subs.length > 0 ? (done / subs.length) * 100 : (task.status === 'done' ? 100 : 0) };
  });

  // Toolbar: subtasks toggle (kanban only) + view switch + New Task button.
  const controls = (
    <div className="flex items-center gap-3">
      {viewMode === 'kanban' && (
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
            checked={showSubtasks}
            onChange={e => {
              const val = e.target.checked;
              setShowSubtasks(val);
              try { localStorage.setItem(_subtasksKey, String(val)); } catch { /* storage unavailable */ }
            }}
          />
          {t('taskBoard.showSubtasks')}
        </label>
      )}
      {/* View toggle */}
      <div className="flex items-center bg-gray-100 rounded-lg p-1">
        <button
          onClick={() => setViewMode('list')}
          className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-sm font-medium transition-colors ${
            viewMode === 'list' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <List className="w-4 h-4" />
          {t('taskBoard.list')}
        </button>
        <button
          onClick={() => setViewMode('kanban')}
          className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-sm font-medium transition-colors ${
            viewMode === 'kanban' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <Columns className="w-4 h-4" />
          {t('taskBoard.kanban')}
        </button>
      </div>

      <button
        onClick={() => { setCreateDefaultStatus('todo'); setShowCreateModal(true); }}
        className="bg-indigo-600 text-white px-4 py-2 rounded-md flex items-center gap-2 text-sm hover:bg-indigo-700 transition-colors"
      >
        <Plus className="w-4 h-4" />
        {t('taskBoard.newTask')}
      </button>
    </div>
  );

  return (
    <div className="flex flex-col min-h-full">
      {/* Header — rendered inline, or portaled into the parent tab row when a
          `toolbarTarget` is provided (embedded in a detail page). */}
      {toolbarTarget === undefined ? (
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-4">
            {title && <h2 className="text-2xl font-semibold text-gray-800">{title}</h2>}
          </div>
          {controls}
        </div>
      ) : (
        toolbarTarget && createPortal(controls, toolbarTarget)
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-gray-400">
          <Loader className="w-6 h-6 animate-spin mr-2" />
          {t('taskBoard.loadingTasks')}
        </div>
      ) : viewMode === 'kanban' ? (
        /* ── KANBAN VIEW ── */
        <div className="flex gap-3 overflow-x-auto items-stretch flex-1 min-h-0">
          {KANBAN_COLUMNS.map((col, idx) => (
            <KanbanColumn
              key={col.id}
              column={col}
              tasks={getColumnTasks(col)}
              allTasks={allTasks}
              onDrop={handleStatusChange}
              onDelete={handleDelete}
              onAssign={handleAssign}
              onApprove={handleApprove}
              onReject={handleReject}
              deletingById={deletingById}
              onAddTask={handleAddTask}
              isFirst={idx === 0}
              projectNameForTask={projectNameForTask}
              showWorkspace={showWorkspaceColumn}
            />
          ))}
        </div>
      ) : (
        /* ── LIST VIEW ── */
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.status')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.title')}</th>
                {showWorkspaceColumn && (
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.workspace')}</th>
                )}
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.agent')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.waitApproval')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.blocked')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.progress')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.created')}</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">{t('taskBoard.actions')}</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {topLevelWithProgress.length === 0 ? (
                <tr>
                  <td colSpan={showWorkspaceColumn ? 9 : 8} className="px-6 py-10 text-center text-gray-400">{t('taskBoard.noTasksFound')}</td>
                </tr>
              ) : (
                topLevelWithProgress.map(task => (
                  <tr
                    key={task.id}
                    className="hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/tasks/${task.id}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => { if (e.key === 'Enter') navigate(`/tasks/${task.id}`); }}
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <StatusBadge status={task.status} />
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        {task.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{task.key}</span>}
                        <span className="text-sm font-medium text-gray-900">{task.title}</span>
                        <ProjectBadge name={projectNameForTask(task)} />
                        {task.external_source ? (
                          <IssueSourceBadge source={task.external_source} />
                        ) : task.created_by === 'external' && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-violet-50 text-violet-600 border border-violet-200 flex-shrink-0">{t('taskBoard.external')}</span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 truncate max-w-xs">{task.description}</div>
                    </td>
                    {showWorkspaceColumn && (
                      <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-600">
                        <span className="bg-gray-100 px-2 py-1 rounded">{task.workspace || '—'}</span>
                      </td>
                    )}
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-600">
                      {task.assigned_agent_type ? (
                        <span className="flex items-center gap-1">
                          {task.assigned_agent_params?.flow_id ? (
                            <Workflow className="w-3 h-3 text-purple-500" />
                          ) : (
                            <User className="w-3 h-3" />
                          )}
                          {task.assigned_agent_type}
                        </span>
                      ) : '—'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs">
                      {task.agent_state === 'pending_approval' ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded bg-amber-100 text-amber-700">{t('taskBoard.yes')}</span>
                      ) : (
                        <span className="text-gray-400">{t('taskBoard.no')}</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs">
                      {task.status === 'blocked' ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded bg-red-100 text-red-700">{t('taskBoard.yes')}</span>
                      ) : (
                        <span className="text-gray-400">{t('taskBoard.no')}</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {task.subtasks.length > 0 ? (
                        <>
                          <div className="w-full bg-gray-200 rounded-full h-2 max-w-[120px]">
                            <div className="bg-indigo-600 h-2 rounded-full" style={{ width: `${task.progress}%` }} />
                          </div>
                          <span className="text-xs text-gray-400 mt-0.5 block">
                            {Math.round(task.progress)}% ({task.doneSubtasks}/{task.subtasks.length})
                          </span>
                        </>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500">
                      {new Date(task.created_at).toLocaleString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={e => { e.stopPropagation(); handleAssign(task); }}
                          className="p-1.5 text-indigo-500 hover:text-indigo-700 hover:bg-indigo-50 rounded"
                          title={t('taskBoard.assignAgent')}
                        >
                          <UserPlus className="w-4 h-4" />
                        </button>
                        <Link
                          to={`/tasks/${task.id}`}
                          className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-50 rounded"
                          onClick={e => e.stopPropagation()}
                          title={t('taskBoard.openDetails')}
                        >
                          <ExternalLink className="w-4 h-4" />
                        </Link>
                        <button
                          onClick={e => { e.stopPropagation(); handleDelete(task.id, task.title); }}
                          disabled={!!deletingById[task.id]}
                          className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-40"
                          title={t('taskBoard.deleteTask')}
                        >
                          {deletingById[task.id] ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Modals */}
      {showCreateModal && (
        <CreateTaskModal
          defaultStatus={createDefaultStatus}
          selectedWorkspace={selectedWorkspace}
          defaultProjectId={projectId}
          lockProject={!!projectId}
          onClose={() => setShowCreateModal(false)}
          onCreated={fetchData}
        />
      )}
      {assignModal && (
        <AssignAgentModal
          task={assignModal}
          agents={agents}
          flows={flows}
          onClose={() => setAssignModal(null)}
          onAssigned={handleAssigned}
        />
      )}
    </div>
  );
}

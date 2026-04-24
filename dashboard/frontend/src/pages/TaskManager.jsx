import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Plus, CheckCircle, Clock, AlertCircle, StopCircle, Loader,
  ExternalLink, Trash2, List, Columns, UserPlus, ChevronRight,
  GitBranch, User, Layers, X, ThumbsUp, ThumbsDown
} from 'lucide-react';
import { getTasks, createTask, deleteTask, getAgents, assignAgent, approveAssignment, rejectAssignment, updateTask, getProjects } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

// ─── Status configuration ────────────────────────────────────────────────────
const STATUS_CONFIG = {
  todo:        { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Clock,        label: 'Todo' },
  ready:       { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: CheckCircle,  label: 'Ready to Assign' },
  pending:     { bg: 'bg-amber-100',  text: 'text-amber-700',  icon: Clock,        label: 'Waiting Approval' },
  in_progress: { bg: 'bg-yellow-100', text: 'text-yellow-700', icon: Loader,       label: 'In Progress' },
  blocked:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: AlertCircle,  label: 'Blocked' },
  stopped:     { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: StopCircle,   label: 'Stopped' },
  resolved:    { bg: 'bg-purple-100', text: 'text-purple-700', icon: CheckCircle,  label: 'Resolved' },
  reviewing:   { bg: 'bg-cyan-100',   text: 'text-cyan-700',   icon: Loader,       label: 'Reviewing' },
  reviewed:    { bg: 'bg-teal-100',   text: 'text-teal-700',   icon: CheckCircle,  label: 'Reviewed' },
  done:        { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle,  label: 'Done' },
};

// ─── Kanban columns: each column maps to one or more backend statuses ────────
const KANBAN_COLUMNS = [
  {
    id: 'todo', label: 'Todo', targetStatus: 'todo',
    statuses: ['todo'],
    headerBg: 'bg-gray-100', headerText: 'text-gray-700',
    dotColor: 'bg-gray-400', dropBorder: 'border-gray-300',
  },
  {
    id: 'ready', label: 'Ready to Assign', targetStatus: 'ready',
    statuses: ['ready'],
    headerBg: 'bg-blue-50', headerText: 'text-blue-700',
    dotColor: 'bg-blue-500', dropBorder: 'border-blue-300',
  },
  {
    id: 'waiting_approval', label: 'Waiting Approval', targetStatus: 'pending',
    statuses: ['pending'],
    headerBg: 'bg-amber-50', headerText: 'text-amber-700',
    dotColor: 'bg-amber-500', dropBorder: 'border-amber-300',
    droppable: false,
  },
  {
    id: 'in_progress', label: 'In Progress', targetStatus: 'in_progress',
    statuses: ['in_progress'],
    headerBg: 'bg-yellow-50', headerText: 'text-yellow-700',
    dotColor: 'bg-yellow-500', dropBorder: 'border-yellow-300',
  },
  {
    id: 'blocked', label: 'Blocked', targetStatus: 'blocked',
    statuses: ['blocked'],
    headerBg: 'bg-red-50', headerText: 'text-red-700',
    dotColor: 'bg-red-500', dropBorder: 'border-red-300',
  },
  {
    id: 'resolved', label: 'Resolved', targetStatus: 'resolved',
    statuses: ['stopped', 'resolved'],
    headerBg: 'bg-purple-50', headerText: 'text-purple-700',
    dotColor: 'bg-purple-500', dropBorder: 'border-purple-300',
  },
  {
    id: 'reviewing', label: 'Reviewing', targetStatus: 'reviewing',
    statuses: ['reviewing'],
    headerBg: 'bg-cyan-50', headerText: 'text-cyan-700',
    dotColor: 'bg-cyan-500', dropBorder: 'border-cyan-300',
    droppable: false,
  },
  {
    id: 'reviewed', label: 'Reviewed', targetStatus: 'reviewed',
    statuses: ['reviewed'],
    headerBg: 'bg-teal-50', headerText: 'text-teal-700',
    dotColor: 'bg-teal-500', dropBorder: 'border-teal-300',
  },
  {
    id: 'done', label: 'Done', targetStatus: 'done',
    statuses: ['done'],
    headerBg: 'bg-green-50', headerText: 'text-green-700',
    dotColor: 'bg-green-500', dropBorder: 'border-green-300',
  },
];

// ─── Small reusable components ───────────────────────────────────────────────
function StatusBadge({ status }) {
  const s = STATUS_CONFIG[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle, label: status };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'in_progress' ? 'animate-spin' : ''}`} />
      {s.label}
    </span>
  );
}

function ProgressBar({ value, total, done }) {
  if (total === 0) return null;
  const pct = Math.round((done / total) * 100);
  return (
    <div className="mt-2">
      <div className="w-full bg-gray-200 rounded-full h-1.5">
        <div className="bg-indigo-500 h-1.5 rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400 mt-0.5 block">{pct}% ({done}/{total} subtasks)</span>
    </div>
  );
}

// ─── Kanban card ─────────────────────────────────────────────────────────────
function KanbanCard({ task, allTasks, onDelete, onAssign, onStatusChange, onApprove, onReject, deletingById }) {
  const navigate = useNavigate();
  const parentTask = task.parent_id ? allTasks.find(t => t.id === task.parent_id) : null;
  const subtasks = allTasks.filter(t => t.parent_id === task.id);
  const doneSubtasks = subtasks.filter(t => t.status === 'done').length;
  const childSubtasks = allTasks.filter(t => t.parent_id === task.id);

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
        className="text-sm font-medium text-gray-800 leading-snug mb-1 cursor-pointer hover:text-indigo-600"
        onClick={() => navigate(`/tasks/${task.id}`)}
        title={task.title}
      >
        {task.title}
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
      <div className="flex flex-wrap gap-1.5 mb-2">
        <StatusBadge status={task.status} />
        {task.workspace && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-gray-50 text-gray-500 border border-gray-200">
            {task.workspace}
          </span>
        )}
        {task.created_by === 'external' && (
          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-violet-50 text-violet-600 border border-violet-200">
            External
          </span>
        )}
        {childSubtasks.length > 0 && (
          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-indigo-50 text-indigo-600 border border-indigo-100">
            <Layers className="w-3 h-3" />
            {childSubtasks.length} subtasks
          </span>
        )}
      </div>

      {/* Agent info */}
      {task.assigned_agent_type && (
        <div className="flex items-center gap-1 text-xs text-gray-500 mb-1">
          <User className="w-3 h-3" />
          <span className="truncate">{task.assigned_agent_type}</span>
          {task.agent_state && task.agent_state !== 'none' && (
            <span className={`ml-auto px-1.5 py-0.5 rounded text-xs font-medium ${
              task.agent_state === 'running' ? 'bg-green-100 text-green-700' :
              task.agent_state === 'completed' ? 'bg-blue-100 text-blue-700' :
              task.agent_state === 'failed' ? 'bg-red-100 text-red-700' :
              task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
              'bg-gray-100 text-gray-600'
            }`}>
              {task.agent_state === 'pending_approval' ? 'awaiting approval' : task.agent_state}
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
            <ThumbsUp className="w-3 h-3" /> Approve
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onReject(task.id); }}
            className="flex-1 flex items-center justify-center gap-1 px-2 py-1 text-xs bg-red-50 text-red-700 border border-red-200 rounded hover:bg-red-100"
          >
            <ThumbsDown className="w-3 h-3" /> Reject
          </button>
        </div>
      )}

      {/* Subtask progress */}
      <ProgressBar value={subtasks.length} total={subtasks.length} done={doneSubtasks} />

      {/* Actions */}
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-gray-100 opacity-0 group-hover:opacity-100 transition-opacity">
        <div className="flex gap-1">
          <button
            onClick={() => onAssign(task)}
            className="p-1 text-indigo-500 hover:text-indigo-700 hover:bg-indigo-50 rounded"
            title="Assign agent"
          >
            <UserPlus className="w-3.5 h-3.5" />
          </button>
          <Link
            to={`/tasks/${task.id}`}
            className="p-1 text-gray-500 hover:text-gray-700 hover:bg-gray-50 rounded"
            title="Open details"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </Link>
        </div>
        <button
          onClick={() => onDelete(task.id, task.title)}
          disabled={!!deletingById[task.id]}
          className="p-1 text-red-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-40"
          title="Delete task"
        >
          {deletingById[task.id] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

// ─── Kanban column ────────────────────────────────────────────────────────────
function KanbanColumn({ column, tasks, allTasks, onDrop, onDelete, onAssign, onStatusChange, onApprove, onReject, deletingById, onAddTask, isFirst }) {
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
    <div className="flex-1 min-w-[220px] max-w-xs flex flex-col">
      {/* Column header */}
      <div className={`flex items-center justify-between px-3 py-2 rounded-t-lg ${column.headerBg}`}>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${column.dotColor}`} />
          <span className={`text-sm font-semibold ${column.headerText}`}>{column.label}</span>
        </div>
        <div className="flex items-center gap-1">
          <span className={`text-xs px-1.5 py-0.5 rounded-full bg-white bg-opacity-70 font-medium ${column.headerText}`}>
            {tasks.length}
          </span>
          {isFirst && (
            <button
              onClick={() => onAddTask(column.targetStatus)}
              className={`p-0.5 rounded hover:bg-white hover:bg-opacity-50 ${column.headerText} opacity-60 hover:opacity-100`}
              title="Add new task"
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
          <div className="text-center text-xs text-gray-400 py-6 select-none">No tasks</div>
        )}
        {tasks.map(task => (
          <KanbanCard
            key={task.id}
            task={task}
            allTasks={allTasks}
            onDelete={onDelete}
            onAssign={onAssign}
            onStatusChange={onStatusChange}
            onApprove={onApprove}
            onReject={onReject}
            deletingById={deletingById}
          />
        ))}
      </div>
    </div>
  );
}

// ─── Assign Agent Modal ───────────────────────────────────────────────────────
function AssignAgentModal({ task, agents, onClose, onAssigned }) {
  const [selectedAgent, setSelectedAgent] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleAssign = async () => {
    if (!selectedAgent) return;
    setLoading(true);
    setError('');
    try {
      await assignAgent(task.id, { agent_id: selectedAgent });
      onAssigned();
      onClose();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-sm w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">Assign Agent</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <p className="text-sm text-gray-600 mb-4 truncate">Task: <span className="font-medium">{task.title}</span></p>
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">Select Agent</label>
          <select
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
            value={selectedAgent}
            onChange={e => setSelectedAgent(e.target.value)}
          >
            <option value="">-- Choose an agent --</option>
            {agents.map(a => (
              <option key={a.id} value={a.id}>{a.name || a.id}</option>
            ))}
          </select>
        </div>
        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
          <button
            onClick={handleAssign}
            disabled={!selectedAgent || loading}
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? 'Assigning…' : 'Assign'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Create Task Modal ────────────────────────────────────────────────────────
function CreateTaskModal({ defaultStatus, selectedWorkspace, onClose, onCreated }) {
  const [form, setForm] = useState({
    title: '', description: '',
    should_decompose: false,
    status: defaultStatus || 'todo',
    project_id: '',
  });
  const [loading, setLoading] = useState(false);
  const [projects, setProjects] = useState([]);

  useEffect(() => {
    getProjects()
      .then(r => setProjects(r.data))
      .catch(() => setProjects([]));
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await createTask({ ...form, project_id: form.project_id || null, workspace_name: selectedWorkspace || null });
      onCreated();
      onClose();
    } catch (err) {
      console.error('Error creating task:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg max-w-md w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xl font-bold text-gray-800">Create New Task</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
            <input
              type="text" required
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.title}
              onChange={e => setForm({ ...form, title: e.target.value })}
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              rows="3"
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Initial Status</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.status}
              onChange={e => setForm({ ...form, status: e.target.value })}
            >
              {Object.entries(STATUS_CONFIG).filter(([val]) => val !== 'pending').map(([val, cfg]) => (
                <option key={val} value={val}>{cfg.label}</option>
              ))}
            </select>
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Project (optional)</label>
            <select
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={form.project_id}
              onChange={e => setForm({ ...form, project_id: e.target.value })}
            >
              <option value="">— No project —</option>
              {projects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="mb-6 flex items-center">
            <input
              id="decompose" type="checkbox"
              className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
              checked={form.should_decompose}
              onChange={e => setForm({ ...form, should_decompose: e.target.checked })}
            />
            <label htmlFor="decompose" className="ml-2 text-sm text-gray-700">
              Automatically decompose this task
            </label>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
            <button
              type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Main TaskManager component ───────────────────────────────────────────────
const TaskManager = () => {
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const [tasks, setTasks] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [agents, setAgents] = useState([]);
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
      const [tasksResp, agentsResp] = await Promise.all([
        getTasks(workspaceFilter),
        getAgents(workspaceFilter).catch(() => ({ data: [] })),
      ]);

      const raw = tasksResp.data;
      setAllTasks(raw);
      setAgents(agentsResp.data);

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
    fetchData();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [selectedWorkspace, liveUpdates]);

  // Reload showSubtasks preference when workspace changes
  useEffect(() => {
    try {
      const key = `task_manager_show_subtasks_${selectedWorkspace || 'default'}`;
      setShowSubtasks(localStorage.getItem(key) === 'true');
    } catch { /* ignore */ }
  }, [selectedWorkspace]);

  // Delete
  const handleDelete = async (taskId, title) => {
    if (!window.confirm(`Delete task "${title}"? This cannot be undone.`)) return;
    setDeletingById(prev => ({ ...prev, [taskId]: true }));
    try {
      await deleteTask(taskId, { cascade: true });
      fetchData();
    } catch (err) {
      alert('Error deleting task: ' + (err.response?.data?.detail || err.message));
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
    catch (err) { alert('Error approving: ' + (err.response?.data?.detail || err.message)); }
  };
  const handleReject = async (taskId) => {
    try { await rejectAssignment(taskId); fetchData(); }
    catch (err) { alert('Error rejecting: ' + (err.response?.data?.detail || err.message)); }
  };

  // Add task from column header
  const handleAddTask = (status) => {
    setCreateDefaultStatus(status);
    setShowCreateModal(true);
  };

  // ── Kanban: which tasks appear in each column ─────────────────────────────
  const tasksForKanban = showSubtasks ? allTasks : allTasks.filter(t => !t.parent_id);

  const getColumnTasks = (column) => tasksForKanban.filter((t) => {
    if (t.status === 'pending') return column.id === 'waiting_approval';
    return column.statuses.includes(t.status);
  });

  // ── List view helpers ─────────────────────────────────────────────────────
  const topLevelWithProgress = tasks.map(task => {
    const subs = allTasks.filter(s => s.parent_id === task.id);
    const done = subs.filter(s => s.status === 'done').length;
    return { ...task, subtasks: subs, doneSubtasks: done, progress: subs.length > 0 ? (done / subs.length) * 100 : (task.status === 'done' ? 100 : 0) };
  });

  return (
    <div className="flex flex-col min-h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-4">
          <h2 className="text-2xl font-semibold text-gray-800">Tasks</h2>
          {viewMode === 'kanban' && (
            <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
              <input
                type="checkbox"
                className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
                checked={showSubtasks}
                onChange={e => {
                  const val = e.target.checked;
                  setShowSubtasks(val);
                  try { localStorage.setItem(`task_manager_show_subtasks_${selectedWorkspace || 'default'}`, String(val)); } catch { /* ignore */ }
                }}
              />
              Show subtasks
            </label>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* View toggle */}
          <div className="flex items-center bg-gray-100 rounded-lg p-1">
            <button
              onClick={() => setViewMode('list')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'list' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <List className="w-4 h-4" />
              List
            </button>
            <button
              onClick={() => setViewMode('kanban')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'kanban' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              <Columns className="w-4 h-4" />
              Kanban
            </button>
          </div>

          <button
            onClick={() => { setCreateDefaultStatus('todo'); setShowCreateModal(true); }}
            className="bg-indigo-600 text-white px-4 py-2 rounded-md flex items-center gap-2 text-sm hover:bg-indigo-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Task
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-gray-400">
          <Loader className="w-6 h-6 animate-spin mr-2" />
          Loading tasks…
        </div>
      ) : viewMode === 'kanban' ? (
        /* ── KANBAN VIEW ── */
        <div className="flex gap-3 overflow-x-auto items-stretch flex-1 -mb-5">
          {KANBAN_COLUMNS.map((col, idx) => (
            <KanbanColumn
              key={col.id}
              column={col}
              tasks={getColumnTasks(col)}
              allTasks={allTasks}
              onDrop={handleStatusChange}
              onDelete={handleDelete}
              onAssign={handleAssign}
              onStatusChange={handleStatusChange}
              onApprove={handleApprove}
              onReject={handleReject}
              deletingById={deletingById}
              onAddTask={handleAddTask}
              isFirst={idx === 0}
            />
          ))}
        </div>
      ) : (
        /* ── LIST VIEW ── */
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Title</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Workspace</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Agent</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Wait Approval</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Blocked</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Progress</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {topLevelWithProgress.length === 0 ? (
                <tr>
                  <td colSpan="9" className="px-6 py-10 text-center text-gray-400">No tasks found</td>
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
                        <span className="text-sm font-medium text-gray-900">{task.title}</span>
                        {task.created_by === 'external' && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-violet-50 text-violet-600 border border-violet-200 flex-shrink-0">External</span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 truncate max-w-xs">{task.description}</div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-600">
                      <span className=" bg-gray-100 px-2 py-1 rounded">{task.workspace || '—'}</span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-600">
                      {task.assigned_agent_type ? (
                        <span className="flex items-center gap-1">
                          <User className="w-3 h-3" />
                          {task.assigned_agent_type}
                        </span>
                      ) : '—'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs">
                      {task.agent_state === 'pending_approval' ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded bg-amber-100 text-amber-700">Yes</span>
                      ) : (
                        <span className="text-gray-400">No</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs">
                      {task.status === 'blocked' ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded bg-red-100 text-red-700">Yes</span>
                      ) : (
                        <span className="text-gray-400">No</span>
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
                          title="Assign agent"
                        >
                          <UserPlus className="w-4 h-4" />
                        </button>
                        <Link
                          to={`/tasks/${task.id}`}
                          className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-50 rounded"
                          onClick={e => e.stopPropagation()}
                          title="Open details"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </Link>
                        <button
                          onClick={e => { e.stopPropagation(); handleDelete(task.id, task.title); }}
                          disabled={!!deletingById[task.id]}
                          className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-40"
                          title="Delete task"
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
          onClose={() => setShowCreateModal(false)}
          onCreated={fetchData}
        />
      )}
      {assignModal && (
        <AssignAgentModal
          task={assignModal}
          agents={agents}
          onClose={() => setAssignModal(null)}
          onAssigned={handleAssigned}
        />
      )}
    </div>
  );
};

export default TaskManager;

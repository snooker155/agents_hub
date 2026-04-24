import { useState, useEffect, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  ChevronLeft, Clock, AlertCircle, Terminal,
  Play, Square, Split, Trash2, Folder, Plus, Edit2, Check, X,
  User, UserPlus, Flag, GitBranch, Layers, ExternalLink, Loader,
  ChevronDown, ThumbsUp, ThumbsDown, History, FileText,
} from 'lucide-react';
import {
  getTask, getAgents, assignAgent, approveAssignment, rejectAssignment, stopAgent, getMessageLogs,
  getMessageInsights, runDecomposer, getTaskExecutionLog, deleteTask, updateTask, createTask, getProjects,
  getTaskActivityLog, getTaskResult, getSettings,
} from '../api';
import api from '../api';

// ─── Constants ────────────────────────────────────────────────────────────────
const ALL_STATUSES = [
  { value: 'todo',        label: 'Todo',             bg: 'bg-gray-100',    text: 'text-gray-600',   dot: 'bg-gray-400' },
  { value: 'ready',       label: 'Ready to Assign',  bg: 'bg-blue-100',    text: 'text-blue-700',   dot: 'bg-blue-500' },
  { value: 'pending',     label: 'Waiting Approval', bg: 'bg-amber-100',   text: 'text-amber-700',  dot: 'bg-amber-500', readonly: true },
  { value: 'in_progress', label: 'In Progress',      bg: 'bg-yellow-100',  text: 'text-yellow-700', dot: 'bg-yellow-500' },
  { value: 'blocked',     label: 'Blocked',          bg: 'bg-red-100',     text: 'text-red-700',    dot: 'bg-red-500' },
  { value: 'stopped',     label: 'Stopped',          bg: 'bg-gray-100',    text: 'text-gray-500',   dot: 'bg-gray-400' },
  { value: 'resolved',    label: 'Resolved',         bg: 'bg-purple-100',  text: 'text-purple-700', dot: 'bg-purple-500' },
  { value: 'reviewing',   label: 'Reviewing',        bg: 'bg-cyan-100',    text: 'text-cyan-700',   dot: 'bg-cyan-500',  readonly: true },
  { value: 'reviewed',    label: 'Reviewed',         bg: 'bg-teal-100',    text: 'text-teal-700',   dot: 'bg-teal-500' },
  { value: 'done',        label: 'Done',             bg: 'bg-green-100',   text: 'text-green-700',  dot: 'bg-green-500' },
];

const PRIORITIES = [
  { value: 'critical', label: 'Critical', color: 'text-red-600',    bg: 'bg-red-50',     border: 'border-red-200' },
  { value: 'high',     label: 'High',     color: 'text-orange-600', bg: 'bg-orange-50',  border: 'border-orange-200' },
  { value: 'medium',   label: 'Medium',   color: 'text-yellow-600', bg: 'bg-yellow-50',  border: 'border-yellow-200' },
  { value: 'low',      label: 'Low',      color: 'text-blue-500',   bg: 'bg-blue-50',    border: 'border-blue-200' },
];

const statusCfg = (status) =>
  ALL_STATUSES.find(s => s.value === status) || ALL_STATUSES[0];

const priorityCfg = (p) =>
  PRIORITIES.find(x => x.value === p);

// ─── Small helpers ────────────────────────────────────────────────────────────
function StatusBadge({ status, size = 'sm' }) {
  const s = statusCfg(status);
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium ${
      size === 'xs' ? 'text-xs' : 'text-sm'
    } ${s.bg} ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

function PriorityBadge({ priority }) {
  if (!priority) return null;
  const p = priorityCfg(priority);
  if (!p) return null;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-medium ${p.color} ${p.bg} ${p.border}`}>
      <Flag className="w-3 h-3" />
      {p.label}
    </span>
  );
}

// Inline editable text
function InlineEdit({ value, onSave, multiline = false, className = '' }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const ref = useRef(null);

  useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);

  const commit = async () => {
    if (draft !== value) await onSave(draft);
    setEditing(false);
  };

  const cancel = () => { setDraft(value); setEditing(false); };

  if (!editing) {
    return (
      <span
        className={`group cursor-pointer hover:bg-gray-50 rounded px-1 -mx-1 transition-colors ${className}`}
        onClick={() => setEditing(true)}
        title="Click to edit"
      >
        {value || <span className="text-gray-400 italic">Click to add…</span>}
        <Edit2 className="inline w-3 h-3 ml-1 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />
      </span>
    );
  }

  return (
    <span className="flex items-start gap-1">
      {multiline ? (
        <textarea
          ref={ref}
          className={`border border-indigo-300 rounded px-2 py-1 text-sm resize-none w-full focus:outline-none focus:ring-1 focus:ring-indigo-400 ${className}`}
          rows={4}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') cancel(); if (e.key === 'Enter' && e.metaKey) commit(); }}
        />
      ) : (
        <input
          ref={ref}
          type="text"
          className={`border border-indigo-300 rounded px-2 py-1 text-sm w-full focus:outline-none focus:ring-1 focus:ring-indigo-400 ${className}`}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') cancel(); }}
        />
      )}
      <button onClick={commit} className="p-1 text-green-600 hover:bg-green-50 rounded mt-0.5"><Check className="w-3.5 h-3.5" /></button>
      <button onClick={cancel} className="p-1 text-gray-400 hover:bg-gray-100 rounded mt-0.5"><X className="w-3.5 h-3.5" /></button>
    </span>
  );
}

// Status dropdown with click-outside close
function StatusDropdown({ current, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const s = statusCfg(current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border ${s.bg} ${s.text} border-transparent hover:border-current transition-colors`}
      >
        <span className={`w-2 h-2 rounded-full ${s.dot}`} />
        {s.label}
        <ChevronDown className="w-3.5 h-3.5 ml-0.5" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          {ALL_STATUSES.filter(opt => !opt.readonly).map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 ${opt.value === current ? 'font-semibold' : ''}`}
            >
              <span className={`w-2 h-2 rounded-full ${opt.dot}`} />
              {opt.label}
              {opt.value === current && <Check className="w-3.5 h-3.5 ml-auto text-indigo-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Priority dropdown
function PriorityDropdown({ current, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const p = priorityCfg(current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border ${
          p ? `${p.color} ${p.bg} ${p.border}` : 'text-gray-500 bg-gray-50 border-gray-200'
        } hover:opacity-80 transition-opacity`}
      >
        <Flag className="w-3 h-3" />
        {p ? p.label : 'No priority'}
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-36 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
          >
            <Flag className="w-3 h-3" />
            No priority
            {!current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
          </button>
          {PRIORITIES.map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 ${opt.color}`}
            >
              <Flag className="w-3 h-3" />
              {opt.label}
              {opt.value === current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Workspace link badge
function WorkspaceBadge({ current }) {
  const navigate = useNavigate();
  if (!current) return null;
  return (
    <button
      onClick={() => navigate(`/workspaces/${encodeURIComponent(current)}`)}
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-indigo-200 bg-indigo-50 rounded-lg text-xs font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
      title="Open workspace"
    >
      <Folder className="w-3 h-3" />
      {current}
    </button>
  );
}

// Project selector dropdown
function ProjectSelector({ current, projects, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const navigate = useNavigate();
  const currentProject = projects.find(p => p.id === current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative inline-flex items-center border border-indigo-200 bg-indigo-50 rounded-lg overflow-visible text-xs font-medium text-indigo-700">
      {currentProject ? (
        <button
          onClick={() => navigate(`/projects/${currentProject.id}`)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 hover:bg-indigo-100 transition-colors"
          title="Open project"
        >
          <Folder className="w-3 h-3" />
          {currentProject.name}
        </button>
      ) : (
        <span className="flex items-center gap-1.5 px-2.5 py-1.5 text-indigo-400">
          <Folder className="w-3 h-3" />
          No project
        </span>
      )}
      <button
        onClick={() => setOpen(o => !o)}
        className="px-1.5 py-1.5 border-l border-indigo-200 hover:bg-indigo-100 transition-colors"
        title="Change project"
      >
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1 max-h-52 overflow-auto">
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
          >
            No project
            {!current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
          </button>
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => { onChange(p.id); setOpen(false); }}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 text-gray-700"
            >
              <span className="truncate">{p.name}</span>
              {p.id === current && <Check className="w-3 h-3 ml-auto text-indigo-600 flex-shrink-0" />}
            </button>
          ))}
          {projects.length === 0 && (
            <p className="px-3 py-2 text-xs text-gray-400 italic">No projects found</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Add Subtask Modal ────────────────────────────────────────────────────────
function AddSubtaskModal({ parentId, parentWorkspace, onCreated, onCancel }) {
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
      alert('Error creating subtask: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl max-w-md w-full p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">Add Subtask</h3>
          <button onClick={onCancel} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
            <input
              ref={inputRef}
              type="text"
              required
              placeholder="Subtask title…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={title}
              onChange={e => setTitle(e.target.value)}
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-1">Description <span className="text-gray-400 font-normal">(optional)</span></label>
            <textarea
              rows={3}
              placeholder="Describe what needs to be done…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none focus:ring-indigo-500 focus:border-indigo-500"
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={onCancel} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !title.trim()}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? 'Adding…' : 'Add Subtask'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Subtask row ──────────────────────────────────────────────────────────────
function SubtaskRow({ st, onDelete, onStatusChange, onAssign, deleting }) {
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
            <User className="w-3 h-3" />
            {st.assigned_agent_type}
            {st.agent_state && st.agent_state !== 'none' && (
              <span className={`ml-1 px-1 py-0.5 rounded text-xs ${
                st.agent_state === 'running' ? 'bg-blue-100 text-blue-700' :
                st.agent_state === 'completed' ? 'bg-green-100 text-green-700' :
                st.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                st.agent_state === 'pending' ? 'bg-yellow-100 text-yellow-700' :
                'bg-gray-100 text-gray-600'
              }`}>{st.agent_state === 'pending_approval' ? 'awaiting approval' : st.agent_state}</span>
            )}
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          onClick={() => navigate(`/tasks/${st.id}`)}
          className="p-1 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded"
          title="Open details"
        >
          <ExternalLink className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onAssign(st)}
          className="p-1 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded"
          title="Assign agent"
        >
          <UserPlus className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => onDelete(st.id)}
          disabled={deleting}
          className="p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded disabled:opacity-50"
          title="Delete"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
const TaskDetails = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { liveUpdates } = useWorkspace();

  const [task, setTask] = useState(null);
  const [parentTask, setParentTask] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState('');
  const [executionLog, setExecutionLog] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [workspaceFiles, setWorkspaceFiles] = useState([]);
  const [sessionInsights, setSessionInsights] = useState(null);

  const [activeTab, setActiveTab] = useState('execution');
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [assignTarget, setAssignTarget] = useState(null); // null = parent task, subtask obj otherwise
  const [selectedAgent, setSelectedAgent] = useState('');


  const [deletingTask, setDeletingTask] = useState(false);
  const [deletingSubtasks, setDeletingSubtasks] = useState({});
  const [showAddSubtask, setShowAddSubtask] = useState(false);

  const [saving, setSaving] = useState(false);
  const [projects, setProjects] = useState([]);
  const [activityLog, setActivityLog] = useState([]);
  const [taskResults, setTaskResults] = useState([]);
  const [taskAssignmentMode, setTaskAssignmentMode] = useState('any');

  useEffect(() => {
    getSettings().then(r => setTaskAssignmentMode(r.data.task_assignment_mode || 'any')).catch(() => {});
  }, []);

  const fetchData = async () => {
    try {
      const [taskResp, projectsResp] = await Promise.all([
        getTask(id),
        getProjects().catch(() => ({ data: [] })),
      ]);
      setTask(taskResp.data);
      setProjects(projectsResp.data || []);
      const agentsResp = await getAgents(taskResp.data.workspace || undefined).catch(() => ({ data: [] }));
      setAgents(agentsResp.data);

      if (taskResp.data.parent_id) {
        getTask(taskResp.data.parent_id).then(r => setParentTask(r.data)).catch(() => {});
      } else {
        setParentTask(null);
      }

      if (taskResp.data.assigned_agent_run_id) {
        setActiveRunId(taskResp.data.assigned_agent_run_id);
        fetchLogs(taskResp.data.assigned_agent_run_id);
        fetchInsights(taskResp.data.assigned_agent_run_id);
      } else {
        setActiveRunId(null);
        setLogs('');
        setSessionInsights(null);
      }
      // Always fetch execution log — sidecar file persists independently of assigned_agent_run_id
      fetchExecutionLog();

      getTaskActivityLog(id).then(r => setActivityLog(r.data.activity_log || [])).catch(() => {});
      getTaskResult(id).then(r => {
        setTaskResults(r.data.results || []);
        setWorkspaceFiles(r.data.files || []);
      }).catch(() => {});

      setLoading(false);
    } catch (err) {
      console.error('Error fetching task details:', err);
      setLoading(false);
    }
  };

  const fetchLogs = async (runId) => {
    try { const r = await getMessageLogs(runId); setLogs(r.data.logs); } catch { /* ignore */ }
  };

  const fetchExecutionLog = async () => {
    try { const r = await getTaskExecutionLog(id); setExecutionLog(r.data.entries || []); } catch { /* ignore */ }
  };

  const fetchInsights = async (runId) => {
    try {
      const r = await getMessageInsights(runId);
      setSessionInsights(r.data || null);
    } catch {
      setSessionInsights(null);
    }
  };

  useEffect(() => {
    fetchData();
    if (!liveUpdates) return;
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [id, liveUpdates]);

  // ── Patch helper ─────────────────────────────────────────────────────────
  const patch = async (fields, taskId = id) => {
    setSaving(true);
    try {
      await updateTask(taskId, fields);
      fetchData();
    } catch (err) {
      alert('Failed to update: ' + (err.response?.data?.detail || err.message));
    } finally {
      setSaving(false);
    }
  };

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleAssignAgent = async () => {
    if (!selectedAgent) return;
    try {
      const targetId = assignTarget ? assignTarget.id : id;
      const resp = await assignAgent(targetId, { agent_id: selectedAgent });
      setActiveRunId(resp.data.run_id);
      setShowAssignModal(false);
      setAssignTarget(null);
      setSelectedAgent('');
      fetchData();
    } catch (err) {
      alert('Error assigning agent: ' + (err.response?.data?.detail || err.message));
    }
  };

  const openAssign = (subtask = null) => {
    setAssignTarget(subtask);
    setSelectedAgent('');
    setShowAssignModal(true);
  };

  const handleStopAgent = async () => {
    try { await stopAgent(id); fetchData(); } catch (err) { console.error(err); }
  };

  const handleApproveAssignment = async () => {
    try { await approveAssignment(id); fetchData(); }
    catch (err) { alert('Error approving assignment: ' + (err.response?.data?.detail || err.message)); }
  };

  const handleRejectAssignment = async () => {
    try { await rejectAssignment(id); fetchData(); }
    catch (err) { alert('Error rejecting assignment: ' + (err.response?.data?.detail || err.message)); }
  };

  const handleRunDecomposer = async () => {
    try {
      const resp = await runDecomposer(id, {});
      if (resp.data?.run_id) { setActiveRunId(resp.data.run_id); fetchData(); }
    } catch (err) {
      alert('Error: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleDeleteTask = async () => {
    if (!task) return;
    const count = (task.subtasks || []).length;
    if (!window.confirm(count > 0
      ? `Delete this task and its ${count} subtask(s)? This cannot be undone.`
      : 'Delete this task? This cannot be undone.')
    ) return;
    setDeletingTask(true);
    try { await deleteTask(id, { cascade: true }); navigate('/tasks'); }
    catch (err) { alert('Error: ' + (err.response?.data?.detail || err.message)); }
    finally { setDeletingTask(false); }
  };

  const handleDeleteSubtask = async (subtaskId) => {
    if (!window.confirm('Delete this subtask?')) return;
    setDeletingSubtasks(prev => ({ ...prev, [subtaskId]: true }));
    try { await deleteTask(subtaskId, { cascade: true }); fetchData(); }
    catch (err) { alert('Error: ' + (err.response?.data?.detail || err.message)); }
    finally { setDeletingSubtasks(prev => ({ ...prev, [subtaskId]: false })); }
  };

  // ── Derived ───────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="flex items-center justify-center py-16 text-gray-400">
      <Loader className="w-6 h-6 animate-spin mr-2" /> Loading task…
    </div>
  );
  if (!task) return <div className="text-center py-10 text-gray-500">Task not found</div>;

  const subtasks = (task.subtasks || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  const doneCount = subtasks.filter(s => s.status === 'done').length;
  const progress = subtasks.length > 0 ? Math.round((doneCount / subtasks.length) * 100) : (task.status === 'done' ? 100 : 0);
  const TABS = [
    { id: 'execution', label: 'Execution', Icon: Clock },
    { id: 'subtasks',  label: `Subtasks (${subtasks.length})`, Icon: Layers },
    { id: 'activity',  label: 'Activity', Icon: History },
    { id: 'results',   label: 'Results',  Icon: FileText },
    { id: 'logs',      label: 'Logs',      Icon: Terminal },
    { id: 'files',     label: 'Files',     Icon: Folder },
  ];
  const activityItems = activityLog.slice().sort((a, b) => {
    const ta = new Date(a?.timestamp || 0).getTime();
    const tb = new Date(b?.timestamp || 0).getTime();
    return tb - ta;
  });
  const messageResults = (
    (sessionInsights?.message_runs || [])
      .map((m) => ({ id: m.message_id, timestamp: m.timestamp, output: m.output }))
      .filter((m) => (m.output || '').trim())
  );
  const assistantResults = (
    (sessionInsights?.messages || [])
      .filter((m) => m.role === 'assistant' && (m.content || '').trim())
      .map((m, idx) => ({ id: `assistant-${idx}`, output: m.content }))
  );
  const toolResults = ((sessionInsights?.tools || []).filter((t) => (t.output || '').trim()));
  const hasResults = taskResults.length > 0;

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-5 text-sm text-gray-500">
        <Link to="/tasks" className="flex items-center gap-1 text-indigo-600 hover:text-indigo-800">
          <ChevronLeft className="w-4 h-4" /> Tasks
        </Link>
        {task.parent_id && (
          <>
            <span>/</span>
            <Link to={`/tasks/${task.parent_id}`} className="flex items-center gap-1 text-indigo-600 hover:text-indigo-800 max-w-[200px] truncate">
              <GitBranch className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="truncate">{parentTask ? parentTask.title : task.parent_id.slice(0, 8) + '…'}</span>
            </Link>
          </>
        )}
        <span>/</span>
        <span className="text-gray-700 truncate max-w-xs">{task.title}</span>
      </div>

      {/* Main task card */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 mb-6">
        {/* Title row */}
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="flex-1 min-w-0">
            <h2 className="text-2xl font-bold text-gray-900 mb-1">
              <InlineEdit
                value={task.title}
                onSave={v => patch({ title: v })}
                className="text-2xl font-bold"
              />
            </h2>
            <div className="flex items-center flex-wrap gap-2 mt-2">
              <StatusDropdown current={task.status} onChange={v => patch({ status: v })} />
              <PriorityDropdown current={task.priority} onChange={v => patch({ priority: v })} />
              <ProjectSelector
                current={task.project_id || null}
                projects={projects}
                onChange={v => patch({ project_id: v })}
              />
              <WorkspaceBadge current={task.workspace} />
              {task.created_by === 'external' && (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-violet-50 text-violet-600 border border-violet-200">
                  External
                </span>
              )}
              {saving && <Loader className="w-3.5 h-3.5 text-gray-400 animate-spin" />}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {task.agent_state === 'pending_approval' ? (
              <>
                <button onClick={handleApproveAssignment} className="flex items-center gap-1.5 px-3 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
                  <ThumbsUp className="w-4 h-4" /> Approve
                </button>
                <button onClick={handleRejectAssignment} className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 rounded-lg text-sm hover:bg-red-100">
                  <ThumbsDown className="w-4 h-4" /> Reject
                </button>
              </>
            ) : task.agent_state === 'running' ? (
              <button onClick={handleStopAgent} className="flex items-center gap-1.5 px-3 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700">
                <Square className="w-4 h-4" /> Stop Agent
              </button>
            ) : (
              <button onClick={() => openAssign()} className="flex items-center gap-1.5 px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">
                <Play className="w-4 h-4" /> Assign Agent
              </button>
            )}
            {task.created_by === 'user' && (
              <button onClick={handleRunDecomposer} className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700">
                <Split className="w-4 h-4" /> Decompose
              </button>
            )}
            <button onClick={handleDeleteTask} disabled={deletingTask} className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 rounded-lg text-sm hover:bg-red-100 disabled:opacity-50">
              <Trash2 className="w-4 h-4" />
              {deletingTask ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </div>

        {/* Description */}
        <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
          <InlineEdit
            value={task.description || ''}
            onSave={v => patch({ description: v })}
            multiline
            className="text-sm text-gray-700"
          />
        </div>

        {/* Meta row */}
        <div className="mt-4 pt-4 border-t border-gray-100 flex flex-col gap-2">
          {/* Parent task info */}
          {parentTask && (
            <Link
              to={`/tasks/${parentTask.id}`}
              className="flex items-center gap-3 group hover:no-underline"
            >
              <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0">
                <GitBranch className="w-3.5 h-3.5" />
                Parent task
              </div>
              <div className="flex items-center gap-2 min-w-0">
                <StatusBadge status={parentTask.status} size="xs" />
                <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
                  {parentTask.title}
                </span>
                {parentTask.priority && <PriorityBadge priority={parentTask.priority} />}
                {parentTask.assigned_agent_type && (
                  <span className="flex items-center gap-1 text-xs text-gray-400 flex-shrink-0">
                    <User className="w-3 h-3" />{parentTask.assigned_agent_type}
                  </span>
                )}
              </div>
              <ExternalLink className="w-3.5 h-3.5 text-gray-300 group-hover:text-indigo-500 ml-auto flex-shrink-0 transition-colors" />
            </Link>
          )}
          <div className="flex items-center gap-4 flex-wrap">
          <span className="text-xs text-gray-400">ID: <span className="">{task.id}</span></span>
          <span className="text-xs text-gray-400">Created: {new Date(task.created_at).toLocaleString()}</span>
          {task.assigned_agent_type && (
            <span className="flex items-center gap-1 text-xs text-gray-500">
              <User className="w-3.5 h-3.5" /> {task.assigned_agent_type}
              <span className={`ml-1 px-1.5 py-0.5 rounded text-xs font-medium ${
                task.agent_state === 'running' ? 'bg-blue-100 text-blue-700' :
                task.agent_state === 'completed' ? 'bg-green-100 text-green-700' :
                task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                task.agent_state === 'pending' ? 'bg-yellow-100 text-yellow-700' :
                'bg-gray-100 text-gray-600'
              }`}>{task.agent_state === 'pending_approval' ? 'awaiting approval' : task.agent_state}</span>
            </span>
          )}
          </div>
        </div>

        {/* Subtask progress bar */}
        {subtasks.length > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Progress</span>
              <span>{doneCount}/{subtasks.length} subtasks done ({progress}%)</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div className="bg-indigo-500 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {/* Blocked reason */}
        {task.blocked_reason && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-sm text-red-800 font-semibold flex items-center gap-1.5">
              <AlertCircle className="w-4 h-4" /> Blocked
            </p>
            <p className="text-sm text-red-700 mt-0.5">{task.blocked_reason}</p>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="mb-5 border-b border-gray-200">
        <nav className="flex gap-1">
          {TABS.map(({ id: tid, label, Icon }) => (
            <button
              key={tid}
              onClick={() => {
                setActiveTab(tid);
                if (tid === 'files') {
                  // Prefer stored result files; fall back to live scan for running tasks
                  getTaskResult(id)
                    .then(r => {
                      const stored = r.data.files || [];
                      if (stored.length > 0) {
                        setWorkspaceFiles(stored);
                      } else {
                        api.get(`/tasks/${id}/workspace-files`)
                          .then(r2 => setWorkspaceFiles(r2.data.files || []))
                          .catch(() => {});
                      }
                    })
                    .catch(() => {
                      api.get(`/tasks/${id}/workspace-files`)
                        .then(r2 => setWorkspaceFiles(r2.data.files || []))
                        .catch(() => {});
                    });
                }
              }}
              className={`inline-flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tid
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* ── Subtasks tab ── */}
      {activeTab === 'subtasks' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-gray-800">Subtasks</h3>
            <button
              onClick={() => setShowAddSubtask(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
            >
              <Plus className="w-4 h-4" /> Add Subtask
            </button>
          </div>

          {subtasks.length > 0 ? (
            <div className="space-y-2 mt-3">
              {subtasks.map(st => (
                <SubtaskRow
                  key={st.id}
                  st={st}
                  agents={agents}
                  onDelete={handleDeleteSubtask}
                  onStatusChange={(stId, v) => patch({ status: v }, stId)}
                  onAssign={(st) => openAssign(st)}
                  deleting={!!deletingSubtasks[st.id]}
                />
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic mt-3">
              No subtasks yet. Add one manually or use "Decompose" to auto-generate them.
            </p>
          )}
        </div>
      )}

      {/* ── Execution tab ── */}
      {activeTab === 'execution' && (
        <div className="flex flex-col gap-6">
          {/* Status pane */}
          <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
            <h3 className="text-base font-semibold text-gray-800 mb-4">Execution Status</h3>
            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              {/* Agent */}
              <div className="flex flex-col gap-1">
                <dt className="text-xs text-gray-400 uppercase tracking-wide">Agent</dt>
                <dd>
                  {task.assigned_agent_type ? (
                    <button
                      onClick={() => navigate(`/agents/${task.assigned_agent_type}`)}
                      className="text-sm font-medium text-gray-800 hover:text-indigo-600 transition-colors"
                    >
                      {task.assigned_agent_type}
                    </button>
                  ) : (
                    <span className="text-sm text-gray-400">—</span>
                  )}
                </dd>
              </div>
              {/* State */}
              <div className="flex flex-col gap-1">
                <dt className="text-xs text-gray-400 uppercase tracking-wide">State</dt>
                <dd>
                  <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
                    task.agent_state === 'running'          ? 'bg-blue-100 text-blue-700' :
                    task.agent_state === 'completed'        ? 'bg-green-100 text-green-700' :
                    task.agent_state === 'failed'           ? 'bg-red-100 text-red-700' :
                    task.agent_state === 'stopped'          ? 'bg-gray-100 text-gray-500' :
                    task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                    task.agent_state === 'pending'          ? 'bg-yellow-100 text-yellow-700' :
                    task.agent_state === 'assigned'         ? 'bg-indigo-100 text-indigo-700' :
                    'bg-gray-100 text-gray-500'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      task.agent_state === 'running'          ? 'bg-blue-500 animate-pulse' :
                      task.agent_state === 'completed'        ? 'bg-green-500' :
                      task.agent_state === 'failed'           ? 'bg-red-500' :
                      task.agent_state === 'stopped'          ? 'bg-gray-400' :
                      task.agent_state === 'pending_approval' ? 'bg-amber-500' :
                      task.agent_state === 'pending'          ? 'bg-yellow-500' :
                      task.agent_state === 'assigned'         ? 'bg-indigo-500' :
                      'bg-gray-300'
                    }`} />
                    {task.agent_state === 'pending_approval' ? 'awaiting approval' : (task.agent_state || 'none')}
                  </span>
                </dd>
              </div>
              {/* Session */}
              <div className="flex flex-col gap-1">
                <dt className="text-xs text-gray-400 uppercase tracking-wide">Session</dt>
                <dd>
                  {task.assigned_agent_run_id ? (
                    <button
                      onClick={() => navigate(`/sessions/${task.assigned_agent_run_id}`)}
                      className="text-sm font-medium text-gray-800 hover:text-indigo-600 transition-colors break-all text-left"
                    >
                      {task.assigned_agent_run_id}
                    </button>
                  ) : (
                    <span className="text-sm text-gray-400">—</span>
                  )}
                </dd>
              </div>
            </dl>
          </div>

          {/* Execution log pane */}
          <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
            <h3 className="text-base font-semibold text-gray-800 mb-4">Agent Runs</h3>
            {executionLog.length > 0 ? (
              <div className="space-y-3 max-h-96 overflow-auto pr-1">
                {[...executionLog].reverse().map((entry, i) => (
                  <div key={entry.run_id || i} className="border border-gray-100 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-700">{entry.agent_id}</span>
                      <div className="flex items-center gap-2">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                          entry.status === 'running'   ? 'bg-blue-100 text-blue-700' :
                          entry.status === 'completed' ? 'bg-green-100 text-green-700' :
                          entry.status === 'failed'    ? 'bg-red-100 text-red-700' :
                          entry.status === 'stopped'   ? 'bg-gray-100 text-gray-500' :
                          'bg-gray-100 text-gray-500'
                        }`}>{entry.status}</span>
                        {entry.run_id && (
                          <button
                            onClick={() => navigate(`/messages/${entry.run_id}`)}
                            className="text-gray-300 hover:text-indigo-500 transition-colors"
                            title="Open message details"
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="flex gap-4 text-xs text-gray-400">
                      {entry.started_at && <span>Started: {new Date(entry.started_at).toLocaleString()}</span>}
                      {entry.finished_at && <span>Finished: {new Date(entry.finished_at).toLocaleString()}</span>}
                    </div>
                    {entry.model && <div className="text-xs text-gray-400 mt-0.5">Model: {entry.model}</div>}
                    {(entry.total_tokens > 0) && (
                      <div className="text-xs text-gray-400 mt-0.5">
                        Tokens: {entry.inbound_tokens} in / {entry.outbound_tokens} out
                      </div>
                    )}
                    {entry.error && <div className="text-xs text-red-500 mt-0.5">{entry.error}</div>}
                    <div className="text-xs text-gray-300 mt-1 font-mono truncate">{entry.run_id}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400 italic">No agent runs recorded yet.</p>
            )}
          </div>
        </div>
      )}

      {/* ── Activity tab ── */}
      {activeTab === 'activity' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <h3 className="text-base font-semibold text-gray-800 mb-4">Task Activity</h3>
          {activityItems.length > 0 ? (
            <div className="space-y-3">
              {activityItems.map((item, idx) => {
                const ts = item?.timestamp ? new Date(item.timestamp).toLocaleString() : 'Unknown time';
                const text = item?.message || item?.type || 'Activity update';
                return (
                  <div key={`${item?.timestamp || 't'}-${idx}`} className="border-l-2 border-indigo-200 pl-3 py-1">
                    <div className="text-xs text-gray-400">{ts}</div>
                    <div className="text-sm text-gray-700">{text}</div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">No activity entries yet.</p>
          )}
        </div>
      )}

      {/* ── Results tab ── */}
      {activeTab === 'results' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-gray-800">Agent Results</h3>
            {activeRunId && <span className="text-xs text-gray-400">Run: {activeRunId}</span>}
          </div>
          {hasResults ? (
            <div className="space-y-3">
              {[...taskResults].reverse().map((entry, idx) => (
                <div key={entry.run_id || idx} className="border border-indigo-100 rounded-lg p-3 bg-indigo-50">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-xs font-medium text-indigo-600">
                      {entry.agent_id || 'Agent'}
                    </span>
                    {entry.timestamp && (
                      <span className="text-xs text-gray-400">{new Date(entry.timestamp).toLocaleString()}</span>
                    )}
                    {entry.run_id && (
                      <span className="text-xs text-gray-300 font-mono ml-auto">{entry.run_id.slice(0, 8)}</span>
                    )}
                  </div>
                  <pre className="text-xs text-gray-700 whitespace-pre-wrap">{entry.result}</pre>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">No results captured for this task yet.</p>
          )}
        </div>
      )}

      {/* ── Logs tab ── */}
      {activeTab === 'logs' && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden flex flex-col h-[500px]">
          <div className="bg-gray-800 px-4 py-2.5 flex items-center justify-between">
            <span className="flex items-center gap-2 text-gray-300 text-sm font-medium">
              <Terminal className="w-4 h-4" /> Agent Logs
            </span>
            {activeRunId && (
              <span className="text-xs text-gray-500">Run: {activeRunId.slice(0, 8)}</span>
            )}
          </div>
          <div className="p-4 flex-1 overflow-auto text-xs text-green-400 bg-black leading-relaxed">
            {logs
              ? <pre className="whitespace-pre-wrap">{logs}</pre>
              : <p className="text-gray-600 italic">No logs available.</p>
            }
          </div>
        </div>
      )}

      {/* ── Files tab ── */}
      {activeTab === 'files' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <h3 className="text-base font-semibold text-gray-800 mb-4">Files Changed by Task</h3>
          {workspaceFiles.length ? (
            <ul className="text-sm text-gray-700 max-h-96 overflow-auto divide-y divide-gray-100">
              {workspaceFiles.map(f => (
                <li key={f} className="py-1.5 font-mono text-xs text-gray-700 truncate">{f}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400 italic">No files were created or modified by this task.</p>
          )}
        </div>
      )}

      {/* ── Add Subtask Modal ── */}
      {showAddSubtask && (
        <AddSubtaskModal
          parentId={id}
          parentWorkspace={task.workspace}
          onCreated={() => { setShowAddSubtask(false); fetchData(); }}
          onCancel={() => setShowAddSubtask(false)}
        />
      )}

      {/* ── Assign Agent Modal ── */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl max-w-lg w-full p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold text-gray-800">
                {assignTarget ? `Assign Agent: ${assignTarget.title}` : 'Assign Agent to Task'}
              </h3>
              <button onClick={() => { setShowAssignModal(false); setAssignTarget(null); }} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="mb-2 flex items-center gap-3 text-xs text-gray-400">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> Node running</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-300 inline-block" /> No node</span>
              {taskAssignmentMode === 'nodes_only' && (
                <span className="ml-auto text-amber-600 font-medium">Nodes-only mode — agents without a running node cannot be selected</span>
              )}
            </div>

            <div className="mb-5 grid grid-cols-1 gap-2 max-h-72 overflow-y-auto pr-1">
              {agents.length === 0 && (
                <p className="text-sm text-gray-400 italic py-2">No agents available for this workspace.</p>
              )}
              {agents.map(a => {
                const hasNode = a.has_running_node;
                const disabled = taskAssignmentMode === 'nodes_only' && !hasNode;
                const selected = selectedAgent === a.id;
                return (
                  <button
                    key={a.id}
                    onClick={() => !disabled && setSelectedAgent(a.id)}
                    disabled={disabled}
                    title={disabled ? 'No running node — start a node for this agent first' : undefined}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-all ${
                      disabled
                        ? 'border-gray-100 bg-gray-50 opacity-40 cursor-not-allowed'
                        : selected
                          ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-400'
                          : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50'
                    }`}
                  >
                    <span className={`flex-shrink-0 w-2.5 h-2.5 rounded-full mt-0.5 ${hasNode ? 'bg-green-500' : 'bg-gray-300'}`} />
                    <span className="flex-1 min-w-0">
                      <span className={`block text-sm font-medium ${selected ? 'text-indigo-700' : 'text-gray-800'}`}>
                        {a.name}
                      </span>
                      <span className="block text-xs text-gray-400 truncate">{a.id}{!hasNode && ' · no running node'}</span>
                    </span>
                    {selected && <Check className="w-4 h-4 text-indigo-600 flex-shrink-0" />}
                  </button>
                );
              })}
            </div>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => { setShowAssignModal(false); setAssignTarget(null); }}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >Cancel</button>
              <button
                onClick={handleAssignAgent}
                disabled={!selectedAgent}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >Start Execution</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TaskDetails;

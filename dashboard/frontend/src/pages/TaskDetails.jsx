import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import {
  ChevronLeft, Clock, AlertCircle, Terminal,
  Play, Pause, Square, Split, Trash2, Folder, FolderOpen, Plus, Check, X,
  User, UserPlus, Flag, GitBranch, Layers, ExternalLink, Loader,
  ChevronDown, ChevronRight, ThumbsUp, ThumbsDown, History, FileText, Eye, Code2, HelpCircle,
  CheckSquare, ShieldQuestion,
} from 'lucide-react';
import {
  getTask, getAgents, assignAgent, approveAssignment, rejectAssignment, stopAgent, getMessageLogs,
  runDecomposer, getTaskExecutionLog, deleteTask, updateTask, createTask, getProjects,
  getTaskActivityLog, getTaskResult, getSettings, getTaskFileContent, getTaskFileRawUrl,
  answerTask, approveTaskCall, pauseTaskContainer, resumeTaskContainer, getMessageInsights,
} from '../api';
import api from '../api';
import MarkdownRenderer from '../components/MarkdownRenderer';
import ProcessGraph, { TokenPill } from '../components/ProcessGraph';
import LiveRunStream from '../components/LiveRunStream';

import { PageContainer, PageHeader } from '../components/PageLayout';
import InlineEdit from '../components/InlineEdit';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
// ─── File tree helpers (shared shape with WorkspaceDetails) ─────────────────────
const buildFileTree = (paths) => {
  const root = { type: 'dir', children: {} };
  (paths || []).forEach((rawPath) => {
    const cleanPath = String(rawPath || '').trim();
    if (!cleanPath) return;
    const parts = cleanPath.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, idx) => {
      const isFile = idx === parts.length - 1;
      if (!node.children[part]) {
        node.children[part] = isFile
          ? { type: 'file', name: part, path: parts.join('/') }
          : { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });

  const toArray = (node, parentPath = '') => (
    Object.keys(node.children || {})
      .sort((a, b) => {
        const aNode = node.children[a];
        const bNode = node.children[b];
        if (aNode.type !== bNode.type) return aNode.type === 'dir' ? -1 : 1;
        return a.localeCompare(b);
      })
      .map((name) => {
        const child = node.children[name];
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        if (child.type === 'dir') {
          return { type: 'dir', name, path: fullPath, children: toArray(child, fullPath) };
        }
        return { type: 'file', name, path: child.path || fullPath };
      })
  );

  return toArray(root);
};

const parentDirPaths = (filePath) => {
  const parts = String(filePath || '').split('/').filter(Boolean);
  const dirs = [];
  for (let i = 1; i < parts.length; i += 1) {
    dirs.push(parts.slice(0, i).join('/'));
  }
  return dirs;
};

const isMarkdownPath = (p) => /\.(md|markdown|mdx)$/i.test(String(p || ''));

// ─── Constants ────────────────────────────────────────────────────────────────
const ALL_STATUSES = [
  { value: 'todo',        bg: 'bg-gray-100',    text: 'text-gray-600',   dot: 'bg-gray-400' },
  { value: 'ready',       bg: 'bg-blue-100',    text: 'text-blue-700',   dot: 'bg-blue-500' },
  { value: 'pending',     bg: 'bg-amber-100',   text: 'text-amber-700',  dot: 'bg-amber-500', readonly: true },
  { value: 'in_progress', bg: 'bg-yellow-100',  text: 'text-yellow-700', dot: 'bg-yellow-500' },
  { value: 'blocked',     bg: 'bg-red-100',     text: 'text-red-700',    dot: 'bg-red-500' },
  { value: 'awaiting_input', bg: 'bg-amber-100', text: 'text-amber-700',  dot: 'bg-amber-500', readonly: true },
  { value: 'awaiting_approval', bg: 'bg-amber-100', text: 'text-amber-700', dot: 'bg-amber-500', readonly: true },
  { value: 'stopped',     bg: 'bg-gray-100',    text: 'text-gray-500',   dot: 'bg-gray-400' },
  { value: 'resolved',    bg: 'bg-purple-100',  text: 'text-purple-700', dot: 'bg-purple-500' },
  { value: 'reviewing',   bg: 'bg-cyan-100',    text: 'text-cyan-700',   dot: 'bg-cyan-500',  readonly: true },
  { value: 'reviewed',    bg: 'bg-teal-100',    text: 'text-teal-700',   dot: 'bg-teal-500' },
  { value: 'done',        bg: 'bg-green-100',   text: 'text-green-700',  dot: 'bg-green-500' },
];

const PRIORITIES = [
  { value: 'critical', color: 'text-red-600',    bg: 'bg-red-50',     border: 'border-red-200' },
  { value: 'high',     color: 'text-orange-600', bg: 'bg-orange-50',  border: 'border-orange-200' },
  { value: 'medium',   color: 'text-yellow-600', bg: 'bg-yellow-50',  border: 'border-yellow-200' },
  { value: 'low',      color: 'text-blue-500',   bg: 'bg-blue-50',    border: 'border-blue-200' },
];

const statusCfg = (status) =>
  ALL_STATUSES.find(s => s.value === status) || ALL_STATUSES[0];

const priorityCfg = (p) =>
  PRIORITIES.find(x => x.value === p);

// ─── Small helpers ────────────────────────────────────────────────────────────
function StatusBadge({ status, size = 'sm' }) {
  const { t } = useI18n();
  const s = statusCfg(status);
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium ${
      size === 'xs' ? 'text-xs' : 'text-sm'
    } ${s.bg} ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {t(`taskStatus.${s.value}`)}
    </span>
  );
}

function PriorityBadge({ priority }) {
  const { t } = useI18n();
  if (!priority) return null;
  const p = priorityCfg(priority);
  if (!p) return null;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-medium ${p.color} ${p.bg} ${p.border}`}>
      <Flag className="w-3 h-3" />
      {t(`priority.${p.value}`)}
    </span>
  );
}

// Status dropdown with click-outside close
function StatusDropdown({ current, onChange }) {
  const { t } = useI18n();
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
        {t(`taskStatus.${s.value}`)}
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
              {t(`taskStatus.${opt.value}`)}
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
  const { t } = useI18n();
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
        {p ? t(`priority.${p.value}`) : t('taskDetails.noPriority')}
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-36 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
          >
            <Flag className="w-3 h-3" />
            {t('taskDetails.noPriority')}
            {!current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
          </button>
          {PRIORITIES.map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 ${opt.color}`}
            >
              <Flag className="w-3 h-3" />
              {t(`priority.${opt.value}`)}
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
  const { t } = useI18n();
  const navigate = useNavigate();
  if (!current) return null;
  return (
    <button
      onClick={() => navigate(`/workspaces/${encodeURIComponent(current)}`)}
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-indigo-200 bg-indigo-50 rounded-lg text-xs font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
      title={t('taskDetails.openWorkspace')}
    >
      <Folder className="w-3 h-3" />
      {current}
    </button>
  );
}

// Project selector dropdown
function ProjectSelector({ current, projects, onChange }) {
  const { t } = useI18n();
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
          title={t('taskDetails.openProject')}
        >
          <Folder className="w-3 h-3" />
          {currentProject.name}
        </button>
      ) : (
        <span className="flex items-center gap-1.5 px-2.5 py-1.5 text-indigo-400">
          <Folder className="w-3 h-3" />
          {t('taskDetails.noProject')}
        </span>
      )}
      <button
        onClick={() => setOpen(o => !o)}
        className="px-1.5 py-1.5 border-l border-indigo-200 hover:bg-indigo-100 transition-colors"
        title={t('taskDetails.changeProject')}
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
            <p className="px-3 py-2 text-xs text-gray-400 italic">{t('taskDetails.noProjectsFound')}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Add Subtask Modal ────────────────────────────────────────────────────────
function AddSubtaskModal({ parentId, parentWorkspace, onCreated, onCancel }) {
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
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              value={title}
              onChange={e => setTitle(e.target.value)}
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('taskDetails.description')} <span className="text-gray-400 font-normal">({t('common.optional')})</span></label>
            <textarea
              rows={3}
              placeholder={t('taskDetails.describeWhatNeedsToBe')}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none focus:ring-indigo-500 focus:border-indigo-500"
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

// ─── Subtask row ──────────────────────────────────────────────────────────────
function SubtaskRow({ st, onDelete, onStatusChange, onAssign, deleting }) {
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
            <User className="w-3 h-3" />
            {st.assigned_agent_type}
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

// ─── Result block ─────────────────────────────────────────────────────────────
// One agent result with its own Rendered / Raw view toggle.
function ResultBlock({ entry }) {
  const { t } = useI18n();
  const [view, setView] = useState('rendered');
  const text = String(entry.result ?? '');
  return (
    <div className="border border-indigo-100 rounded-lg p-3 bg-indigo-50">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-xs font-medium text-indigo-600">{entry.agent_id || 'Agent'}</span>
        {entry.timestamp && (
          <span className="text-xs text-gray-400">{new Date(entry.timestamp).toLocaleString()}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <div className="flex rounded-lg border border-indigo-200 overflow-hidden text-xs font-semibold">
            <button
              type="button"
              onClick={() => setView('rendered')}
              className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${view === 'rendered' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              <Eye className="w-3.5 h-3.5" /> {t('taskDetails.rendered')}
            </button>
            <button
              type="button"
              onClick={() => setView('raw')}
              className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-indigo-200 ${view === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            >
              <Code2 className="w-3.5 h-3.5" /> {t('taskDetails.raw')}
            </button>
          </div>
          {entry.run_id && (
            <span className="text-xs text-gray-300 font-mono">{entry.run_id.slice(0, 8)}</span>
          )}
        </div>
      </div>
      {view === 'rendered' ? (
        <div className="bg-white rounded-md p-3 border border-indigo-100">
          <MarkdownRenderer content={text} />
        </div>
      ) : (
        <pre className="text-xs text-gray-700 whitespace-pre-wrap">{text}</pre>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
const TaskDetails = () => {
  const { t } = useI18n();
  const toast = useToast();
  const { id } = useParams();
  const navigate = useNavigate();
  const { liveUpdates, selectedWorkspace } = useWorkspace();
  // The task's workspace is redundant when a specific workspace is selected in
  // the header — only surface it in the default (all-workspaces) view.
  const onDefaultWorkspace = !selectedWorkspace || selectedWorkspace === 'default';

  const [task, setTask] = useState(null);
  const [parentTask, setParentTask] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState('');
  const [executionLog, setExecutionLog] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [workspaceFiles, setWorkspaceFiles] = useState([]);

  // ── Files tab: preview state (mirrors WorkspaceDetails) ──────────────────
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [selectedFileIsPdf, setSelectedFileIsPdf] = useState(false);
  const [pdfViewMode, setPdfViewMode] = useState('render'); // 'render' | 'text'
  const [mdViewMode, setMdViewMode] = useState('rendered'); // 'rendered' | 'raw'
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');

  const [answerDraft, setAnswerDraft] = useState('');
  const [answerSubmitting, setAnswerSubmitting] = useState(false);
  const [approvalNote, setApprovalNote] = useState('');
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState('execution');
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [assignTarget, setAssignTarget] = useState(null); // null = parent task, subtask obj otherwise
  const [selectedAgent, setSelectedAgent] = useState('');


  const [deletingTask, setDeletingTask] = useState(false);
  const [deletingSubtasks, setDeletingSubtasks] = useState({});
  const [showAddSubtask, setShowAddSubtask] = useState(false);

  const [saving, setSaving] = useState(false);
  const [projects, setProjects] = useState([]);
  const [depTasks, setDepTasks] = useState([]);
  const [activityLog, setActivityLog] = useState([]);
  const [taskResults, setTaskResults] = useState([]);
  const [taskAssignmentMode, setTaskAssignmentMode] = useState('any');

  useEffect(() => {
    getSettings().then(r => setTaskAssignmentMode(r.data.task_assignment_mode || 'any')).catch(() => {});
  }, []);

  const fetchLogs = useCallback(async (runId) => {
    try { const r = await getMessageLogs(runId); setLogs(r.data.logs); } catch (e) { toast.error(t('taskDetails.errors.loadLogs'), errorDetail(e)); }
  }, [t, toast]);

  const fetchExecutionLog = useCallback(async (assignedRunId) => {
    try {
      const r = await getTaskExecutionLog(id);
      const entries = r.data.entries || [];
      setExecutionLog(entries);
      // No live assignment but past runs exist → show the latest run's logs.
      if (!assignedRunId && entries.length > 0) {
        const latest = entries[entries.length - 1];
        if (latest?.run_id) {
          setActiveRunId(latest.run_id);
          fetchLogs(latest.run_id);
        }
      }
    } catch (e) {
      toast.error(t('taskDetails.errors.executionLog'), errorDetail(e));
    }
  }, [id, fetchLogs, t, toast]);

  const fetchData = useCallback(async () => {
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

      // Load the tasks this one depends on (for the "Depends on" section)
      const depIds = taskResp.data.depends || [];
      if (depIds.length > 0) {
        Promise.all(depIds.map(d => getTask(d).then(r => r.data).catch(() => null)))
          .then(list => setDepTasks(list.filter(Boolean)));
      } else {
        setDepTasks([]);
      }

      if (taskResp.data.assigned_agent_run_id) {
        setActiveRunId(taskResp.data.assigned_agent_run_id);
        fetchLogs(taskResp.data.assigned_agent_run_id);
      } else {
        setActiveRunId(null);
        setLogs('');
      }
      // Always fetch execution log — sidecar file persists independently of
      // assigned_agent_run_id. When the task has no active assignment (run
      // completed and assignment cleared), fall back to the most recent run
      // from the execution log so the Logs / Results tabs still have content.
      fetchExecutionLog(taskResp.data.assigned_agent_run_id);

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
  }, [fetchExecutionLog, fetchLogs, id]);

  useEffect(() => {
    fetchData();
  }, [fetchData, id, liveUpdates]);
  // Detail page: refetch (debounced) on task or run changes.
  useLiveRefetch(fetchData, { enabled: liveUpdates });

  // ── Execution flow (unified with the Chat process panel) ──────────────────
  // Per-run insights carry the same message_runs shape the Chat page renders,
  // so the Execution tab can show one continuous flow across all agent runs
  // instead of flat per-run records.
  const [flowRuns, setFlowRuns] = useState([]);
  const [flowLoading, setFlowLoading] = useState(false);
  const insightsCacheRef = useRef({}); // run_id -> { status, message_runs }

  useEffect(() => {
    let cancelled = false;
    const entries = (executionLog || []).filter((e) => e.run_id);
    if (!entries.length) { setFlowRuns([]); setFlowLoading(false); return undefined; }

    const load = async () => {
      if (!Object.keys(insightsCacheRef.current).length) setFlowLoading(true);
      await Promise.all(entries.map(async (entry) => {
        const cached = insightsCacheRef.current[entry.run_id];
        // Finished runs are immutable — fetch once. Running runs refresh on
        // every execution-log update so live tool calls show up.
        if (cached && cached.status !== 'running' && entry.status !== 'running') return;
        try {
          const r = await getMessageInsights(entry.run_id);
          insightsCacheRef.current[entry.run_id] = {
            status: entry.status,
            message_runs: r.data?.message_runs || [],
          };
        } catch { /* insights may not exist yet for a just-started run */ }
      }));
      if (cancelled) return;
      const merged = entries.flatMap((entry) => {
        const runs = insightsCacheRef.current[entry.run_id]?.message_runs || [];
        const base = runs.length ? runs : [{
          message_id: entry.run_id,
          run_id: entry.run_id,
          agent_id: entry.agent_id,
          timestamp: entry.started_at,
          input: '',
          output: '',
          tools: [],
          inbound_tokens: entry.inbound_tokens,
          outbound_tokens: entry.outbound_tokens,
          total_tokens: entry.total_tokens,
          duration_ms: 0,
        }];
        return base.map((mr) => ({
          ...mr,
          timestamp: mr.timestamp || entry.started_at,
          status: entry.status,
          channel: entry.channel,
          error: mr.error || entry.error,
        }));
      });
      setFlowRuns(merged);
      setFlowLoading(false);
    };
    load();
    return () => { cancelled = true; };
  }, [executionLog]);

  // Size the flow so the page content fits the viewport exactly — the flow
  // takes all the height left below the header card and the page itself never
  // scrolls (the run timeline scrolls inside the flow instead). Shrink by the
  // page's overflow; grow only to close a visible gap below the pane. Never
  // grow in response to scrolling, which would extend the page and re-create
  // the scrollbar.
  const flowScrollRef = useRef(null);
  const [flowHeight, setFlowHeight] = useState(null);

  useEffect(() => {
    if (activeTab !== 'execution') return undefined;
    const compute = () => {
      const node = flowScrollRef.current;
      const pane = node?.parentElement; // the card wrapping the flow
      if (!node || !pane) return;
      const main = node.closest('main') || document.scrollingElement;
      const padBottom = parseFloat(getComputedStyle(main).paddingBottom) || 0;
      const overflow = main.scrollHeight - main.clientHeight;
      const gap = main.getBoundingClientRect().bottom - padBottom - pane.getBoundingClientRect().bottom;
      const h = node.getBoundingClientRect().height - overflow + Math.max(0, gap);
      setFlowHeight(Math.max(240, Math.floor(h)));
    };
    compute();
    // Capture-phase listener sees the layout <main> scrolling (e.g. the flow's
    // own scroll-to-latest on mount), so any overflow is corrected right away.
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [activeTab, flowLoading, flowRuns.length]);

  // ── Files tab: load/preview ────────────────────────────────────────────────
  const loadFileContent = useCallback(async (path) => {
    if (!path) return;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    try {
      const resp = await getTaskFileContent(id, path);
      setSelectedFileContent(resp.data?.content || '');
      setSelectedFileSize(Number(resp.data?.size || 0));
      setSelectedFileIsPdf(!!resp.data?.is_pdf);
      setPdfViewMode('render');
      setMdViewMode('rendered');
    } catch (e) {
      const detail = e?.response?.data?.detail || t('taskDetails.errors.fileContent');
      setFileContentError(detail);
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setSelectedFileIsPdf(false);
    } finally {
      setFileContentLoading(false);
    }
  }, [id, t]);

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  };

  const fileTree = useMemo(() => buildFileTree(workspaceFiles), [workspaceFiles]);

  // Auto-expand folders and select the first file when the list changes.
  useEffect(() => {
    if (!workspaceFiles.length) {
      setSelectedFilePath('');
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setExpandedFolders(new Set());
      return;
    }
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      workspaceFiles.forEach((p) => parentDirPaths(p).forEach((dir) => next.add(dir)));
      return next;
    });
    if (!selectedFilePath || !workspaceFiles.includes(selectedFilePath)) {
      loadFileContent(workspaceFiles[0]);
    }
  }, [workspaceFiles, loadFileContent, selectedFilePath]);

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleFolder(node.path)}
            className="w-full flex items-center gap-1.5 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 rounded text-left"
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
            title={node.path}
          >
            {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
            {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
            <span className="truncate">{node.name}</span>
          </button>
          {open && node.children?.length > 0 && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }
    const isSelected = node.path === selectedFilePath;
    return (
      <button
        key={node.path}
        type="button"
        onClick={() => {
          setExpandedFolders((prev) => {
            const next = new Set(prev);
            parentDirPaths(node.path).forEach((dir) => next.add(dir));
            return next;
          });
          loadFileContent(node.path);
        }}
        className={`w-full flex items-center gap-1.5 px-2 py-1 text-sm rounded text-left ${
          isSelected ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700 hover:bg-gray-50'
        }`}
        style={{ paddingLeft: `${depth * 14 + 28}px` }}
        title={node.path}
      >
        <FileText className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  });

  // ── Patch helper ─────────────────────────────────────────────────────────
  const patch = async (fields, taskId = id) => {
    setSaving(true);
    try {
      await updateTask(taskId, fields);
      fetchData();
    } catch (err) {
      alert(`${t('taskDetails.errors.update')}: ` + (err.response?.data?.detail || err.message));
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
      alert(`${t('taskDetails.errors.assignAgent')}: ` + (err.response?.data?.detail || err.message));
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

  const handlePauseContainer = async () => {
    try { await pauseTaskContainer(id); fetchData(); }
    catch (err) { alert(`${t('taskDetails.errors.pause')}: ` + (err.response?.data?.detail || err.message)); }
  };

  const handleResumeContainer = async () => {
    try { await resumeTaskContainer(id); fetchData(); }
    catch (err) { alert(`${t('taskDetails.errors.resume')}: ` + (err.response?.data?.detail || err.message)); }
  };

  const handleAnswerTask = async () => {
    const text = answerDraft.trim();
    if (!text) return;
    setAnswerSubmitting(true);
    try {
      const resp = await answerTask(id, text);
      if (resp.data?.run_id) setActiveRunId(resp.data.run_id);
      setAnswerDraft('');
      fetchData();
    } catch (err) {
      alert(`${t('taskDetails.errors.submitAnswer')}: ` + (err.response?.data?.detail || err.message));
    } finally {
      setAnswerSubmitting(false);
    }
  };

  // Decide on the tool call the agent stopped for. Approving records that exact
  // call (tool + arguments) as allowed once and resumes the agent; denying
  // resumes it with the refusal and the note, so it can pick another route.
  const handleApprovalDecision = async (approved) => {
    setApprovalSubmitting(true);
    try {
      const resp = await approveTaskCall(id, approved, approvalNote.trim());
      if (resp.data?.run_id) setActiveRunId(resp.data.run_id);
      setApprovalNote('');
      fetchData();
    } catch (err) {
      alert(`${t('taskDetails.errors.submitApproval')}: ` + (err.response?.data?.detail || err.message));
    } finally {
      setApprovalSubmitting(false);
    }
  };

  const handleApproveAssignment = async () => {
    try { await approveAssignment(id); fetchData(); }
    catch (err) { alert(`${t('taskDetails.errors.approve')}: ` + (err.response?.data?.detail || err.message)); }
  };

  const handleRejectAssignment = async () => {
    try { await rejectAssignment(id); fetchData(); }
    catch (err) { alert(`${t('taskDetails.errors.reject')}: ` + (err.response?.data?.detail || err.message)); }
  };

  const handleRunDecomposer = async () => {
    try {
      const resp = await runDecomposer(id, {});
      if (resp.data?.run_id) { setActiveRunId(resp.data.run_id); fetchData(); }
    } catch (err) {
      alert(`${t('common.error')}: ` + (err.response?.data?.detail || err.message));
    }
  };

  const handleDeleteTask = async () => {
    if (!task) return;
    const count = (task.subtasks || []).length;
    if (!window.confirm(count > 0
      ? `Delete this task and its ${count} subtask(s)? This cannot be undone.`
      : t('taskDetails.confirmDeleteTask'))
    ) return;
    setDeletingTask(true);
    try { await deleteTask(id, { cascade: true }); navigate('/tasks'); }
    catch (err) { alert(`${t('common.error')}: ` + (err.response?.data?.detail || err.message)); }
    finally { setDeletingTask(false); }
  };

  const handleDeleteSubtask = async (subtaskId) => {
    if (!window.confirm(t('taskDetails.confirmDeleteSubtask'))) return;
    setDeletingSubtasks(prev => ({ ...prev, [subtaskId]: true }));
    try { await deleteTask(subtaskId, { cascade: true }); fetchData(); }
    catch (err) { alert(`${t('common.error')}: ` + (err.response?.data?.detail || err.message)); }
    finally { setDeletingSubtasks(prev => ({ ...prev, [subtaskId]: false })); }
  };

  // ── Derived ───────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="flex items-center justify-center py-16 text-gray-400">
      <Loader className="w-6 h-6 animate-spin mr-2" /> {t('taskDetails.loadingTask')}
    </div>
  );
  if (!task) return <div className="text-center py-10 text-gray-500">{t('taskDetails.taskNotFound')}</div>;

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
  // Results are derived per-task (getTaskResult by task id), not from run insights.
  const hasResults = taskResults.length > 0;

  return (
    <PageContainer>
      <PageHeader
        icon={CheckSquare}
        backTo="/tasks"
        backLabel={t('taskDetails.tasks')}
        title={
          <InlineEdit
            value={task.title}
            onSave={v => patch({ title: v })}
            className="text-2xl font-bold"
          />
        }
        badges={task.key && (
          <span className="text-sm font-semibold text-gray-400 flex-shrink-0">{task.key}</span>
        )}
        actions={<>
            {subtasks.length > 0 && task.status === 'in_progress' && (
              <button onClick={handlePauseContainer} className="flex items-center gap-1.5 px-3 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600">
                <Pause className="w-4 h-4" /> {t('taskDetails.pause')}
              </button>
            )}
            {subtasks.length > 0 && task.status === 'stopped' && (
              <button onClick={handleResumeContainer} className="flex items-center gap-1.5 px-3 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
                <Play className="w-4 h-4" /> {t('taskDetails.resume')}
              </button>
            )}
            {task.agent_state === 'pending_approval' ? (
              <>
                <button onClick={handleApproveAssignment} className="flex items-center gap-1.5 px-3 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700">
                  <ThumbsUp className="w-4 h-4" /> {t('taskDetails.approve')}
                </button>
                <button onClick={handleRejectAssignment} className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 rounded-lg text-sm hover:bg-red-100">
                  <ThumbsDown className="w-4 h-4" /> {t('taskDetails.reject')}
                </button>
              </>
            ) : task.agent_state === 'running' ? (
              <button onClick={handleStopAgent} className="flex items-center gap-1.5 px-3 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700">
                <Square className="w-4 h-4" /> {t('taskDetails.stopAgent')}
              </button>
            ) : (
              <button onClick={() => openAssign()} className="flex items-center gap-1.5 px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">
                <Play className="w-4 h-4" /> {t('taskDetails.assignAgent2')}
              </button>
            )}
            {task.created_by === 'user' && (
              <button onClick={handleRunDecomposer} className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700">
                <Split className="w-4 h-4" /> {t('taskDetails.decompose')}
              </button>
            )}
            <button onClick={handleDeleteTask} disabled={deletingTask} className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 rounded-lg text-sm hover:bg-red-100 disabled:opacity-50">
              <Trash2 className="w-4 h-4" />
              {deletingTask ? 'Deleting…' : 'Delete'}
            </button>
        </>}
      >
        <div className="flex items-center flex-wrap gap-2 mt-3">
          <StatusDropdown current={task.status} onChange={v => patch({ status: v })} />
          <PriorityDropdown current={task.priority} onChange={v => patch({ priority: v })} />
          <ProjectSelector
            current={task.project_id || null}
            projects={projects}
            onChange={v => patch({ project_id: v })}
          />
          {onDefaultWorkspace && <WorkspaceBadge current={task.workspace} />}
          {task.created_by === 'external' && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-violet-50 text-violet-600 border border-violet-200">
              {t('taskDetails.external')}
            </span>
          )}
          {saving && <Loader className="w-3.5 h-3.5 text-gray-400 animate-spin" />}
        </div>
      </PageHeader>

      {/* Main task card */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 mb-6">
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
                {t('taskDetails.parentTask')}
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
          {/* Dependencies — tasks that must complete before this one runs */}
          {depTasks.length > 0 && (
            <div className="flex items-start gap-3">
              <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0 mt-0.5">
                <GitBranch className="w-3.5 h-3.5" />
                {t('taskDetails.dependsOn')}
              </div>
              <div className="flex flex-col gap-1 min-w-0">
                {depTasks.map(dep => (
                  <Link key={dep.id} to={`/tasks/${dep.id}`} className="flex items-center gap-2 group hover:no-underline min-w-0">
                    <StatusBadge status={dep.status} size="xs" />
                    {dep.key && <span className="text-xs font-semibold text-gray-400 flex-shrink-0">{dep.key}</span>}
                    <span className="text-sm font-medium text-gray-700 group-hover:text-indigo-600 truncate transition-colors">
                      {dep.title}
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-4 flex-wrap">
          {task.key && <span className="text-xs text-gray-400">{t('taskDetails.key')} <span className="font-semibold text-gray-500">{task.key}</span></span>}
          <span className="text-xs text-gray-400">{t('taskDetails.id')} <span className="">{task.id}</span></span>
          <span className="text-xs text-gray-400">{t('taskDetails.createdAt')}: {new Date(task.created_at).toLocaleString()}</span>
          {task.assigned_agent_type && (
            <span className="flex items-center gap-1 text-xs text-gray-500">
              <User className="w-3.5 h-3.5" /> {task.assigned_agent_type}
              <span className={`ml-1 px-1.5 py-0.5 rounded text-xs font-medium ${
                task.agent_state === 'running' ? 'bg-blue-100 text-blue-700' :
                task.agent_state === 'completed' ? 'bg-green-100 text-green-700' :
                task.agent_state === 'pending_approval' ? 'bg-amber-100 text-amber-700' :
                task.agent_state === 'pending' ? 'bg-yellow-100 text-yellow-700' :
                'bg-gray-100 text-gray-600'
              }`}>{task.agent_state === 'pending_approval' ? t('taskDetails.awaitingApproval') : task.agent_state}</span>
            </span>
          )}
          </div>
        </div>

        {/* Subtask progress bar */}
        {subtasks.length > 0 && (
          <div className="mt-4">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>{t('taskDetails.progress')}</span>
              <span>{t('taskDetails.subtasksDone', { done: doneCount, total: subtasks.length, pct: progress })}</span>
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
              <AlertCircle className="w-4 h-4" /> {t('taskDetails.blocked')}
            </p>
            <p className="text-sm text-red-700 mt-0.5">{task.blocked_reason}</p>
          </div>
        )}

        {/* Awaiting input — the agent paused to ask a question */}
        {task.status === 'awaiting_input' && (
          <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
            <p className="text-sm text-amber-800 font-semibold flex items-center gap-1.5">
              <HelpCircle className="w-4 h-4" /> {t('taskDetails.theAgentNeedsYourInput')}
            </p>
            <p className="text-sm text-amber-900 mt-1 whitespace-pre-wrap">
              {task.pending_question?.question || t('taskDetails.waitingForAnswer')}
            </p>
            {Array.isArray(task.pending_question?.choices) && task.pending_question.choices.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-3">
                {task.pending_question.choices.map((c, i) => (
                  <button
                    key={i}
                    type="button"
                    disabled={answerSubmitting}
                    onClick={() => { setAnswerDraft(c); }}
                    className={`px-3 py-1 rounded-full border text-sm transition-colors disabled:opacity-50 ${
                      answerDraft === c
                        ? 'border-amber-500 bg-amber-200 text-amber-900'
                        : 'border-amber-300 bg-white text-amber-800 hover:bg-amber-100'
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            )}
            <div className="flex items-end gap-2 mt-3">
              <textarea
                value={answerDraft}
                onChange={(e) => setAnswerDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); handleAnswerTask(); }
                }}
                rows={2}
                placeholder={t('taskDetails.typeYourAnswerCtrlEnter')}
                className="flex-1 px-3 py-2 text-sm border border-amber-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-300 resize-y"
              />
              <button
                type="button"
                disabled={answerSubmitting || !answerDraft.trim()}
                onClick={handleAnswerTask}
                className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
              >
                {answerSubmitting ? t('taskDetails.sending') : t('taskDetails.sendAndResume')}
              </button>
            </div>
          </div>
        )}

        {/* Awaiting approval — the agent stopped before a tool call that needs a human yes */}
        {task.status === 'awaiting_approval' && (
          <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
            <p className="text-sm text-amber-800 font-semibold flex items-center gap-1.5">
              <ShieldQuestion className="w-4 h-4" /> {t('taskDetails.theAgentNeedsApproval')}
            </p>
            <p className="text-sm text-amber-900 mt-1">
              <code className="px-1.5 py-0.5 rounded bg-amber-100 font-mono text-xs">
                {task.pending_approval?.tool || '—'}
              </code>
            </p>
            {task.pending_approval?.reason && (
              <p className="text-sm text-amber-900 mt-1 whitespace-pre-wrap">{task.pending_approval.reason}</p>
            )}
            <pre className="mt-2 p-2 bg-white border border-amber-200 rounded text-xs text-gray-800 overflow-x-auto">
              {JSON.stringify(task.pending_approval?.input ?? {}, null, 2)}
            </pre>
            <div className="flex items-center gap-2 mt-3">
              <input
                type="text"
                value={approvalNote}
                onChange={(e) => setApprovalNote(e.target.value)}
                placeholder={t('taskDetails.approvalNotePlaceholder')}
                className="flex-1 px-3 py-2 text-sm border border-amber-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-300"
              />
              <button
                type="button"
                disabled={approvalSubmitting}
                onClick={() => handleApprovalDecision(true)}
                className="px-4 py-2 rounded-lg bg-amber-500 text-white text-sm font-semibold hover:bg-amber-600 disabled:opacity-50"
              >
                {approvalSubmitting ? t('taskDetails.sending') : t('taskDetails.approveCall')}
              </button>
              <button
                type="button"
                disabled={approvalSubmitting}
                onClick={() => handleApprovalDecision(false)}
                className="px-4 py-2 rounded-lg border border-amber-300 bg-white text-amber-800 text-sm font-semibold hover:bg-amber-100 disabled:opacity-50"
              >
                {t('taskDetails.denyCall')}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Live agent output for this task's session. Renders nothing until the
          session channel produces events, so a task with nothing running keeps
          its previous layout. */}
      <LiveRunStream sessionId={task.session_id} title={t('taskDetails.liveAgentOutput')} className="mb-6" />

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
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
              className={`inline-flex items-center gap-1.5 px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
                activeTab === tid
                  ? 'border-indigo-600 text-indigo-700'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
              aria-current={activeTab === tid ? 'page' : undefined}
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
            <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.subtasks')}</h3>
            <button
              onClick={() => setShowAddSubtask(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
            >
              <Plus className="w-4 h-4" /> {t('taskDetails.addSubtask')}
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
          {/* Execution flow pane — same agent-process view as the Chat page */}
          <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
            <div className="flex items-center justify-between gap-2 mb-4 flex-wrap">
              <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.executionFlow')}</h3>
              {flowRuns.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  <TokenPill
                    label={t('taskDetails.taskIn')}
                    value={flowRuns.reduce((s, mr) => s + (Number(mr.inbound_tokens) || 0), 0)}
                  />
                  <TokenPill
                    label={t('taskDetails.taskOut')}
                    value={flowRuns.reduce((s, mr) => s + (Number(mr.outbound_tokens) || 0), 0)}
                  />
                  <TokenPill
                    label={t('taskDetails.taskTotal')}
                    value={flowRuns.reduce((s, mr) => s + (Number(mr.total_tokens) || ((Number(mr.inbound_tokens) || 0) + (Number(mr.outbound_tokens) || 0))), 0)}
                  />
                </div>
              )}
            </div>
            {flowLoading ? (
              <div className="flex items-center gap-2 text-sm text-gray-400 py-4">
                <Loader className="w-4 h-4 animate-spin" /> {t('taskDetails.loadingExecutionFlow')}
              </div>
            ) : flowRuns.length > 0 ? (
              <div
                ref={flowScrollRef}
                className="overflow-auto pr-1"
                style={{ height: flowHeight ? `${flowHeight}px` : 'calc(100vh - 240px)' }}
              >
                <ProcessGraph
                  key={flowRuns.map((mr, idx) => `${mr.message_id || mr.run_id || idx}`).join('|')}
                  messageRuns={flowRuns}
                  titleByAgent
                />
              </div>
            ) : (
              <p className="text-sm text-gray-400 italic">{t('taskDetails.noAgentRunsRecordedYet')}</p>
            )}
          </div>
        </div>
      )}

      {/* ── Activity tab ── */}
      {activeTab === 'activity' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <h3 className="text-base font-semibold text-gray-800 mb-4">{t('taskDetails.taskActivity')}</h3>
          {activityItems.length > 0 ? (
            <div className="space-y-3">
              {activityItems.map((item, idx) => {
                const ts = item?.timestamp ? new Date(item.timestamp).toLocaleString() : t('taskDetails.unknownTime');
                const text = item?.message || item?.type || t('taskDetails.activityUpdate');
                return (
                  <div key={`${item?.timestamp || 't'}-${idx}`} className="border-l-2 border-indigo-200 pl-3 py-1">
                    <div className="text-xs text-gray-400">{ts}</div>
                    <div className="text-sm text-gray-700">{text}</div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">{t('taskDetails.noActivityEntriesYet')}</p>
          )}
        </div>
      )}

      {/* ── Results tab ── */}
      {activeTab === 'results' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-gray-800">{t('taskDetails.agentResults')}</h3>
            {activeRunId && <span className="text-xs text-gray-400">{t('taskDetails.run')}: {activeRunId}</span>}
          </div>
          {hasResults ? (
            <div className="space-y-3">
              {[...taskResults].reverse().map((entry, idx) => (
                <ResultBlock key={entry.run_id || idx} entry={entry} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">{t('taskDetails.noResultsCapturedForThis')}</p>
          )}
        </div>
      )}

      {/* ── Logs tab ── */}
      {activeTab === 'logs' && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden flex flex-col h-[500px]">
          <div className="bg-gray-800 px-4 py-2.5 flex items-center justify-between">
            <span className="flex items-center gap-2 text-gray-300 text-sm font-medium">
              <Terminal className="w-4 h-4" /> {t('taskDetails.agentLogs')}
            </span>
            {activeRunId && (
              <span className="text-xs text-gray-500">{t('taskDetails.run')}: {activeRunId.slice(0, 8)}</span>
            )}
          </div>
          <div className="p-4 flex-1 overflow-auto text-xs text-green-400 bg-black leading-relaxed">
            {logs
              ? <pre className="whitespace-pre-wrap">{logs}</pre>
              : <p className="text-gray-600 italic">{t('taskDetails.noLogsAvailable')}</p>
            }
          </div>
        </div>
      )}

      {/* ── Files tab ── */}
      {activeTab === 'files' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
            <Folder className="w-5 h-5" /> {t('taskDetails.filesChangedByTask')}
          </h3>
          {workspaceFiles.length ? (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 h-[500px]">
              {/* Tree */}
              <div className="lg:col-span-4 border border-gray-200 rounded-lg p-2 overflow-y-auto min-h-0">
                {renderFileNodes(fileTree)}
              </div>
              {/* Preview */}
              <div className="lg:col-span-8 border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
                <div className="px-4 py-2 border-b bg-gray-50 flex items-center justify-between gap-2 shrink-0">
                  <div className="min-w-0">
                    <div className="text-xs text-gray-500">{t('taskDetails.selectedFile')}</div>
                    <div className="text-sm text-gray-700 truncate flex items-center gap-2">
                      <span className="truncate">{selectedFilePath || '-'}</span>
                      {selectedFileIsPdf && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-100 text-red-600 shrink-0">
                          <FileText className="w-2.5 h-2.5" /> {t('taskDetails.pdf')}
                        </span>
                      )}
                    </div>
                    {selectedFileSize > 0 && (
                      <div className="text-xs text-gray-400 mt-0.5">{t('taskDetails.bytes', { count: selectedFileSize })}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {selectedFileIsPdf && (
                      <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                        <button
                          type="button"
                          onClick={() => setPdfViewMode('render')}
                          className={`px-2.5 py-1 transition-colors ${pdfViewMode === 'render' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          {t('taskDetails.render')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPdfViewMode('text')}
                          className={`px-2.5 py-1 transition-colors border-l border-gray-200 ${pdfViewMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          {t('taskDetails.text')}
                        </button>
                      </div>
                    )}
                    {!selectedFileIsPdf && isMarkdownPath(selectedFilePath) && (
                      <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                        <button
                          type="button"
                          onClick={() => setMdViewMode('rendered')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${mdViewMode === 'rendered' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          <Eye className="w-3.5 h-3.5" /> {t('taskDetails.rendered')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setMdViewMode('raw')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-gray-200 ${mdViewMode === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          <Code2 className="w-3.5 h-3.5" /> {t('taskDetails.raw')}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                <div className={`${selectedFileIsPdf && pdfViewMode === 'render' ? '' : 'p-4'} flex-1 min-h-0 overflow-auto`}>
                  {fileContentLoading ? (
                    <p className="text-sm text-gray-500 p-4">{t('taskDetails.loadingFileContent')}</p>
                  ) : fileContentError ? (
                    <p className="text-sm text-red-600 p-4">{fileContentError}</p>
                  ) : selectedFilePath && selectedFileIsPdf && pdfViewMode === 'render' ? (
                    <iframe
                      title={selectedFilePath}
                      src={getTaskFileRawUrl(id, selectedFilePath)}
                      className="w-full h-full border-0"
                    />
                  ) : selectedFilePath && isMarkdownPath(selectedFilePath) && mdViewMode === 'rendered' ? (
                    <MarkdownRenderer content={selectedFileContent} />
                  ) : selectedFilePath ? (
                    <pre className="text-xs text-gray-800 whitespace-pre-wrap break-words">{selectedFileContent}</pre>
                  ) : (
                    <p className="text-sm text-gray-500">{t('taskDetails.selectAFileToPreview')}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400 italic">{t('taskDetails.noFilesWereCreatedOr')}</p>
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
                {assignTarget ? t('taskDetails.assignAgentTo', { title: assignTarget.title }) : t('taskDetails.assignAgentToTask')}
              </h3>
              <button onClick={() => { setShowAssignModal(false); setAssignTarget(null); }} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="mb-2 flex items-center gap-3 text-xs text-gray-400">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> {t('taskDetails.nodeRunning')}</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-300 inline-block" /> {t('taskDetails.noNode')}</span>
              {taskAssignmentMode === 'nodes_only' && (
                <span className="ml-auto text-amber-600 font-medium">{t('taskDetails.nodesOnlyModeAgentsWithout')}</span>
              )}
            </div>

            <div className="mb-5 grid grid-cols-1 gap-2 max-h-72 overflow-y-auto pr-1">
              {agents.length === 0 && (
                <p className="text-sm text-gray-400 italic py-2">{t('taskDetails.noAgentsAvailableForThis')}</p>
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
                    title={disabled ? t('taskDetails.noRunningNodeHint') : undefined}
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
                      <span className="block text-xs text-gray-400 truncate">{a.id}{!hasNode && ` · ${t('taskDetails.noRunningNode')}`}</span>
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
              >{t('taskDetails.cancel')}</button>
              <button
                onClick={handleAssignAgent}
                disabled={!selectedAgent}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >{t('taskDetails.startExecution')}</button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default TaskDetails;

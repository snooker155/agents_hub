import {
  AlertCircle, CheckSquare, Clock, FileText, Folder, History, Layers, Loader, Pause, Play, Radio,
  Split, Square, Terminal, ThumbsDown, ThumbsUp, Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import api, {
  answerTask, approveAssignment, approveTaskCall, deleteTask, getAgents,
  getMessageInsights, getMessageLogs, getProjects, getTask, getTaskActivityLog,
  getTaskExecutionLog, getTaskFileContent, getTaskResult, getTasks,
  pauseTaskContainer, rejectAssignment, resumeTaskContainer, runDecomposer, stopAgent,
  updateTask,
} from '../api';
import InlineEdit from '../components/InlineEdit';
import LiveRunStream from '../components/LiveRunStream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import PageLoader from '../components/PageLoader';
import { useLiveRefetch, useStream } from '../components/stream';
import { DecomposeModal } from '../components/task/DecomposeProgress';
import DependsEditor from '../components/task/DependsEditor';
import { newDecomposeChannel } from '../components/task/decomposeChannel';
import { forwardBack } from '../components/task/taskBack';
import { AddSubtaskModal } from '../components/task/SubtaskParts';
import AssignExecutorModal from '../components/task/AssignExecutorModal';
import {
  DueDateField, PriorityDropdown, ProjectSelector, StatusDropdown, WorkspaceBadge,
} from '../components/task/TaskFields';
import { TaskFilesTab } from '../components/task/TaskFilesTab';
import { TaskKeyRow, TaskLinksRow } from '../components/task/TaskMetaRow';
import TaskOutcomeCard from '../components/task/TaskOutcomeCard';
import { AwaitingInputCard, BudgetPauseCard, ToolApprovalCard } from '../components/task/TaskPendingCards';
import {
  ActivityTab, ExecutionTab, LogsTab, ResultsTab, SubtasksTab,
} from '../components/task/TaskTabPanels';
import { buildFileTree, confirmStatusChange, parentDirPaths } from '../components/task/taskUtils';
import { errorDetail, useToast } from '../components/toast';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

// A stable empty list, so memoised children do not see a new array per render.
const EMPTY_FLOW_RUNS = [];
// The outcome grader's runs (tasks/outcome.py GRADER_AGENT_ID).
const GRADER_AGENT_ID = 'outcome_grader';

const TaskDetails = () => {
  const { t } = useI18n();
  const toast = useToast();
  const { id } = useParams();
  const navigate = useNavigate();
  // Opened from a project's task board: the back link returns there, and the
  // tasks this page links to keep that way back (task/taskBack.js).
  const backState = forwardBack(useLocation());
  const backTo = backState?.backTo;
  const { clientId } = useStream();
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
  const [decomposeChannel, setDecomposeChannel] = useState(null);
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
  // The new cap offered on a budget pause card, seeded once at twice the
  // limit the run just hit (a plausible next stop, not a guess the operator
  // has to type from scratch) and left alone after that so an edit sticks.
  // `null` means the operator has not edited it, so the seed applies.
  const [budgetCapEdit, setBudgetCapDraft] = useState(null);
  const budgetPending = task?.status === 'awaiting_approval' && task?.pending_approval?.kind === 'budget';
  const budgetLimit = Number(task?.pending_approval?.limit_usd) || 0;
  const budgetCapDraft = budgetPending
    ? (budgetCapEdit ?? String(budgetLimit > 0 ? budgetLimit * 2 : 10))
    : '';
  // Leaving the budget pause forgets an edit, so the next pause seeds again.
  if (!budgetPending && budgetCapEdit !== null) setBudgetCapDraft(null);
  const [activeTab, setActiveTab] = useState('live');
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [assignTarget, setAssignTarget] = useState(null); // null = parent task, subtask obj otherwise


  const [deletingTask, setDeletingTask] = useState(false);
  const [deletingSubtasks, setDeletingSubtasks] = useState({});
  const [showAddSubtask, setShowAddSubtask] = useState(false);

  const [saving, setSaving] = useState(false);
  const [projects, setProjects] = useState([]);
  const [depTasks, setDepTasks] = useState([]);
  // The workspace's tasks: what waits for this one, the dependencies' names
  // on the subtask rows, and the choice in the dependency editor.
  const [workspaceTasks, setWorkspaceTasks] = useState([]);
  const [editingDepends, setEditingDepends] = useState(false);
  const [activityLog, setActivityLog] = useState([]);
  const [taskResults, setTaskResults] = useState([]);

  // The run the Logs tab shows when the reader picked one there; otherwise
  // the task's current or latest work run. Kept in a ref so a live refetch
  // refreshes the picked run's log instead of switching back.
  const [logRunId, setLogRunId] = useState(null);
  const logRunRef = useRef(null);
  const fetchLogs = useCallback(async (runId) => {
    const target = logRunRef.current || runId;
    try { const r = await getMessageLogs(target); setLogs(r.data.logs); } catch (e) { toast.error(t('taskDetails.errors.loadLogs'), errorDetail(e)); }
  }, [t, toast]);
  const pickLogRun = useCallback((runId) => {
    logRunRef.current = runId || null;
    setLogRunId(runId || null);
    fetchLogs(runId);
  }, [fetchLogs]);

  const fetchExecutionLog = useCallback(async (assignedRunId) => {
    try {
      const r = await getTaskExecutionLog(id);
      const entries = r.data.entries || [];
      setExecutionLog(entries);
      // No live assignment but past runs exist → show the latest work run's
      // logs. A grading is a model call inside the hub, with no process log
      // of its own (its input and answer are on the Execution tab).
      const workRuns = entries.filter((e) => e.run_id && e.agent_id !== GRADER_AGENT_ID);
      if (!assignedRunId && workRuns.length > 0) {
        const latest = workRuns[workRuns.length - 1];
        if (latest?.run_id) {
          setActiveRunId(latest.run_id);
          fetchLogs(latest.run_id);
        }
      }
    } catch (e) {
      toast.error(t('taskDetails.errors.executionLog'), errorDetail(e));
    }
  }, [id, fetchLogs, t, toast]);

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  const fetchData = useCallback(() => (
    Promise.all([
      getTask(id),
      getProjects().catch(() => ({ data: [] })),
    ])
      .then(([taskResp, projectsResp]) => {
        setTask(taskResp.data);
        setProjects(projectsResp.data || []);
        return getAgents(taskResp.data.workspace || undefined)
          .catch(() => ({ data: [] }))
          .then((agentsResp) => {
            setAgents(agentsResp.data);

            if (taskResp.data.parent_id) {
              getTask(taskResp.data.parent_id).then(r => setParentTask(r.data)).catch(() => {});
            } else {
              setParentTask(null);
            }

            getTasks(taskResp.data.workspace || undefined)
              .then(r => setWorkspaceTasks(Array.isArray(r.data) ? r.data : (r.data?.items || [])))
              .catch(() => setWorkspaceTasks([]));

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
          });
      })
      .catch((err) => console.error('Error fetching task details:', err))
      .finally(() => setLoading(false))
  ), [fetchExecutionLog, fetchLogs, id]);

  useEffect(() => {
    fetchData();
  }, [fetchData, id, liveUpdates]);
  // Detail page: refetch (debounced) on task or run changes.
  useLiveRefetch(fetchData, { enabled: liveUpdates });

  // ── Execution flow (unified with the Chat process panel) ──────────────────
  // Per-run insights carry the same message_runs shape the Chat page renders,
  // so the Execution tab can show one continuous flow across all agent runs
  // instead of flat per-run records.
  const [flowRunsLoaded, setFlowRuns] = useState([]);
  const [flowLoadingRaw, setFlowLoading] = useState(false);
  const insightsCacheRef = useRef({}); // run_id -> { status, message_runs }

  useEffect(() => {
    let cancelled = false;
    const entries = (executionLog || []).filter((e) => e.run_id);
    if (!entries.length) return undefined;

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
          subtask: entry.subtask || null,
          error: mr.error || entry.error,
        }));
      });
      setFlowRuns(merged);
      setFlowLoading(false);
    };
    load();
    return () => { cancelled = true; };
  }, [executionLog]);
  // Without any run in the execution log there is nothing to show, whatever
  // an earlier log produced.
  const hasFlowEntries = (executionLog || []).some((e) => e.run_id);
  const flowRuns = hasFlowEntries ? flowRunsLoaded : EMPTY_FLOW_RUNS;
  const flowLoading = hasFlowEntries && flowLoadingRaw;


  // ── Files tab: load/preview ────────────────────────────────────────────────
  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  // `fetchFileContent` sets nothing before the response, so the auto-select
  // effect below can call it; a click goes through `loadFileContent`, which
  // raises the loading flag first.
  // The file asked for last: a slower answer for one clicked before it is
  // dropped, or quick clicks through the list would end on the wrong file.
  const latestFileRef = useRef(null);
  const fetchFileContent = useCallback((path) => {
    latestFileRef.current = path;
    const current = () => latestFileRef.current === path;
    return getTaskFileContent(id, path)
      .then((resp) => {
        if (!current()) return;
        setSelectedFileContent(resp.data?.content || '');
        setSelectedFileSize(Number(resp.data?.size || 0));
        setSelectedFileIsPdf(!!resp.data?.is_pdf);
        setPdfViewMode('render');
        setMdViewMode('rendered');
      })
      .catch((e) => {
        if (!current()) return;
        const detail = e?.response?.data?.detail || t('taskDetails.errors.fileContent');
        setFileContentError(detail);
        setSelectedFileContent('');
        setSelectedFileSize(0);
        setSelectedFileIsPdf(false);
      })
      .finally(() => { if (current()) setFileContentLoading(false); });
  }, [id, t]);

  const loadFileContent = useCallback((path) => {
    if (!path) return undefined;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    return fetchFileContent(path);
  }, [fetchFileContent]);

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  };

  const fileTree = useMemo(() => buildFileTree(workspaceFiles), [workspaceFiles]);

  // The list changing is handled during render, where state may be adjusted:
  // an empty list clears the preview; otherwise every parent folder opens and,
  // when the shown file is gone, the first one is selected. The fetch for that
  // selection is started by the effect below (`autoFilePath`).
  const [seenFiles, setSeenFiles] = useState(workspaceFiles);
  const [autoFilePath, setAutoFilePath] = useState(null);
  if (seenFiles !== workspaceFiles) {
    setSeenFiles(workspaceFiles);
    if (!workspaceFiles.length) {
      setSelectedFilePath('');
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setExpandedFolders(new Set());
      setAutoFilePath(null);
    } else {
      setExpandedFolders((prev) => {
        const next = new Set(prev);
        workspaceFiles.forEach((p) => parentDirPaths(p).forEach((dir) => next.add(dir)));
        return next;
      });
      if (!selectedFilePath || !workspaceFiles.includes(selectedFilePath)) {
        setSelectedFilePath(workspaceFiles[0]);
        setFileContentLoading(true);
        setFileContentError('');
        setAutoFilePath(workspaceFiles[0]);
      }
    }
  }

  useEffect(() => {
    if (autoFilePath) fetchFileContent(autoFilePath);
  }, [autoFilePath, fetchFileContent]);

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
  // The task itself (subtask null) or one of its subtasks; the modal loads
  // what can be assigned and does the assigning (task/AssignExecutorModal.jsx).
  const openAssign = (subtask = null) => {
    setAssignTarget(subtask);
    setShowAssignModal(true);
  };

  const onAssigned = (data) => {
    if (!assignTarget && data.run_id) setActiveRunId(data.run_id);
    setAssignTarget(null);
    fetchData();
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

  // Decide on a run parked at its money cap (pending_approval.kind ===
  // 'budget'). Approving raises the cap to the operator's new number and
  // resumes from where it stopped; refusing stops the task instead of
  // resuming it, since there is no tool call to deny here.
  const handleBudgetDecision = async (approved) => {
    setApprovalSubmitting(true);
    try {
      const resp = await approveTaskCall(id, approved, approvalNote.trim(), approved ? Number(budgetCapDraft) : undefined);
      if (resp.data?.run_id) setActiveRunId(resp.data.run_id);
      setApprovalNote('');
      setBudgetCapDraft(null);
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

  // The decomposer is followed live in a modal (DecomposeProgress), on a
  // channel this tab is subscribed to before the run starts.
  const handleRunDecomposer = async () => {
    const channel = newDecomposeChannel();
    setDecomposeChannel(channel);
    try {
      const resp = await runDecomposer(id, { client_id: clientId, channel });
      if (resp.data?.run_id) { setActiveRunId(resp.data.run_id); fetchData(); }
    } catch (err) {
      setDecomposeChannel(null);
      alert(`${t('common.error')}: ` + (err.response?.data?.detail || err.message));
    }
  };

  const handleDeleteTask = async () => {
    if (!task) return;
    const count = (task.subtasks || []).length;
    if (!window.confirm(count > 0
      ? t('taskDetails.confirmDeleteWithSubtasks', { count })
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
  if (loading) return <PageLoader size="lg" label={t('taskDetails.loadingTask')} />;
  if (!task) return <div className="text-center py-10 text-gray-500">{t('taskDetails.taskNotFound')}</div>;

  const subtasks = (task.subtasks || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  const tasksById = new Map(workspaceTasks.map(x => [x.id, x]));
  for (const st of subtasks) tasksById.set(st.id, st);
  const dependents = workspaceTasks.filter(x => (x.depends || []).includes(task.id));
  const runningSubtask = subtasks.find(st => st.session_id && st.assigned_agent_run_id && st.status === 'in_progress');
  const liveSession = runningSubtask?.session_id || task.session_id || null;
  const liveWorking = !!runningSubtask || (task.status === 'in_progress' && !!task.assigned_agent_run_id);
  const doneCount = subtasks.filter(s => s.status === 'done').length;
  const progress = subtasks.length > 0 ? Math.round((doneCount / subtasks.length) * 100) : (task.status === 'done' ? 100 : 0);
  const TABS = [
    { id: 'live',      label: t('taskDetails.tabs.live'), Icon: Radio },
    { id: 'results',   label: t('taskDetails.tabs.results'), Icon: FileText },
    { id: 'subtasks',  label: t('taskDetails.tabs.subtasks', { count: subtasks.length }), Icon: Layers },
    { id: 'activity',  label: t('taskDetails.tabs.activity'), Icon: History },
    { id: 'execution', label: t('taskDetails.tabs.execution'), Icon: Clock },
    { id: 'logs',      label: t('taskDetails.tabs.logs'), Icon: Terminal },
    { id: 'files',     label: t('taskDetails.tabs.files'), Icon: Folder },
  ];
  const activityItems = activityLog.slice().sort((a, b) => {
    const ta = new Date(a?.timestamp || 0).getTime();
    const tb = new Date(b?.timestamp || 0).getTime();
    return tb - ta;
  });
  // Results are derived per-task (getTaskResult by task id), not from run insights.
  const hasResults = taskResults.length > 0;

  return (
    // The page scrolls as a whole: the cards, the tab bar and the open tab
    // run one under another, none with a scroll of its own.
    <PageContainer>
      <PageHeader
        icon={CheckSquare}
        backTo={backTo?.path || '/tasks'}
        backLabel={backTo ? t('taskDetails.projectTasks') : t('taskDetails.tasks')}
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
            {/* Only a task nobody has started is split: once it moved on from
                todo, the button stays but is inactive, and says why. */}
            {task.created_by === 'user' && (
              <button
                onClick={handleRunDecomposer}
                disabled={task.status !== 'todo'}
                title={task.status !== 'todo' ? t('taskDetails.decomposeOnlyTodo') : undefined}
                className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-emerald-600"
              >
                <Split className="w-4 h-4" /> {t('taskDetails.decompose')}
              </button>
            )}
            <button onClick={handleDeleteTask} disabled={deletingTask} className="flex items-center gap-1.5 px-3 py-2 bg-red-50 text-red-700 rounded-lg text-sm hover:bg-red-100 disabled:opacity-50">
              <Trash2 className="w-4 h-4" />
              {deletingTask ? t('common.deleting') : t('common.delete')}
            </button>
        </>}
      >
        <div className="flex items-center flex-wrap gap-2 mt-3">
          <StatusDropdown current={task.status} onChange={v => { if (confirmStatusChange(t, task, v)) patch({ status: v }); }} />
          <PriorityDropdown current={task.priority} onChange={v => patch({ priority: v })} />
          <DueDateField current={task.due_at} overdue={task.overdue} onChange={v => patch({ due_at: v })} />
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
          {/* Subtask progress, on the line with status, priority and due date. */}
          {subtasks.length > 0 && (
            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500" title={t('taskDetails.progress')}>
              <span className="whitespace-nowrap">{t('taskDetails.subtasksDone', { done: doneCount, total: subtasks.length, pct: progress })}</span>
              <div className="w-32 sm:w-48 bg-gray-200 rounded-full h-2">
                <div className="bg-indigo-500 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}
        </div>
      </PageHeader>

      {/* The cards' own bottom margins are dropped for an even gap. */}
      <div className="flex flex-col gap-4 [&>*]:mb-0">
      {/* Main task card */}
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6 mb-6">
        {/* Key, id, creation time and executor */}
        <TaskKeyRow task={task} />

        {/* Description, under the key and id: labelled, so it reads as a
            field; its pencil shows on hover */}
        <div className="mt-4">
          <div className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-1.5">
            {t('taskDetails.description')}
          </div>
          <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
            <InlineEdit
              value={task.description || ''}
              onSave={v => patch({ description: v })}
              multiline
              className="text-sm text-gray-700"
            />
          </div>
        </div>

        {/* Parent task and dependencies, after the description */}
        <TaskLinksRow depTasks={depTasks} dependents={dependents} parentTask={parentTask} linkState={backState}
          onEditDepends={() => setEditingDepends(true)} className="mt-4" />

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
          <AwaitingInputCard
            answerDraft={answerDraft}
            answerSubmitting={answerSubmitting}
            handleAnswerTask={handleAnswerTask}
            setAnswerDraft={setAnswerDraft}
            task={task}
          />
        )}

        {/* Awaiting approval — either a tool call that needs a human yes, or a
            run parked at its own money cap (task.pending_approval.kind ===
            'budget', set by RunBudgetGuard; see common/run_budget.py). The
            two share one status but ask a different question, so they get
            different cards rather than one trying to cover both. */}
        {task.status === 'awaiting_approval' && task.pending_approval?.kind === 'budget' ? (
          <BudgetPauseCard
            approvalSubmitting={approvalSubmitting}
            budgetCapDraft={budgetCapDraft}
            handleBudgetDecision={handleBudgetDecision}
            setBudgetCapDraft={setBudgetCapDraft}
            task={task}
          />
        ) : task.status === 'awaiting_approval' && (
          <ToolApprovalCard
            approvalNote={approvalNote}
            approvalSubmitting={approvalSubmitting}
            handleApprovalDecision={handleApprovalDecision}
            setApprovalNote={setApprovalNote}
            task={task}
          />
        )}
      </div>

      {/* The task's outcome rubric with its gradings (components/task/);
          nothing beyond its compact header when there is none. */}
      <TaskOutcomeCard task={task} onChanged={fetchData} />

      </div>

      {/* Tabs */}
      <div className="shrink-0 mt-2 border-b border-gray-200">
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

      {/* The open tab's area: as tall as the tab, the page scrolls past it. */}
      <div className="min-h-[14rem] flex flex-col">
      {/* ── Live output tab: always there. An agent working through the
          subtasks runs on each subtask's own session, so the panel follows
          the subtask being worked on, else the task's own session. ── */}
      {activeTab === 'live' && (
        <div className="h-[34rem] flex flex-col bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <LiveRunStream
            key={liveSession || 'none'}
            sessionId={liveSession}
            docked
            working={liveWorking}
            title={runningSubtask
              ? `${t('taskDetails.liveAgentOutput')} · ${runningSubtask.key || runningSubtask.title}`
              : t('taskDetails.liveAgentOutput')}
            emptyText={t('taskDetails.liveIdle')}
            waitingText={t('taskDetails.liveWaiting')}
            className="flex-1"
          />
        </div>
      )}

      {/* ── Subtasks tab ── */}
      {activeTab === 'subtasks' && (
        <SubtasksTab
          agents={agents}
          deletingSubtasks={deletingSubtasks}
          handleDeleteSubtask={handleDeleteSubtask}
          openAssign={openAssign}
          patch={patch}
          setShowAddSubtask={setShowAddSubtask}
          subtasks={subtasks}
          tasksById={tasksById}
        />
      )}

      {/* ── Execution tab ── */}
      {activeTab === 'execution' && (
        <ExecutionTab flowLoading={flowLoading} flowRuns={flowRuns} />
      )}

      {/* ── Activity tab ── */}
      {activeTab === 'activity' && (
        <ActivityTab activityItems={activityItems} runs={executionLog} />
      )}

      {/* ── Results tab ── */}
      {activeTab === 'results' && (
        <ResultsTab hasResults={hasResults} taskResults={taskResults}
          onOpenFile={(path) => { setActiveTab('files'); loadFileContent(path); }} />
      )}

      {/* ── Logs tab ── */}
      {activeTab === 'logs' && (
        <LogsTab
          activeRunId={logRunId || activeRunId}
          logs={logs}
          runs={executionLog.filter((e) => e.run_id && e.agent_id !== GRADER_AGENT_ID).reverse()}
          onSelectRun={pickLogRun}
        />
      )}

      {/* ── Files tab ── */}
      {activeTab === 'files' && (
        <TaskFilesTab
          task={task}
          onTaskChanged={fetchData}
          expandedFolders={expandedFolders}
          fileContentError={fileContentError}
          fileContentLoading={fileContentLoading}
          fileTree={fileTree}
          id={id}
          loadFileContent={loadFileContent}
          mdViewMode={mdViewMode}
          pdfViewMode={pdfViewMode}
          selectedFileContent={selectedFileContent}
          selectedFileIsPdf={selectedFileIsPdf}
          selectedFilePath={selectedFilePath}
          selectedFileSize={selectedFileSize}
          setExpandedFolders={setExpandedFolders}
          setMdViewMode={setMdViewMode}
          setPdfViewMode={setPdfViewMode}
          toggleFolder={toggleFolder}
          workspaceFiles={workspaceFiles}
        />
      )}
      </div>

      {decomposeChannel && (
        <DecomposeModal
          task={task}
          channel={decomposeChannel}
          showOpenTask={false}
          onClose={() => { setDecomposeChannel(null); fetchData(); }}
        />
      )}

      {editingDepends && (
        <DependsEditor
          task={task}
          candidates={workspaceTasks}
          onSaved={fetchData}
          onClose={() => setEditingDepends(false)}
        />
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
        <AssignExecutorModal
          task={assignTarget || task}
          onClose={() => { setShowAssignModal(false); setAssignTarget(null); }}
          onAssigned={onAssigned}
        />
      )}
    </PageContainer>
  );
};

export default TaskDetails;

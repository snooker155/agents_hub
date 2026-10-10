import {
  AlertCircle, CheckSquare, Clock, FileText, Folder, History, Layers, Loader, Pause, Play,
  Split, Square, Terminal, ThumbsDown, ThumbsUp, Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api, {
  answerTask, approveAssignment, approveTaskCall, assignAgent, deleteTask, getAgents, getLoops,
  getMessageInsights, getMessageLogs, getProjects, getSettings, getTask, getTaskActivityLog,
  getTaskExecutionLog, getTaskFileContent, getTaskResult, getTeams, listFlows,
  pauseTaskContainer, rejectAssignment, resumeTaskContainer, runDecomposer, stopAgent,
  updateTask,
} from '../api';
import InlineEdit from '../components/InlineEdit';
import LiveRunStream from '../components/LiveRunStream';
import { PageContainer, PageHeader } from '../components/PageLayout';
import PageLoader from '../components/PageLoader';
import TaskFilesCard from '../components/files/TaskFilesCard';
import { useLiveRefetch } from '../components/stream';
import { AddSubtaskModal } from '../components/task/SubtaskParts';
import TaskAgentVersionPin from '../components/task/TaskAgentVersionPin';
import { TaskAssignModal } from '../components/task/TaskAssignModal';
import {
  DueDateField, PriorityDropdown, ProjectSelector, StatusDropdown, WorkspaceBadge,
} from '../components/task/TaskFields';
import { TaskFilesTab } from '../components/task/TaskFilesTab';
import { TaskMetaRow } from '../components/task/TaskMetaRow';
import TaskOutcomeCard from '../components/task/TaskOutcomeCard';
import { AwaitingInputCard, BudgetPauseCard, ToolApprovalCard } from '../components/task/TaskPendingCards';
import {
  ActivityTab, ExecutionTab, LogsTab, ResultsTab, SubtasksTab,
} from '../components/task/TaskTabPanels';
import { buildFileTree, parentDirPaths } from '../components/task/taskUtils';
import { errorDetail, useToast } from '../components/toast';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

// A stable empty list, so memoised children do not see a new array per render.
const EMPTY_FLOW_RUNS = [];

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
  const [activeTab, setActiveTab] = useState('execution');
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [assignTarget, setAssignTarget] = useState(null); // null = parent task, subtask obj otherwise
  const [selectedAgent, setSelectedAgent] = useState('');
  // Besides an agent, a task may be assigned a flow, a team or a loop
  // (tasks.models.Executor). 'agent' keeps the original single-list picker;
  // the other three each pick from their own catalog, fetched lazily when
  // the modal opens.
  const [executorKind, setExecutorKind] = useState('agent');
  const [flows, setFlows] = useState([]);
  const [teams, setTeams] = useState([]);
  const [loops, setLoops] = useState([]);
  const [selectedFlow, setSelectedFlow] = useState('');
  const [selectedTeam, setSelectedTeam] = useState('');
  const [selectedLoop, setSelectedLoop] = useState('');
  const [catalogsLoaded, setCatalogsLoaded] = useState(false);


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
  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  // `fetchFileContent` sets nothing before the response, so the auto-select
  // effect below can call it; a click goes through `loadFileContent`, which
  // raises the loading flag first.
  const fetchFileContent = useCallback((path) => (
    getTaskFileContent(id, path)
      .then((resp) => {
        setSelectedFileContent(resp.data?.content || '');
        setSelectedFileSize(Number(resp.data?.size || 0));
        setSelectedFileIsPdf(!!resp.data?.is_pdf);
        setPdfViewMode('render');
        setMdViewMode('rendered');
      })
      .catch((e) => {
        const detail = e?.response?.data?.detail || t('taskDetails.errors.fileContent');
        setFileContentError(detail);
        setSelectedFileContent('');
        setSelectedFileSize(0);
        setSelectedFileIsPdf(false);
      })
      .finally(() => setFileContentLoading(false))
  ), [id, t]);

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
  const handleAssignAgent = async () => {
    const selection = { agent: selectedAgent, flow: selectedFlow, team: selectedTeam, loop: selectedLoop }[executorKind];
    if (!selection) return;
    try {
      const targetId = assignTarget ? assignTarget.id : id;
      const body = executorKind === 'agent'
        ? { agent_id: selectedAgent }
        : { executor: { kind: executorKind, id: selection } };
      const resp = await assignAgent(targetId, body);
      setActiveRunId(resp.data.run_id);
      setShowAssignModal(false);
      setAssignTarget(null);
      setSelectedAgent('');
      setSelectedFlow('');
      setSelectedTeam('');
      setSelectedLoop('');
      fetchData();
    } catch (err) {
      alert(`${t('taskDetails.errors.assignAgent')}: ` + (err.response?.data?.detail || err.message));
    }
  };

  const openAssign = (subtask = null) => {
    setAssignTarget(subtask);
    setSelectedAgent('');
    setSelectedFlow('');
    setSelectedTeam('');
    setSelectedLoop('');
    setExecutorKind('agent');
    setShowAssignModal(true);
    if (!catalogsLoaded) {
      const ws = task?.workspace || undefined;
      Promise.all([
        listFlows(ws).catch(() => ({ data: [] })),
        getTeams(ws).catch(() => ({ data: { teams: [] } })),
        getLoops(ws).catch(() => ({ data: { loops: [] } })),
      ]).then(([flowsResp, teamsResp, loopsResp]) => {
        setFlows(flowsResp.data || []);
        setTeams(teamsResp.data?.teams || []);
        setLoops(loopsResp.data?.loops || []);
        setCatalogsLoaded(true);
      });
    }
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
  if (loading) return <PageLoader size="lg" label={t('taskDetails.loadingTask')} />;
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
        <TaskMetaRow depTasks={depTasks} parentTask={parentTask} task={task} />

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

      {/* The run's pinned agent version and the task's outcome rubric with
          its gradings (components/task/). Each renders nothing it has no
          data for beyond its own compact header. */}
      <TaskAgentVersionPin task={task} onChanged={fetchData} />
      <TaskOutcomeCard task={task} onChanged={fetchData} />
      <TaskFilesCard task={task} onChanged={fetchData} />

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
        <SubtasksTab
          agents={agents}
          deletingSubtasks={deletingSubtasks}
          handleDeleteSubtask={handleDeleteSubtask}
          openAssign={openAssign}
          patch={patch}
          setShowAddSubtask={setShowAddSubtask}
          subtasks={subtasks}
        />
      )}

      {/* ── Execution tab ── */}
      {activeTab === 'execution' && (
        <ExecutionTab
          flowHeight={flowHeight}
          flowLoading={flowLoading}
          flowRuns={flowRuns}
          flowScrollRef={flowScrollRef}
        />
      )}

      {/* ── Activity tab ── */}
      {activeTab === 'activity' && (
        <ActivityTab activityItems={activityItems} />
      )}

      {/* ── Results tab ── */}
      {activeTab === 'results' && (
        <ResultsTab activeRunId={activeRunId} hasResults={hasResults} taskResults={taskResults} />
      )}

      {/* ── Logs tab ── */}
      {activeTab === 'logs' && (
        <LogsTab activeRunId={activeRunId} logs={logs} />
      )}

      {/* ── Files tab ── */}
      {activeTab === 'files' && (
        <TaskFilesTab
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
        <TaskAssignModal
          agents={agents}
          assignTarget={assignTarget}
          catalogsLoaded={catalogsLoaded}
          executorKind={executorKind}
          flows={flows}
          handleAssignAgent={handleAssignAgent}
          loops={loops}
          selectedAgent={selectedAgent}
          selectedFlow={selectedFlow}
          selectedLoop={selectedLoop}
          selectedTeam={selectedTeam}
          setAssignTarget={setAssignTarget}
          setExecutorKind={setExecutorKind}
          setSelectedAgent={setSelectedAgent}
          setSelectedFlow={setSelectedFlow}
          setSelectedLoop={setSelectedLoop}
          setSelectedTeam={setSelectedTeam}
          setShowAssignModal={setShowAssignModal}
          taskAssignmentMode={taskAssignmentMode}
          teams={teams}
        />
      )}
    </PageContainer>
  );
};

export default TaskDetails;

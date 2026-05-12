import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import { getWorkspace, getWorkspaceFilesByName, getWorkspaceFileContent, getAgents, addAgentToWorkspace, removeAgentFromWorkspace, createTask, deleteWorkspace, getProjects, getWorkspaceInstructions, updateWorkspaceInstructions } from '../api';
import { ChevronLeft, ChevronDown, ChevronRight, Folder, FolderOpen, FileText, Users, ShoppingBag, Plus, Trash2, Shield, Search, CheckSquare, AlertTriangle, Lock, FolderGit2, Globe, Server, GitBranch, BarChart2, BookOpen, Save, Check } from 'lucide-react';

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
          return {
            type: 'dir',
            name,
            path: fullPath,
            children: toArray(child, fullPath),
          };
        }
        return {
          type: 'file',
          name,
          path: child.path || fullPath,
        };
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

const WorkspaceDetails = () => {
  const { name } = useParams();
  const navigate = useNavigate();
  const { liveUpdates } = useWorkspace();
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState([]);
  const [allAgents, setAllAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('tasks');
  const [agentSearch, setAgentSearch] = useState('');
  const [taskSearch, setTaskSearch] = useState('');
  const [taskStatusFilter, setTaskStatusFilter] = useState('all');
  const [taskSort, setTaskSort] = useState('newest');
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [newTask, setNewTask] = useState({
    title: '',
    description: '',
    should_decompose: false,
    project_id: '',
  });
  const [wsProjects, setWsProjects] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');
  const [instructions, setInstructions] = useState('');
  const [instructionsDraft, setInstructionsDraft] = useState('');
  const [instructionsSaving, setInstructionsSaving] = useState(false);
  const [instructionsSaved, setInstructionsSaved] = useState(false);
  const instructionsLoadedRef = React.useRef(false);

  const fetchData = async () => {
    try {
      // Fetch in parallel but handle partial failures so the page still opens
      const [wsRes, filesRes, agentsRes, projectsRes, instrRes] = await Promise.allSettled([
        getWorkspace(name),
        getWorkspaceFilesByName(name),
        getAgents(),
        getProjects(name),
        getWorkspaceInstructions(name),
      ]);

      if (wsRes.status === 'fulfilled') {
        setWs(wsRes.value.data);
      } else {
        console.error('Failed to load workspace info', wsRes.reason);
      }

      if (filesRes.status === 'fulfilled') {
        setFiles((filesRes.value.data.files || []).filter(
          (p) => !p.split('/').some((seg) => seg.startsWith('.'))
        ));
      } else {
        console.warn('Failed to load workspace files', filesRes.reason);
        setFiles([]);
      }

      if (agentsRes.status === 'fulfilled') {
        setAllAgents(agentsRes.value.data || []);
      } else {
        console.warn('Failed to load agents list', agentsRes.reason);
        setAllAgents([]);
      }

      if (projectsRes.status === 'fulfilled') {
        setWsProjects(projectsRes.value.data || []);
      } else {
        setWsProjects([]);
      }

      if (instrRes.status === 'fulfilled') {
        const text = instrRes.value.data?.instructions || '';
        setInstructions(text);
        if (!instructionsLoadedRef.current) {
          setInstructionsDraft(text);
          instructionsLoadedRef.current = true;
        }
      }
    } catch (e) {
      console.error('Unexpected error while loading workspace data', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); if (!liveUpdates) return; const i = setInterval(fetchData, 5000); return () => clearInterval(i); }, [name, liveUpdates]);

  const handleDelete = async () => {
    if (!window.confirm(`Delete workspace "${ws.name}"? This cannot be undone.`)) return;
    try {
      await deleteWorkspace(name);
      navigate('/workspaces');
    } catch {
      alert('Failed to delete workspace');
    }
  };

  const handleAddAgent = async (agentId) => {
    try {
      await addAgentToWorkspace(name, agentId);
      fetchData();
    } catch {
      alert('Failed to add agent');
    }
  };

  const handleRemoveAgent = async (agentId) => {
    try {
      await removeAgentFromWorkspace(name, agentId);
      fetchData();
    } catch {
      alert('Failed to remove agent');
    }
  };

  const handleCreateTask = async (e) => {
    e.preventDefault();
    const title = (newTask.title || '').trim();
    if (!title) return;
    setCreatingTask(true);
    try {
      await createTask({
        title,
        description: (newTask.description || '').trim(),
        workspace_name: ws.name,
        should_decompose: !!newTask.should_decompose,
        project_id: newTask.project_id || null,
      });
      setShowTaskModal(false);
      setNewTask({ title: '', description: '', should_decompose: false, project_id: '' });
      fetchData();
    } catch {
      alert('Failed to create task');
    } finally {
      setCreatingTask(false);
    }
  };

  const loadFileContent = useCallback(async (path) => {
    if (!path) return;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    try {
      const resp = await getWorkspaceFileContent(name, path);
      setSelectedFileContent(resp.data?.content || '');
      setSelectedFileSize(Number(resp.data?.size || 0));
    } catch (e) {
      const detail = e?.response?.data?.detail || 'Failed to load file content';
      setFileContentError(detail);
      setSelectedFileContent('');
      setSelectedFileSize(0);
    } finally {
      setFileContentLoading(false);
    }
  }, [name]);

  const toggleFolder = (folderPath) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  };

  const handleSaveInstructions = async () => {
    setInstructionsSaving(true);
    setInstructionsSaved(false);
    try {
      await updateWorkspaceInstructions(name, instructionsDraft);
      setInstructions(instructionsDraft);
      setInstructionsSaved(true);
      setTimeout(() => setInstructionsSaved(false), 2000);
    } catch {
      alert('Failed to save instructions');
    } finally {
      setInstructionsSaving(false);
    }
  };

  const tabs = [
    { id: 'tasks', label: 'Tasks', icon: CheckSquare },
    { id: 'projects', label: 'Projects', icon: FolderGit2 },
    { id: 'progress', label: 'Progress', icon: BarChart2 },
    { id: 'files', label: 'Files', icon: FileText },
    { id: 'agents', label: 'Agents', icon: Users },
    { id: 'instructions', label: 'Instructions', icon: BookOpen },
  ];
  const marketAgents = allAgents.filter(a => !ws?.metadata?.allowed_agents?.includes(a.id));
  const normalizedQuery = agentSearch.trim().toLowerCase();
  const filteredMarketAgents = marketAgents.filter((agent) => {
    if (!normalizedQuery) return true;
    const haystack = [
      agent.name,
      agent.id,
      agent.domain,
      agent.description,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(normalizedQuery);
  });
  const allTaskStatuses = Array.from(new Set((ws?.tasks || []).map((t) => String(t.status || '').trim()).filter(Boolean)));
  const normalizedTaskQuery = taskSearch.trim().toLowerCase();
  const filteredSortedTasks = [...(ws?.tasks || [])]
    .filter((t) => {
      if (taskStatusFilter !== 'all' && String(t.status || '') !== taskStatusFilter) return false;
      if (!normalizedTaskQuery) return true;
      const haystack = [t.title, t.description, t.id, t.status].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(normalizedTaskQuery);
    })
    .sort((a, b) => {
      if (taskSort === 'title_az') return String(a.title || '').localeCompare(String(b.title || ''));
      if (taskSort === 'title_za') return String(b.title || '').localeCompare(String(a.title || ''));
      if (taskSort === 'progress_desc') return Number(b.progress || 0) - Number(a.progress || 0);
      if (taskSort === 'progress_asc') return Number(a.progress || 0) - Number(b.progress || 0);
      const aTs = new Date(a.created_at || 0).getTime() || 0;
      const bTs = new Date(b.created_at || 0).getTime() || 0;
      return taskSort === 'oldest' ? aTs - bTs : bTs - aTs;
    });
  const fileTree = useMemo(() => buildFileTree(files), [files]);

  useEffect(() => {
    if (!files.length) {
      setSelectedFilePath('');
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setExpandedFolders(new Set());
      return;
    }

    // Expand top-level folders by default.
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      files.forEach((p) => {
        const top = String(p || '').split('/').filter(Boolean)[0];
        if (top && String(p).includes('/')) next.add(top);
      });
      return next;
    });

    if (!selectedFilePath || !files.includes(selectedFilePath)) {
      const first = files[0];
      if (first) {
        setExpandedFolders((prev) => {
          const next = new Set(prev);
          parentDirPaths(first).forEach((dir) => next.add(dir));
          return next;
        });
        loadFileContent(first);
      }
    }
  }, [files, loadFileContent, selectedFilePath]);

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleFolder(node.path)}
            className="w-full flex items-center gap-1.5 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 rounded"
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
          >
            {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />}
            {open ? <FolderOpen className="w-4 h-4 text-amber-500" /> : <Folder className="w-4 h-4 text-amber-500" />}
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
        className={`w-full flex items-center gap-1.5 px-2 py-1 text-sm rounded ${
          isSelected
            ? 'bg-indigo-50 text-indigo-700'
            : 'text-gray-700 hover:bg-gray-50'
        }`}
        style={{ paddingLeft: `${depth * 14 + 28}px` }}
        title={node.path}
      >
        <FileText className="w-4 h-4 text-gray-400" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  });

  if (loading) return <div className="text-center py-10">Loading workspace...</div>;
  if (!ws) return <div className="text-center py-10">Workspace not found</div>;

  return (
    <div>
      <Link to="/workspaces" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Workspaces
      </Link>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center">
          <Folder className="w-7 h-7 text-gray-700 mr-2" />
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{ws.name}</h2>
            <div className="text-sm text-gray-500 truncate max-w-2xl">{ws.path}</div>
          </div>
        </div>
        <button
          onClick={handleDelete}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
          title="Delete workspace"
        >
          <Trash2 className="w-4 h-4" />
          Delete Workspace
        </button>
      </div>

      <div className="mb-6 border-b border-gray-200">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 text-sm font-semibold border-b-2 transition-colors ${
                  isActive
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {activeTab === 'tasks' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between gap-3 mb-4">
            <h3 className="text-lg font-bold">Allocated Tasks</h3>
            <button
              type="button"
              onClick={() => setShowTaskModal(true)}
              className="inline-flex items-center bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors"
            >
              <Plus className="w-4 h-4 mr-1.5" />
              Create Task
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <div className="relative">
              <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={taskSearch}
                onChange={(e) => setTaskSearch(e.target.value)}
                placeholder="Find by title, id, status..."
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none"
              />
            </div>
            <select
              value={taskStatusFilter}
              onChange={(e) => setTaskStatusFilter(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none bg-white"
            >
              <option value="all">All statuses</option>
              {allTaskStatuses.map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
            <select
              value={taskSort}
              onChange={(e) => setTaskSort(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none bg-white"
            >
              <option value="newest">Sort: Newest first</option>
              <option value="oldest">Sort: Oldest first</option>
              <option value="title_az">Sort: Title A-Z</option>
              <option value="title_za">Sort: Title Z-A</option>
              <option value="progress_desc">Sort: Progress high-low</option>
              <option value="progress_asc">Sort: Progress low-high</option>
            </select>
          </div>

          {filteredSortedTasks.length ? (
            <div className="space-y-3">
              {filteredSortedTasks.map(t => {
                const project = wsProjects.find(p => p.id === t.project_id);
                return (
                  <div
                    key={t.id}
                    className="p-3 border rounded hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/tasks/${t.id}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/tasks/${t.id}`); }}
                  >
                    <div className="flex justify-between items-center">
                      <div>
                        <Link to={`/tasks/${t.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-indigo-700 hover:underline">{t.title}</Link>
                        <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5">
                          <span>Status: {t.status}</span>
                          {project && (
                            <Link
                              to={`/projects/${project.id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="flex items-center gap-0.5 text-indigo-500 hover:text-indigo-700"
                            >
                              <FolderGit2 className="w-3 h-3" /> {project.name}
                            </Link>
                          )}
                        </div>
                      </div>
                      <div className="w-40 bg-gray-200 rounded-full h-2.5">
                        <div className="bg-indigo-600 h-2.5 rounded-full" style={{ width: `${t.progress}%` }}></div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">
              {ws.tasks && ws.tasks.length
                ? 'No tasks match your current filters.'
                : 'No tasks bound to this workspace.'}
            </p>
          )}
        </div>
      )}

      {activeTab === 'projects' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center gap-2">
              <FolderGit2 className="w-5 h-5 text-indigo-500" /> Projects
            </h3>
            <Link
              to="/projects"
              className="text-sm text-indigo-600 hover:text-indigo-800 font-medium"
            >
              Manage all projects →
            </Link>
          </div>
          {wsProjects.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <FolderGit2 className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">No projects in this workspace yet.</p>
              <Link
                to="/projects"
                className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
              >
                Create a project →
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {wsProjects.map(project => (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="flex items-start gap-3 p-4 border border-gray-200 rounded-xl hover:bg-indigo-50 hover:border-indigo-200 transition-colors group"
                >
                  <div className="w-9 h-9 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
                    <FolderGit2 className="w-4 h-4 text-indigo-600" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-gray-900 group-hover:text-indigo-700 truncate">{project.name}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium shrink-0 ${
                        project.status === 'active' ? 'bg-green-100 text-green-700' :
                        project.status === 'completed' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                      }`}>{project.status}</span>
                    </div>
                    {project.description && (
                      <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">{project.description}</p>
                    )}
                    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                      <span className="text-xs text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">{project.type}</span>
                      {project.repo?.type && project.repo.type !== 'none' && (
                        <span className="flex items-center gap-0.5 text-xs text-gray-500">
                          <GitBranch className="w-3 h-3" />{project.repo.type}
                        </span>
                      )}
                      {project.frontend?.enabled && (
                        <span className="flex items-center gap-0.5 text-xs text-blue-500">
                          <Globe className="w-3 h-3" /> Frontend
                        </span>
                      )}
                      {project.backend?.enabled && (
                        <span className="flex items-center gap-0.5 text-xs text-green-600">
                          <Server className="w-3 h-3" /> Backend
                        </span>
                      )}
                      {project.tasks_count > 0 && (
                        <span className="text-xs text-indigo-500">{project.tasks_count} task{project.tasks_count !== 1 ? 's' : ''}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-400 shrink-0 mt-1" />
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'progress' && (() => {
        const allTasks = ws?.tasks || [];
        const statusOrder = ['done', 'in_progress', 'ready', 'blocked', 'stopped', 'todo'];
        const statusLabels = { done: 'Done', in_progress: 'In Progress', ready: 'Ready', blocked: 'Blocked', stopped: 'Stopped', todo: 'To Do' };
        const statusColors = {
          done: { bar: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
          in_progress: { bar: 'bg-yellow-400', badge: 'bg-yellow-100 text-yellow-700' },
          ready: { bar: 'bg-blue-400', badge: 'bg-blue-100 text-blue-700' },
          blocked: { bar: 'bg-red-500', badge: 'bg-red-100 text-red-700' },
          stopped: { bar: 'bg-gray-400', badge: 'bg-gray-100 text-gray-500' },
          todo: { bar: 'bg-gray-300', badge: 'bg-gray-100 text-gray-500' },
        };
        const total = allTasks.length;
        const doneCnt = allTasks.filter(t => t.status === 'done').length;
        const pct = total > 0 ? Math.round((doneCnt / total) * 100) : 0;

        const byCounts = {};
        allTasks.forEach(t => { byCounts[t.status] = (byCounts[t.status] || 0) + 1; });

        // Group by project
        const byProject = {};
        allTasks.forEach(t => {
          const key = t.project_id || '__none__';
          if (!byProject[key]) byProject[key] = { tasks: [], name: null };
          byProject[key].tasks.push(t);
        });
        wsProjects.forEach(p => { if (byProject[p.id]) byProject[p.id].name = p.name; });

        return (
          <div className="space-y-6">
            {/* Summary card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                <BarChart2 className="w-4 h-4 text-indigo-500" /> Overall Progress
              </h3>
              {total === 0 ? (
                <p className="text-sm text-gray-400">No tasks in this workspace yet.</p>
              ) : (
                <>
                  <div className="flex items-end justify-between mb-2">
                    <span className="text-3xl font-bold text-gray-900">{pct}%</span>
                    <span className="text-sm text-gray-500">{doneCnt} / {total} tasks done</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-3 mb-6">
                    <div className="bg-green-500 h-3 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {statusOrder.map(s => {
                      const cnt = byCounts[s] || 0;
                      if (!cnt) return null;
                      const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500', bar: 'bg-gray-300' };
                      return (
                        <div key={s} className="flex items-center justify-between p-3 rounded-lg border border-gray-100 bg-gray-50">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${color.badge}`}>
                            {statusLabels[s] || s}
                          </span>
                          <span className="text-sm font-bold text-gray-700">{cnt}</span>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>

            {/* By project */}
            {Object.keys(byProject).length > 0 && total > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 p-6">
                <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                  <FolderGit2 className="w-4 h-4 text-indigo-500" /> Progress by Project
                </h3>
                <div className="space-y-4">
                  {Object.entries(byProject).map(([projectId, { tasks, name }]) => {
                    const ptotal = tasks.length;
                    const pdone = tasks.filter(t => t.status === 'done').length;
                    const ppct = ptotal > 0 ? Math.round((pdone / ptotal) * 100) : 0;
                    const label = projectId === '__none__' ? 'No Project' : (name || projectId.slice(0, 8) + '…');
                    return (
                      <div key={projectId}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            {projectId !== '__none__' && <FolderGit2 className="w-3.5 h-3.5 text-indigo-400" />}
                            {projectId !== '__none__' ? (
                              <Link to={`/projects/${projectId}`} className="hover:text-indigo-600">{label}</Link>
                            ) : label}
                          </span>
                          <span className="text-xs text-gray-500">{pdone}/{ptotal} done · {ppct}%</span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-2">
                          <div className="bg-indigo-500 h-2 rounded-full transition-all" style={{ width: `${ppct}%` }} />
                        </div>
                        <div className="flex flex-wrap gap-1.5 mt-1.5">
                          {statusOrder.map(s => {
                            const cnt = tasks.filter(t => t.status === s).length;
                            if (!cnt) return null;
                            const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500' };
                            return (
                              <span key={s} className={`text-xs px-1.5 py-0.5 rounded-full ${color.badge}`}>
                                {statusLabels[s] || s}: {cnt}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Activity log */}
            {total > 0 && (() => {
              const logEntries = allTasks.flatMap(t =>
                (t.activity_log || []).map(e => ({ ...e, taskTitle: t.title, taskId: t.id }))
              ).sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
              if (!logEntries.length) return null;
              const typeStyle = {
                created: { dot: 'bg-indigo-400', text: 'text-indigo-600' },
                status_change: { dot: 'bg-yellow-400', text: 'text-yellow-700' },
                agent_assigned: { dot: 'bg-blue-400', text: 'text-blue-700' },
                agent_state: { dot: 'bg-purple-400', text: 'text-purple-700' },
                agent_cleared: { dot: 'bg-gray-400', text: 'text-gray-500' },
              };
              return (
                <div className="bg-white rounded-xl border border-gray-200 p-6">
                  <h3 className="text-base font-semibold text-gray-800 mb-4">Activity Log</h3>
                  <div className="space-y-0 max-h-96 overflow-y-auto">
                    {logEntries.map((e, i) => {
                      const style = typeStyle[e.type] || { dot: 'bg-gray-300', text: 'text-gray-600' };
                      const ts = new Date(e.timestamp);
                      return (
                        <div key={i} className="flex gap-3 pb-3">
                          <div className="flex flex-col items-center">
                            <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${style.dot}`} />
                            {i < logEntries.length - 1 && <span className="w-px flex-1 bg-gray-100 mt-1" />}
                          </div>
                          <div className="pb-1 min-w-0">
                            <div className="flex items-baseline gap-2 flex-wrap">
                              <span className={`text-xs font-medium ${style.text}`}>{e.message}</span>
                              <Link to={`/tasks/${e.taskId}`} className="text-xs text-gray-400 hover:text-indigo-500 truncate max-w-xs">{e.taskTitle}</Link>
                            </div>
                            <span className="text-[10px] text-gray-400">{ts.toLocaleString()}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })()}
          </div>
        );
      })()}

      {activeTab === 'files' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center">
            <FileText className="w-5 h-5 mr-2" />
            Files
          </h3>
          {files.length ? (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
              <div className="lg:col-span-4 border border-gray-200 rounded-lg p-2 max-h-[36rem] overflow-y-auto">
                {renderFileNodes(fileTree)}
              </div>
              <div className="lg:col-span-8 border border-gray-200 rounded-lg overflow-hidden">
                <div className="px-4 py-2 border-b bg-gray-50">
                  <div className="text-xs text-gray-500">Selected file</div>
                  <div className="text-sm text-gray-700 truncate">{selectedFilePath || '-'}</div>
                  {selectedFileSize > 0 && (
                    <div className="text-xs text-gray-400 mt-0.5">{selectedFileSize} bytes</div>
                  )}
                </div>
                <div className="p-4 max-h-[32rem] overflow-auto">
                  {fileContentLoading ? (
                    <p className="text-sm text-gray-500">Loading file content...</p>
                  ) : fileContentError ? (
                    <p className="text-sm text-red-600">{fileContentError}</p>
                  ) : selectedFilePath ? (
                    <pre className="text-xs text-gray-800 whitespace-pre-wrap break-words">{selectedFileContent}</pre>
                  ) : (
                    <p className="text-sm text-gray-500">Select a file to preview its content.</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No files found.</p>
          )}
        </div>
      )}

      {activeTab === 'agents' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-6 flex items-center border-b pb-4">
            <ShoppingBag className="w-6 h-6 mr-2 text-indigo-600" />
            Agent Marketplace & Active Personnel
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Active Agents */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Users className="w-4 h-4 mr-2" />
                Authorized Agents in Workspace
              </h4>
              <div className="space-y-3">
                {ws.metadata?.allowed_agents?.length ? (
                  ws.metadata.allowed_agents.map(agentId => {
                    const agent = allAgents.find(a => a.id === agentId);
                    return (
                      <div key={agentId} className="flex items-center justify-between p-3 bg-indigo-50 border border-indigo-100 rounded-xl">
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-white rounded-lg text-indigo-600 shadow-sm">
                            <Shield className="w-4 h-4" />
                          </div>
                          <div>
                            <div className="text-sm font-bold text-indigo-900">{agent?.name || agentId}</div>
                            <div className="text-[10px] text-indigo-400">{agentId}</div>
                          </div>
                        </div>
                        <button
                          onClick={() => handleRemoveAgent(agentId)}
                          className="p-2 text-indigo-300 hover:text-red-500 transition-colors"
                          title="Remove from workspace"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">No agents authorized for this workspace.</p>
                )}
              </div>
            </div>

            {/* Marketplace */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Plus className="w-4 h-4 mr-2" />
                Available from Marketplace
              </h4>
              <div className="mb-3 relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={agentSearch}
                  onChange={(e) => setAgentSearch(e.target.value)}
                  placeholder="Find agent by name, id, domain..."
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none"
                />
              </div>
              <div className="grid grid-cols-1 gap-3 max-h-80 overflow-y-auto pr-2">
                {filteredMarketAgents.length ? (
                  filteredMarketAgents.map(agent => {
                    const isDefaultOnly = agent.default_workspace_only && name !== 'default';
                    return (
                      <div
                        key={agent.id}
                        className={`flex items-center justify-between p-3 border rounded-xl transition-colors ${
                          isDefaultOnly
                            ? 'border-gray-100 bg-gray-50 opacity-60'
                            : 'border-gray-100 hover:bg-gray-50'
                        }`}
                      >
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-gray-100 rounded-lg text-gray-400 font-bold text-[10px]">
                            {(agent.domain ?? agent.name ?? agent.id ?? '?')
                              .toString()
                              .slice(0, 3)
                              .toUpperCase()}
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
                              {agent.name}
                              {isDefaultOnly && <Lock className="w-3 h-3 text-gray-400" />}
                            </div>
                            <div className="text-[10px] text-gray-400 line-clamp-1">
                              {isDefaultOnly ? 'Default workspace only' : agent.description}
                            </div>
                          </div>
                        </div>
                        <button
                          onClick={() => !isDefaultOnly && handleAddAgent(agent.id)}
                          disabled={isDefaultOnly}
                          title={isDefaultOnly ? 'This agent is restricted to the default workspace' : undefined}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all shadow-sm ${
                            isDefaultOnly
                              ? 'bg-gray-100 text-gray-400 cursor-not-allowed border border-gray-200'
                              : 'bg-white border border-indigo-200 text-indigo-600 hover:bg-indigo-600 hover:text-white'
                          }`}
                        >
                          {isDefaultOnly ? 'Restricted' : 'Add Agent'}
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">
                    {agentSearch.trim() ? 'No marketplace agents match your search.' : 'No marketplace agents available.'}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'instructions' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-indigo-500" />
                Workspace Instructions
              </h3>
              <p className="text-sm text-gray-500 mt-0.5">
                These instructions are prepended to every agent's system prompt when running in this workspace.
              </p>
            </div>
            <button
              type="button"
              onClick={handleSaveInstructions}
              disabled={instructionsSaving || instructionsDraft === instructions}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
                instructionsSaved
                  ? 'bg-green-600 text-white'
                  : instructionsDraft === instructions
                  ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'bg-indigo-600 text-white hover:bg-indigo-700'
              }`}
            >
              {instructionsSaved ? (
                <><Check className="w-4 h-4" /> Saved</>
              ) : (
                <><Save className="w-4 h-4" /> {instructionsSaving ? 'Saving…' : 'Save'}</>
              )}
            </button>
          </div>
          <textarea
            value={instructionsDraft}
            onChange={(e) => setInstructionsDraft(e.target.value)}
            rows={20}
            placeholder={`# ${name} Workspace Instructions\n\nWrite high-level instructions for all agents in this workspace.\nSupports Markdown formatting.\n\nExample:\n- Always respond in English\n- Keep output concise\n- Prefer editing existing files over creating new ones`}
            className="w-full font-mono text-sm border border-gray-200 rounded-lg px-4 py-3 focus:ring-2 focus:ring-indigo-100 focus:border-indigo-400 outline-none resize-y leading-relaxed"
          />
          {instructions && instructionsDraft !== instructions && (
            <p className="text-xs text-amber-600 mt-2">You have unsaved changes.</p>
          )}
        </div>
      )}

      {showTaskModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-lg w-full p-6">
            <h3 className="text-xl font-bold mb-4">Create Task in {ws.name}</h3>
            <form onSubmit={handleCreateTask}>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
                <input
                  type="text"
                  required
                  value={newTask.title}
                  onChange={(e) => setNewTask((prev) => ({ ...prev, title: e.target.value }))}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                />
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  rows="3"
                  value={newTask.description}
                  onChange={(e) => setNewTask((prev) => ({ ...prev, description: e.target.value }))}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                />
              </div>
              {wsProjects.length > 0 && (
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Project (optional)</label>
                  <select
                    value={newTask.project_id}
                    onChange={(e) => setNewTask((prev) => ({ ...prev, project_id: e.target.value }))}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
                  >
                    <option value="">— No project —</option>
                    {wsProjects.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
              )}
              <div className="mb-6 flex items-center">
                <input
                  id="workspace_should_decompose"
                  type="checkbox"
                  checked={newTask.should_decompose}
                  onChange={(e) => setNewTask((prev) => ({ ...prev, should_decompose: e.target.checked }))}
                  className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
                />
                <label htmlFor="workspace_should_decompose" className="ml-2 text-sm text-gray-700">
                  Auto decompose into subtasks
                </label>
              </div>
              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowTaskModal(false)}
                  className="px-4 py-2 text-gray-700 hover:text-gray-900"
                  disabled={creatingTask}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 disabled:opacity-50"
                  disabled={creatingTask}
                >
                  {creatingTask ? 'Creating...' : 'Create Task'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkspaceDetails;

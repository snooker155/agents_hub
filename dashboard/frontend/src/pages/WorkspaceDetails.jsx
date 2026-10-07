import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import { getWorkspace, getWorkspaceFilesByName, getWorkspaceFileContent, getAgents, addAgentToWorkspace, removeAgentFromWorkspace, deleteWorkspace, getProjects, getWorkspaceInstructions, updateWorkspaceInstructions, uploadWorkspaceFile, getWorkspaceFileRawUrl, getWorkspaceFileId, deleteWorkspaceFile, listFlows, removeFlowFromWorkspace, getWorkspaceSettingsOverrides, updateWorkspaceSettingsOverrides } from '../api';
import { ChevronDown, ChevronRight, Folder, FolderOpen, FileText, Users, ShoppingBag, Plus, Trash2, Shield, Search, CheckSquare, AlertTriangle, Lock, FolderGit2, Globe, Server, GitBranch, BarChart2, BookOpen, Save, Check, Upload, Eye, Code2, Workflow, Palette as PaletteIcon, RefreshCw, Loader, Settings as SettingsIcon, UserRound, ShieldAlert, Sparkles, Repeat } from 'lucide-react';
import MarkdownRenderer from '../components/MarkdownRenderer';
import TaskBoard from '../components/TaskBoard';
import WorkspaceMembers from '../components/workspace/WorkspaceMembers';
import WorkspaceSecrets from '../components/workspace/WorkspaceSecrets';
import WorkspacePersonalMemory from '../components/workspace/WorkspacePersonalMemory';
import WorkspaceIsolation from '../components/workspace/WorkspaceIsolation';
import WorkspaceRoles from '../components/workspace/WorkspaceRoles';
import WorkspaceSpecialModels from '../components/workspace/WorkspaceSpecialModels';
import { useAuth, isAdmin, isMultiUser } from '../components/auth';
import { useTheme, resolvePalette } from '../components/theme';
import { invalidateWorkspaceSummary } from '../api/workspaceSummary';
import { PRESET_ORDER, PRESETS, SHADES, checkPalette, matchPreset, rampFromColor } from '../lib/palette';
import { inputCls } from '../components/settingsUi';
import {
  SaveWorkspaceSettingsButton, WorkspaceSettingsStatus,
  ExecutionModeSection, ToolPolicySection, TaskAssignmentSection, WorkspaceDomainPolicyCard,
} from '../components/settings/WorkspaceSettingsSections';
import { useWorkspaceSettings } from '../components/settings/useWorkspaceSettings';

import { PageContainer, PageHeader } from '../components/PageLayout';
import PageLoader from '../components/PageLoader';
import { useI18n } from '../i18n';
// Tab ids the page can open, so a ?tab= deep-link can be validated before use.
const WORKSPACE_TABS = ['files', 'agents', 'instructions', 'tasks', 'projects', 'progress', 'settings'];

const isMarkdownPath = (p) => /\.(md|markdown|mdx)$/i.test(String(p || ''));

const buildFileTree = (paths, directories = []) => {
  const root = { type: 'dir', children: {} };
  (directories || []).forEach((rawPath) => {
    const cleanPath = String(rawPath || '').trim();
    if (!cleanPath) return;
    const parts = cleanPath.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part) => {
      if (!node.children[part]) {
        node.children[part] = { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });
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
  const { t } = useI18n();
  const { name } = useParams();
  const navigate = useNavigate();
  // Deep-link support: ?tab=<tab>&file=<file id>. Chat replies link a file the
  // agent wrote straight to it (see common/entity_links.py); a link with a
  // workspace-relative path, from before files had ids, still opens.
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedTab = searchParams.get('tab') || '';
  const linkedFile = searchParams.get('file') || '';
  const { liveUpdates } = useWorkspace();
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState([]);
  const [folders, setFolders] = useState([]);
  // Path to registry id of the folder files (GET /workspaces/{name}/files).
  const [fileIds, setFileIds] = useState({});
  const fileIdsRef = React.useRef({});
  // The file the address names: by id, or by path for an old link.
  const linkedPath = useMemo(() => {
    if (!linkedFile) return '';
    const byId = Object.keys(fileIds).find((p) => fileIds[p] === linkedFile);
    if (byId && files.includes(byId)) return byId;
    return files.includes(linkedFile) ? linkedFile : '';
  }, [linkedFile, fileIds, files]);
  const [allAgents, setAllAgents] = useState([]);
  const [allFlows, setAllFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState(
    WORKSPACE_TABS.includes(linkedTab) ? linkedTab : 'files');
  const [agentSearch, setAgentSearch] = useState('');
  // System agents are in every workspace and cannot be removed, so they only
  // crowd the authorized list; hidden by default, and the choice sticks.
  const [showSystem, setShowSystem] = useState(() => {
    try { return localStorage.getItem('workspace_agents_show_system') === 'true'; } catch { return false; }
  });
  const toggleSystem = (next) => {
    setShowSystem(next);
    try { localStorage.setItem('workspace_agents_show_system', next ? 'true' : 'false'); } catch { /* per-viewer convenience only */ }
  };
  const [taskToolbar, setTaskToolbar] = useState(null);
  const [wsProjects, setWsProjects] = useState([]);
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [selectedFileIsPdf, setSelectedFileIsPdf] = useState(false);
  const [pdfViewMode, setPdfViewMode] = useState('render');
  const [mdViewMode, setMdViewMode] = useState('rendered'); // 'rendered' | 'raw'
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');
  const [fileDeleting, setFileDeleting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = React.useRef(null);
  const [instructions, setInstructions] = useState('');
  const [instructionsDraft, setInstructionsDraft] = useState('');
  const [instructionsSaving, setInstructionsSaving] = useState(false);
  const [instructionsSaved, setInstructionsSaved] = useState(false);
  const instructionsLoadedRef = React.useRef(false);

  const fetchData = useCallback(async () => {
    try {
      // Fetch in parallel but handle partial failures so the page still opens
      const [wsRes, filesRes, agentsRes, projectsRes, instrRes, flowsRes] = await Promise.allSettled([
        getWorkspace(name),
        getWorkspaceFilesByName(name),
        getAgents(),
        getProjects(name),
        getWorkspaceInstructions(name),
        listFlows(),
      ]);

      if (wsRes.status === 'fulfilled') {
        setWs(wsRes.value.data);
      } else {
        console.error(t('workspaceDetails.errors.info'), wsRes.reason);
      }

      if (filesRes.status === 'fulfilled') {
        setFiles((filesRes.value.data.files || []).filter(
          (p) => !p.split('/').some((seg) => seg.startsWith('.'))
        ));
        setFolders((filesRes.value.data.directories || []).filter(
          (p) => !p.split('/').some((seg) => seg.startsWith('.'))
        ));
        const ids = filesRes.value.data.ids || {};
        fileIdsRef.current = ids;
        setFileIds(ids);
      } else {
        console.warn(t('workspaceDetails.errors.files'), filesRes.reason);
        setFiles([]);
        setFolders([]);
      }

      if (agentsRes.status === 'fulfilled') {
        setAllAgents(agentsRes.value.data || []);
      } else {
        console.warn(t('workspaceDetails.errors.agents'), agentsRes.reason);
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

      if (flowsRes.status === 'fulfilled') {
        setAllFlows(flowsRes.value.data || []);
      } else {
        setAllFlows([]);
      }
    } catch (e) {
      console.error(t('workspaceDetails.errors.unexpected'), e);
    } finally {
      setLoading(false);
    }
  }, [name, t]);

  useEffect(() => { fetchData(); }, [name, liveUpdates, fetchData]);
  useLiveRefetch(fetchData, { enabled: liveUpdates });

  const handleDelete = async () => {
    if (!window.confirm(t('workspaceDetails.confirmDeleteWorkspace', { name: ws.name }))) return;
    try {
      await deleteWorkspace(name);
      navigate('/workspaces');
    } catch {
      alert(t('workspaceDetails.errors.deleteWorkspace'));
    }
  };

  const handleAddAgent = async (agentId) => {
    try {
      await addAgentToWorkspace(name, agentId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.addAgent'));
    }
  };

  const handleRemoveAgent = async (agentId) => {
    try {
      await removeAgentFromWorkspace(name, agentId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.removeAgent'));
    }
  };

  const handleRemoveFlow = async (flowId) => {
    try {
      await removeFlowFromWorkspace(name, flowId);
      fetchData();
    } catch {
      alert(t('workspaceDetails.errors.removeFlow'));
    }
  };

  // The registry id of a folder file, asked for once when the listing had
  // none (a file written outside the tools); null when it cannot have one.
  const fileIdOf = useCallback(async (path) => {
    if (fileIdsRef.current[path]) return fileIdsRef.current[path];
    try {
      const resp = await getWorkspaceFileId(name, path);
      const id = resp.data?.file_id;
      if (!id) return null;
      fileIdsRef.current = { ...fileIdsRef.current, [path]: id };
      setFileIds(fileIdsRef.current);
      return id;
    } catch {
      return null;
    }
  }, [name]);

  // Opens a file; ``link`` puts its id in the address (?file=<id>), for a
  // file the user picked or a link opened, not for the first one shown.
  const loadFileContent = useCallback(async (path, { link = false } = {}) => {
    if (!path) return;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    const fileId = await fileIdOf(path);
    if (link) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set('tab', 'files');
        if (fileId) next.set('file', fileId);
        else next.delete('file');
        return next;
      }, { replace: true });
    }
    try {
      const resp = await getWorkspaceFileContent(name, fileId ? { fileId } : path);
      setSelectedFileContent(resp.data?.content || '');
      setSelectedFileSize(Number(resp.data?.size || 0));
      setSelectedFileIsPdf(!!resp.data?.is_pdf);
      setPdfViewMode('render');
      setMdViewMode('rendered');
    } catch (e) {
      const detail = e?.response?.data?.detail || t('workspaceDetails.errors.fileContent');
      setFileContentError(detail);
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setSelectedFileIsPdf(false);
    } finally {
      setFileContentLoading(false);
    }
  }, [name, t, fileIdOf, setSearchParams]);

  const handleUploadFiles = async (fileList) => {
    const selected = Array.from(fileList || []);
    if (!selected.length) return;
    setUploading(true);
    let lastPath = '';
    try {
      for (const f of selected) {
        const resp = await uploadWorkspaceFile(name, f);
        lastPath = resp.data?.path || lastPath;
      }
      await fetchData();
      if (lastPath) loadFileContent(lastPath, { link: true });
    } catch (e) {
      const detail = e?.response?.data?.detail || t('workspaceDetails.errors.upload');
      alert(detail);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDeleteWorkspacePath = async (path, type = 'file') => {
    if (!path || fileDeleting) return;
    const label = type === 'dir' ? t('workspaceDetails.folder') : t('workspaceDetails.file');
    const warning = type === 'dir' ? ` ${t('workspaceDetails.deleteFolderWarning')}` : '';
    if (!window.confirm(t('workspaceDetails.confirmDeletePath', { label, path, workspace: name }) + warning)) return;
    setFileDeleting(true);
    setFileContentError('');
    try {
      const fileId = type === 'dir' ? null : fileIdsRef.current[path];
      await deleteWorkspaceFile(name, fileId ? { fileId } : path);
      if (type === 'dir') {
        const prefix = `${path}/`;
        setFiles(prev => prev.filter(p => p !== path && !p.startsWith(prefix)));
        setFolders(prev => prev.filter(p => p !== path && !p.startsWith(prefix)));
        if (selectedFilePath === path || selectedFilePath.startsWith(prefix)) {
          setSelectedFilePath('');
          setSelectedFileContent('');
          setSelectedFileSize(0);
          setSelectedFileIsPdf(false);
        }
      } else {
        setFiles(prev => prev.filter(p => p !== path));
        if (selectedFilePath === path) {
          setSelectedFilePath('');
          setSelectedFileContent('');
          setSelectedFileSize(0);
          setSelectedFileIsPdf(false);
        }
      }
      await fetchData();
    } catch (e) {
      const detail = e?.response?.data?.detail || t('workspaceDetails.errors.deletePath', { label });
      setFileContentError(detail);
    } finally {
      setFileDeleting(false);
    }
  };

  const handleDeleteSelectedFile = async () => {
    await handleDeleteWorkspacePath(selectedFilePath, 'file');
  };

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
      alert(t('workspaceDetails.errors.instructions'));
    } finally {
      setInstructionsSaving(false);
    }
  };

  // Keep in sync with WORKSPACE_TABS (the ?tab= allowlist).
  const tabs = [
    { id: 'files', label: t('workspaceDetails.tabs.files'), icon: FileText },
    { id: 'agents', label: t('workspaceDetails.tabs.agents'), icon: Users },
    { id: 'instructions', label: t('workspaceDetails.tabs.instructions'), icon: BookOpen },
    { id: 'tasks', label: t('workspaceDetails.tabs.tasks'), icon: CheckSquare },
    { id: 'projects', label: t('workspaceDetails.tabs.projects'), icon: FolderGit2 },
    { id: 'progress', label: t('workspaceDetails.tabs.progress'), icon: BarChart2 },
    { id: 'settings', label: t('workspaceDetails.tabs.settings'), icon: SettingsIcon },
  ];
  const agentById = new Map(allAgents.map(a => [a.id, a]));
  const allowedAgentIds = ws?.metadata?.allowed_agents || [];
  const isSystemAgentId = (id) => agentById.get(id)?.system === true;
  const systemAllowedCount = allowedAgentIds.filter(isSystemAgentId).length;
  const shownAllowedAgentIds = showSystem ? allowedAgentIds : allowedAgentIds.filter(id => !isSystemAgentId(id));
  // System agents are already in every workspace, so they are never offered here.
  const marketAgents = allAgents.filter(a => !a.system && !allowedAgentIds.includes(a.id));
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
  const fileTree = useMemo(() => buildFileTree(files, folders), [files, folders]);

  useEffect(() => {
    if (!files.length && !folders.length) {
      setSelectedFilePath('');
      setSelectedFileContent('');
      setSelectedFileSize(0);
      setExpandedFolders(new Set());
      return;
    }

    // Expand top-level folders by default.
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      folders.forEach((p) => {
        const top = String(p || '').split('/').filter(Boolean)[0];
        if (top) next.add(top);
      });
      files.forEach((p) => {
        const top = String(p || '').split('/').filter(Boolean)[0];
        if (top && String(p).includes('/')) next.add(top);
      });
      return next;
    });

    if (!selectedFilePath || !files.includes(selectedFilePath)) {
      const first = linkedPath || files[0];
      if (first) {
        setExpandedFolders((prev) => {
          const next = new Set(prev);
          parentDirPaths(first).forEach((dir) => next.add(dir));
          return next;
        });
        // A link with a path is rewritten to the file's id.
        loadFileContent(first, { link: Boolean(linkedPath) && linkedPath === linkedFile });
      } else {
        setSelectedFilePath('');
        setSelectedFileContent('');
        setSelectedFileSize(0);
        setSelectedFileIsPdf(false);
      }
    }
  }, [files, folders, loadFileContent, selectedFilePath, linkedPath, linkedFile]);

  // Another file link opened while the page is up (a chat link, back and
  // forward) switches the open file. The selection is read through a ref so
  // a click, which selects before its id reaches the address, is not undone.
  const selectedPathRef = React.useRef('');
  selectedPathRef.current = selectedFilePath;
  useEffect(() => {
    const current = selectedPathRef.current;
    if (linkedPath && current && linkedPath !== current) {
      loadFileContent(linkedPath, { link: linkedPath === linkedFile });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the link changes, not the selection
  }, [linkedPath]);

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <div
            className="group w-full flex items-center gap-1.5 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 rounded"
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
          >
            <button
              type="button"
              onClick={() => toggleFolder(node.path)}
              className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
              title={node.path}
            >
              {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
              {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
              <span className="truncate">{node.name}</span>
            </button>
            <button
              type="button"
              onClick={() => handleDeleteWorkspacePath(node.path, 'dir')}
              disabled={fileDeleting}
              className="p-1 rounded text-gray-300 hover:text-red-600 hover:bg-red-50 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-30"
              title={t('workspaceDetails.deleteFolder')}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
          {open && node.children?.length > 0 && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }

    const isSelected = node.path === selectedFilePath;
    return (
      <div
        key={node.path}
        className={`group w-full flex items-center gap-1.5 px-2 py-1 text-sm rounded ${
          isSelected
            ? 'bg-indigo-50 text-indigo-700'
            : 'text-gray-700 hover:bg-gray-50'
        }`}
        style={{ paddingLeft: `${depth * 14 + 28}px` }}
      >
        <button
          type="button"
          onClick={() => {
            setExpandedFolders((prev) => {
              const next = new Set(prev);
              parentDirPaths(node.path).forEach((dir) => next.add(dir));
              return next;
            });
            loadFileContent(node.path, { link: true });
          }}
          className="flex items-center gap-1.5 min-w-0 flex-1 text-left"
          title={node.path}
        >
          <FileText className="w-4 h-4 text-gray-400 shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
        <button
          type="button"
          onClick={() => handleDeleteWorkspacePath(node.path, 'file')}
          disabled={fileDeleting}
          className="p-1 rounded text-gray-300 hover:text-red-600 hover:bg-red-50 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-30"
          title={t('workspaceDetails.deleteFile')}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  });

  if (loading) return <PageLoader size="lg" label={t('workspaceDetails.loadingWorkspace')} />;
  if (!ws) return <div className="text-center py-10">{t('workspaceDetails.workspaceNotFound')}</div>;

  return (
    <PageContainer fill={activeTab === 'files' || activeTab === 'tasks' || activeTab === 'settings'}>
      <PageHeader
        icon={Folder}
        title={ws.name}
        description={ws.path}
        backTo="/workspaces"
        backLabel={t('workspaceDetails.workspaces')}
        actions={
          <button
            onClick={handleDelete}
            className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-600 transition hover:bg-rose-100"
            title={t('workspaceDetails.deleteWorkspace')}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            {t('workspaceDetails.deleteWorkspace2')}
          </button>
        }
      />

      <div className="border-b border-gray-200 shrink-0 flex items-end justify-between gap-3">
        <nav className="flex flex-wrap gap-2 -mb-px">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex items-center px-4 py-2 first:pl-0 text-sm font-semibold border-b-2 transition-colors ${
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
        {activeTab === 'tasks' && <div ref={setTaskToolbar} className="flex items-center shrink-0" />}
      </div>

      {activeTab === 'tasks' && (
        <div className="flex-1 min-h-0 overflow-y-auto">
          <TaskBoard
            workspace={name}
            selectedWorkspace={name}
            showWorkspaceColumn={false}
            liveUpdates={liveUpdates}
            toolbarTarget={taskToolbar}
          />
        </div>
      )}

      {activeTab === 'projects' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold flex items-center gap-2">
              <FolderGit2 className="w-5 h-5 text-indigo-500" /> {t('workspaceDetails.projects')}
            </h3>
            <Link
              to="/projects"
              className="text-sm text-indigo-600 hover:text-indigo-800 font-medium"
            >
              {t('workspaceDetails.manageAllProjects')}
            </Link>
          </div>
          {wsProjects.length === 0 ? (
            <div className="text-center py-10 text-gray-400">
              <FolderGit2 className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">{t('workspaceDetails.noProjectsInThisWorkspace')}</p>
              <Link
                to="/projects"
                className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
              >
                {t('workspaceDetails.createAProject')}
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
                          <Globe className="w-3 h-3" /> {t('workspaceDetails.frontend')}
                        </span>
                      )}
                      {project.backend?.enabled && (
                        <span className="flex items-center gap-0.5 text-xs text-green-600">
                          <Server className="w-3 h-3" /> {t('workspaceDetails.backend')}
                        </span>
                      )}
                      {project.tasks_count > 0 && (
                        <span className="text-xs text-indigo-500">{t('workspaceDetails.taskCount', { count: project.tasks_count })}</span>
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
        const statusLabels = {
          done: t('workspaceDetails.taskStatuses.done'),
          in_progress: t('workspaceDetails.taskStatuses.in_progress'),
          ready: t('workspaceDetails.taskStatuses.ready'),
          blocked: t('workspaceDetails.taskStatuses.blocked'),
          stopped: t('workspaceDetails.taskStatuses.stopped'),
          todo: t('workspaceDetails.taskStatuses.todo'),
        };
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
                <BarChart2 className="w-4 h-4 text-indigo-500" /> {t('workspaceDetails.overallProgress')}
              </h3>
              {total === 0 ? (
                <p className="text-sm text-gray-400">{t('workspaceDetails.noTasksInThisWorkspace')}</p>
              ) : (
                <>
                  <div className="flex items-end justify-between mb-2">
                    <span className="text-3xl font-bold text-gray-900">{pct}%</span>
                    <span className="text-sm text-gray-500">{t('workspaceDetails.tasksDone', { done: doneCnt, total })}</span>
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
                  <FolderGit2 className="w-4 h-4 text-indigo-500" /> {t('workspaceDetails.progressByProject')}
                </h3>
                <div className="space-y-4">
                  {Object.entries(byProject).map(([projectId, { tasks, name }]) => {
                    const ptotal = tasks.length;
                    const pdone = tasks.filter(t => t.status === 'done').length;
                    const ppct = ptotal > 0 ? Math.round((pdone / ptotal) * 100) : 0;
                    const label = projectId === '__none__' ? t('workspaceDetails.noProject') : (name || projectId.slice(0, 8) + '…');
                    return (
                      <div key={projectId}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            {projectId !== '__none__' && <FolderGit2 className="w-3.5 h-3.5 text-indigo-400" />}
                            {projectId !== '__none__' ? (
                              <Link to={`/projects/${projectId}`} className="hover:text-indigo-600">{label}</Link>
                            ) : label}
                          </span>
                          <span className="text-xs text-gray-500">{t('workspaceDetails.doneOfTotal', { done: pdone, total: ptotal })} · {ppct}%</span>
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
                  <h3 className="text-base font-semibold text-gray-800 mb-4">{t('workspaceDetails.activityLog')}</h3>
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
        <div className="bg-white p-6 shadow-md rounded-lg flex flex-col flex-1 min-h-0">
          <div className="flex items-center justify-between mb-4 shrink-0">
            <h3 className="text-lg font-bold flex items-center">
              <FileText className="w-5 h-5 mr-2" />
              {t('workspaceDetails.files')}
            </h3>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => handleUploadFiles(e.target.files)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors disabled:opacity-50"
            >
              <Upload className="w-4 h-4" />
              {uploading ? t('workspaceDetails.uploading') : t('workspaceDetails.uploadFile')}
            </button>
          </div>
          {(files.length || folders.length) ? (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 flex-1 min-h-0">
              {/* The tree is a picker; the file is the thing being read. Two
                  of twelve columns is enough for names, and the other ten stop
                  wrapping every other line of the file beside it. */}
              <div className="lg:col-span-2 border border-gray-200 rounded-lg p-2 overflow-auto min-h-0">
                {renderFileNodes(fileTree)}
              </div>
              <div className="lg:col-span-10 border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
                <div className="px-4 py-2 border-b bg-gray-50 flex items-center justify-between gap-2 shrink-0">
                  <div className="min-w-0">
                    <div className="text-xs text-gray-500">{t('workspaceDetails.selectedFile')}</div>
                    <div className="text-sm text-gray-700 truncate flex items-center gap-2">
                      <span className="truncate">{selectedFilePath || '-'}</span>
                      {selectedFileIsPdf && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-100 text-red-600 shrink-0">
                          <FileText className="w-2.5 h-2.5" /> {t('workspaceDetails.pdf')}
                        </span>
                      )}
                    </div>
                    {selectedFileSize > 0 && (
                      <div className="text-xs text-gray-400 mt-0.5">{t('workspaceDetails.bytes', { count: selectedFileSize })}</div>
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
                          {t('workspaceDetails.render')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPdfViewMode('text')}
                          className={`px-2.5 py-1 transition-colors border-l border-gray-200 ${pdfViewMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          {t('workspaceDetails.text')}
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
                          <Eye className="w-3.5 h-3.5" /> {t('workspaceDetails.rendered')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setMdViewMode('raw')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-gray-200 ${mdViewMode === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                        >
                          <Code2 className="w-3.5 h-3.5" /> {t('workspaceDetails.raw')}
                        </button>
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={handleDeleteSelectedFile}
                      disabled={!selectedFilePath || fileDeleting}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      title={selectedFilePath ? t('workspaceDetails.deleteSelectedFile') : t('workspaceDetails.selectFileToDelete')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      {fileDeleting ? t('common.deleting') : t('common.delete')}
                    </button>
                  </div>
                </div>
                <div className={`${selectedFileIsPdf && pdfViewMode === 'render' ? '' : 'p-4'} flex-1 min-h-0 overflow-auto`}>
                  {fileContentLoading ? (
                    <p className="text-sm text-gray-500 p-4">{t('workspaceDetails.loadingFileContent')}</p>
                  ) : fileContentError ? (
                    <p className="text-sm text-red-600 p-4">{fileContentError}</p>
                  ) : selectedFilePath && selectedFileIsPdf && pdfViewMode === 'render' ? (
                    <iframe
                      title={selectedFilePath}
                      src={getWorkspaceFileRawUrl(name, fileIds[selectedFilePath] ? { fileId: fileIds[selectedFilePath] } : selectedFilePath)}
                      className="w-full h-full border-0"
                    />
                  ) : selectedFilePath && isMarkdownPath(selectedFilePath) && mdViewMode === 'rendered' ? (
                    <MarkdownRenderer content={selectedFileContent} />
                  ) : selectedFilePath ? (
                    <pre className="text-xs text-gray-800 whitespace-pre-wrap break-words">{selectedFileContent}</pre>
                  ) : (
                    <p className="text-sm text-gray-500">{t('workspaceDetails.selectAFileToPreview')}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-500 text-sm">{t('workspaceDetails.noFilesFound')}</p>
          )}
        </div>
      )}

      {activeTab === 'agents' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-6 flex items-center border-b pb-4">
            <ShoppingBag className="w-6 h-6 mr-2 text-indigo-600" />
            {t('workspaceDetails.agentsHeading')}
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Active Agents */}
            <div>
              <div className="mb-4 flex items-center justify-between gap-3 flex-wrap">
                <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest flex items-center">
                  <Users className="w-4 h-4 mr-2" />
                  {t('workspaceDetails.authorizedAgentsInWorkspace')}
                </h4>
                <label
                  className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none"
                  title={t('workspaceDetails.showSystemAgentsHint')}
                >
                  <input
                    type="checkbox"
                    checked={showSystem}
                    onChange={(e) => toggleSystem(e.target.checked)}
                    className="w-3.5 h-3.5 accent-indigo-600 cursor-pointer"
                  />
                  {t('workspaceDetails.showSystemAgents')}
                  {systemAllowedCount > 0 && <span className="text-gray-400">({systemAllowedCount})</span>}
                </label>
              </div>
              <div className="space-y-3">
                {shownAllowedAgentIds.length ? (
                  shownAllowedAgentIds.map(agentId => {
                    const agent = agentById.get(agentId);
                    const isSystem = agent?.system === true;
                    return (
                      <div key={agentId} className="flex items-center justify-between p-3 bg-indigo-50 border border-indigo-100 rounded-xl">
                        <div className="flex items-center space-x-3">
                          <div className="p-2 bg-white rounded-lg text-indigo-600 shadow-sm">
                            <Shield className="w-4 h-4" />
                          </div>
                          <div>
                            <div className="text-sm font-bold text-indigo-900 flex items-center gap-1.5">
                              {agent?.name || agentId}
                              {isSystem && (
                                <span className="inline-flex items-center gap-0.5 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-indigo-200 text-indigo-700">
                                  <Lock className="w-2.5 h-2.5" />
                                  {t('workspaceDetails.system')}
                                </span>
                              )}
                            </div>
                            <div className="text-[10px] text-indigo-400">{agentId}</div>
                          </div>
                        </div>
                        {isSystem ? (
                          <span
                            className="p-2 text-indigo-200 cursor-not-allowed"
                            title={t('workspaceDetails.systemAgentRequiredInEvery')}
                          >
                            <Lock className="w-4 h-4" />
                          </span>
                        ) : (
                          <button
                            onClick={() => handleRemoveAgent(agentId)}
                            className="p-2 text-indigo-300 hover:text-red-500 transition-colors"
                            title={t('workspaceDetails.removeFromWorkspace')}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">
                    {allowedAgentIds.length ? t('workspaceDetails.onlySystemAgentsAuthorized') : t('workspaceDetails.noAgentsAuthorizedForThis')}
                  </p>
                )}
              </div>
            </div>

            {/* Marketplace */}
            <div>
              <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
                <Plus className="w-4 h-4 mr-2" />
                {t('workspaceDetails.availableFromMarketplace')}
              </h4>
              <div className="mb-3 relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={agentSearch}
                  onChange={(e) => setAgentSearch(e.target.value)}
                  placeholder={t('workspaceDetails.findAgentByNameId')}
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg outline-none"
                />
              </div>
              <div className="grid grid-cols-1 gap-3">
                {filteredMarketAgents.length ? (
                  filteredMarketAgents.map(agent => {
                    const isDisabled = agent.default_workspace_only && name !== 'default';
                    const disabledTitle = isDisabled ? t('workspaceDetails.defaultWorkspaceOnlyTitle') : undefined;
                    return (
                      <div
                        key={agent.id}
                        className={`flex items-center justify-between p-3 border rounded-xl transition-colors ${
                          isDisabled
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
                              {isDisabled && <Lock className="w-3 h-3 text-gray-400" />}
                            </div>
                            <div className="text-[10px] text-gray-400 line-clamp-1">
                              {isDisabled ? t('workspaceDetails.defaultWorkspaceOnly') : agent.description}
                            </div>
                          </div>
                        </div>
                        <button
                          onClick={() => !isDisabled && handleAddAgent(agent.id)}
                          disabled={isDisabled}
                          title={disabledTitle}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all shadow-sm ${
                            isDisabled
                              ? 'bg-gray-100 text-gray-400 cursor-not-allowed border border-gray-200'
                              : 'bg-white border border-indigo-200 text-indigo-600 hover:bg-indigo-600 hover:text-white'
                          }`}
                        >
                          {isDisabled ? t('workspaceDetails.restricted') : t('workspaceDetails.addAgent')}
                        </button>
                      </div>
                    );
                  })
                ) : (
                  <p className="text-gray-500 text-sm italic">
                    {agentSearch.trim() ? t('workspaceDetails.noMarketAgentsMatch') : t('workspaceDetails.noMarketAgents')}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Authorized Flows */}
          <div className="mt-8 pt-6 border-t">
            <h4 className="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
              <Workflow className="w-4 h-4 mr-2" />
              {t('workspaceDetails.authorizedFlowsInWorkspace')}
            </h4>
            <div className="space-y-3">
              {ws.metadata?.allowed_flows?.length ? (
                ws.metadata.allowed_flows.map(flowId => {
                  const flow = allFlows.find(f => f.id === flowId);
                  return (
                    <div key={flowId} className="flex items-center justify-between p-3 bg-purple-50 border border-purple-100 rounded-xl">
                      <div className="flex items-center space-x-3">
                        <div className="p-2 bg-white rounded-lg text-purple-600 shadow-sm">
                          <Workflow className="w-4 h-4" />
                        </div>
                        <div>
                          <div className="text-sm font-bold text-purple-900">{flow?.name || flowId}</div>
                          <div className="text-[10px] text-purple-400">{flowId}</div>
                        </div>
                      </div>
                      <button
                        onClick={() => handleRemoveFlow(flowId)}
                        className="p-2 text-purple-300 hover:text-red-500 transition-colors"
                        title={t('workspaceDetails.revokeFlowFromWorkspace')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  );
                })
              ) : (
                <p className="text-gray-500 text-sm italic">
                  {t('workspaceDetails.noFlowsAuthorized')}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Who may reach this workspace. Renders nothing outside AUTH_MODE=multi,
          and nothing for a viewer who is neither an owner of it nor an admin.
          See components/workspace/WorkspaceMembers.jsx. */}
      {activeTab === 'agents' && <WorkspaceMembers workspace={name} />}

      {activeTab === 'settings' && (
        <WorkspaceSettingsTab workspace={name} agents={ws?.metadata?.allowed_agents || []} />
      )}

      {activeTab === 'instructions' && (
        <div className="bg-white p-6 shadow-md rounded-lg">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-indigo-500" />
                {t('workspaceDetails.workspaceInstructions')}
              </h3>
              <p className="text-sm text-gray-500 mt-0.5">
                {t('workspaceDetails.instructionsDescription')}
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
                <><Check className="w-4 h-4" /> {t('workspaceDetails.saved')}</>
              ) : (
                <><Save className="w-4 h-4" /> {instructionsSaving ? t('workspaceDetails.saving') : t('workspaceDetails.save')}</>
              )}
            </button>
          </div>
          <textarea
            value={instructionsDraft}
            onChange={(e) => setInstructionsDraft(e.target.value)}
            rows={20}
            placeholder={t('workspaceDetails.instructionsPlaceholder', { name })}
            className="w-full font-mono text-sm border border-gray-200 rounded-lg px-4 py-3 outline-none resize-y leading-relaxed"
          />
          {instructions && instructionsDraft !== instructions && (
            <p className="text-xs text-amber-600 mt-2">{t('workspaceDetails.youHaveUnsavedChanges')}</p>
          )}
        </div>
      )}

    </PageContainer>
  );
};

// ── settings tab ─────────────────────────────────────────────────────────────

// What is configured per workspace, laid out the way the Settings page lays
// out its sections: a left-hand menu and one content column. Provider keys,
// RAG, observability and logging are not here: they are not a property of a
// workspace, so they live on the Settings page only. `saves` marks the
// sections whose fields ride on the Save button; the others save themselves.
const SETTINGS_GROUPS = [
  {
    key: 'agents',
    items: [
      { id: 'execution', icon: Server, saves: true },
      { id: 'roles', icon: Repeat },
      { id: 'specialModels', icon: Sparkles },
      { id: 'toolPolicy', icon: Shield },
      { id: 'webPolicy', icon: Globe },
      { id: 'personalMemory', icon: UserRound },
      { id: 'isolation', icon: ShieldAlert },
      { id: 'tasks', icon: CheckSquare, saves: true },
    ],
  },
  {
    key: 'workspace',
    items: [
      { id: 'secrets', icon: Lock },
      { id: 'palette', icon: PaletteIcon, adminOnly: true },
    ],
  },
];

/**
 * The workspace's own settings. The override fields are drawn by the shared
 * components/settings/WorkspaceSettingsSections.jsx, the same ones the
 * Settings page shows for the workspace picked in the header, so the two
 * never drift. The open section lives in the URL (?tab=settings&section=…).
 */
function WorkspaceSettingsTab({ workspace, agents }) {
  const { t } = useI18n();
  const auth = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const s = useWorkspaceSettings(workspace);

  // The palette card renders nothing for a non-admin in multi mode, so its
  // menu entry would lead to an empty page.
  const canSeePalette = !isMultiUser(auth) || isAdmin(auth);
  const groups = SETTINGS_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.adminOnly || canSeePalette),
  }));
  const items = groups.flatMap((group) => group.items);
  const active = items.find((item) => item.id === searchParams.get('section')) || items[0];

  const open = (id) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', 'settings');
    next.set('section', id);
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="flex min-h-0 flex-1 gap-6">
      <nav className="hidden w-56 shrink-0 overflow-y-auto md:block">
        <div className="pb-8">
          {groups.map((group) => (
            <div key={group.key} className="mb-2">
              <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                {t(`workspaceDetails.settingsTab.groups.${group.key}`)}
              </p>
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = item.id === active.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => open(item.id)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                      isActive
                        ? 'bg-indigo-50 font-semibold text-indigo-600'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    {t(`workspaceDetails.settingsTab.sections.${item.id}`)}
                  </button>
                );
              })}
            </div>
          ))}
          <p className="mx-1 mt-2 rounded-lg border border-dashed border-gray-200 px-3 py-2 text-[11px] leading-snug text-gray-500">
            {t('workspaceDetails.settingsTab.globalHint')}{' '}
            <Link to="/settings" className="text-indigo-600 hover:text-indigo-800">{t('workspaceDetails.settingsTab.openSettings')}</Link>
          </p>
        </div>
      </nav>

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="max-w-3xl space-y-5 pb-16">
          {/* Mobile section selector */}
          <div className="md:hidden">
            <select
              value={active.id}
              onChange={(e) => open(e.target.value)}
              className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm"
            >
              {groups.map((group) => (
                <optgroup key={group.key} label={t(`workspaceDetails.settingsTab.groups.${group.key}`)}>
                  {group.items.map((item) => (
                    <option key={item.id} value={item.id}>{t(`workspaceDetails.settingsTab.sections.${item.id}`)}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {active.saves && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-gray-500 max-w-xl">{t('workspaceDetails.settingsTab.description')}</p>
              <SaveWorkspaceSettingsButton s={s} />
            </div>
          )}
          <WorkspaceSettingsStatus s={s} />

          {s.loading && (active.saves || active.id === 'toolPolicy') ? (
            <p className="text-sm text-gray-500 flex items-center gap-2">
              <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
            </p>
          ) : (
            <>
              {active.id === 'execution' && <ExecutionModeSection s={s} />}
              {active.id === 'toolPolicy' && <ToolPolicySection s={s} />}
              {active.id === 'tasks' && <TaskAssignmentSection s={s} />}
            </>
          )}
          {active.id === 'webPolicy' && <WorkspaceDomainPolicyCard key={workspace} workspace={workspace} />}
          {active.id === 'roles' && <WorkspaceRoles key={workspace} workspace={workspace} />}
          {active.id === 'specialModels' && <WorkspaceSpecialModels key={workspace} workspace={workspace} />}
          {active.id === 'personalMemory' && <WorkspacePersonalMemory workspace={workspace} agents={agents} />}
          {active.id === 'isolation' && <WorkspaceIsolation key={workspace} workspace={workspace} />}
          {active.id === 'secrets' && <WorkspaceSecrets workspace={workspace} agents={agents} />}
          {/* This workspace's default palette (docs/settings.md "Palette"). */}
          {active.id === 'palette' && <WorkspacePaletteDefault workspace={workspace} />}
        </div>
      </div>
    </div>
  );
}

// ── default palette ──────────────────────────────────────────────────────────

/**
 * The workspace's default palette: 2 to 4 base colors stored at
 * `settings.palette` inside the workspace's metadata, read and written
 * through the settings-overrides route every other per-workspace setting
 * already uses (routes/workspaces.py, workspace/storage.py). Visible to an
 * admin always, and to anyone outside `multi` mode, where there is only one
 * operator and every distinction between "mine" and "the workspace's"
 * default palette blurs into "the same setting".
 */
function WorkspacePaletteDefault({ workspace }) {
  const { t } = useI18n();
  const { theme, resolvedMode } = useTheme();
  const auth = useAuth();
  const visible = !isMultiUser(auth) || isAdmin(auth);

  const [overrides, setOverrides] = useState(null);
  const [draft, setDraft] = useState(PRESETS.navy);
  const [enabled, setEnabled] = useState({ neutral: false, ok: false, danger: false });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!workspace || !visible) return;
    setLoading(true);
    try {
      const { data } = await getWorkspaceSettingsOverrides(workspace);
      const all = data?.overrides && typeof data.overrides === 'object' ? data.overrides : {};
      setOverrides(all);
      const p = all.palette && typeof all.palette === 'object' && Object.keys(all.palette).length ? all.palette : null;
      setDraft({ ...PRESETS.navy, ...(p || {}) });
      setEnabled({ neutral: Boolean(p?.neutral), ok: Boolean(p?.ok), danger: Boolean(p?.danger) });
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('workspaceDetails.palette.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [workspace, visible, t]);

  useEffect(() => { load(); }, [load]);

  if (!visible) return null;

  const applyPreset = (name) => {
    setDraft(PRESETS[name]);
    setEnabled({ neutral: true, ok: true, danger: true });
  };

  const activePalette = () => {
    const payload = { brand: draft.brand };
    if (enabled.neutral) payload.neutral = draft.neutral;
    if (enabled.ok) payload.ok = draft.ok;
    if (enabled.danger) payload.danger = draft.danger;
    return payload;
  };

  // The route replaces the whole override bag, and the settings form on the
  // same tab may have saved since this card loaded: build on what is stored
  // now, not on the copy from mount.
  const freshOverrides = async () => {
    const { data } = await getWorkspaceSettingsOverrides(workspace);
    return data?.overrides && typeof data.overrides === 'object' ? data.overrides : {};
  };

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const nextOverrides = { ...(await freshOverrides()), palette: activePalette() };
      await updateWorkspaceSettingsOverrides(workspace, nextOverrides);
      setOverrides(nextOverrides);
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
      invalidateWorkspaceSummary(workspace);
      await resolvePalette(theme);
    } catch (err) {
      setError(err?.response?.data?.detail || t('workspaceDetails.palette.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError('');
    try {
      // A null names the key, so the route clears it instead of carrying it over.
      const nextOverrides = { ...(await freshOverrides()), palette: null };
      await updateWorkspaceSettingsOverrides(workspace, nextOverrides);
      delete nextOverrides.palette;
      setOverrides(nextOverrides);
      setDraft(PRESETS.navy);
      setEnabled({ neutral: false, ok: false, danger: false });
      invalidateWorkspaceSummary(workspace);
      await resolvePalette(theme);
    } catch (err) {
      setError(err?.response?.data?.detail || t('workspaceDetails.palette.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const preset = matchPreset(activePalette());
  const ramp = rampFromColor(draft.brand, { mode: resolvedMode });
  const warnings = checkPalette(activePalette(), resolvedMode);
  const hasDefault = Boolean(overrides?.palette && Object.keys(overrides.palette).length);

  return (
    <div className="bg-white p-6 shadow-md rounded-lg space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold flex items-center gap-2">
            <PaletteIcon className="w-5 h-5 text-indigo-500" />
            {t('workspaceDetails.palette.title')}
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">{t('workspaceDetails.palette.description')}</p>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      {loading ? (
        <PageLoader size="sm" />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {PRESET_ORDER.map((name) => (
              <button key={name} type="button" onClick={() => applyPreset(name)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium border ${
                  preset === name ? 'border-indigo-500 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                }`}>
                {t(`workspaceDetails.palette.presets.${name}`)}
              </button>
            ))}
            <span className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
              preset ? 'text-gray-400' : 'border border-indigo-500 bg-indigo-50 text-indigo-700'
            }`}>
              {t('workspaceDetails.palette.presets.custom')}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <PaletteColorField label={t('workspaceDetails.palette.brand')} value={draft.brand} required
              onChange={(v) => setDraft((d) => ({ ...d, brand: v }))} />
            <PaletteColorField label={t('workspaceDetails.palette.neutral')} value={draft.neutral}
              enabled={enabled.neutral} onToggle={(v) => setEnabled((e) => ({ ...e, neutral: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, neutral: v }))} />
            <PaletteColorField label={t('workspaceDetails.palette.ok')} value={draft.ok}
              enabled={enabled.ok} onToggle={(v) => setEnabled((e) => ({ ...e, ok: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, ok: v }))} />
            <PaletteColorField label={t('workspaceDetails.palette.danger')} value={draft.danger}
              enabled={enabled.danger} onToggle={(v) => setEnabled((e) => ({ ...e, danger: v }))}
              onChange={(v) => setDraft((d) => ({ ...d, danger: v }))} />
          </div>

          <div>
            <span className="block text-xs font-medium text-gray-500 mb-1">{t('workspaceDetails.palette.preview')}</span>
            <div className="flex items-center gap-1">
              {SHADES.map((s) => (
                <div key={s} className="w-6 h-6 rounded" style={{ backgroundColor: ramp[s] }} title={String(s)} />
              ))}
            </div>
          </div>

          {warnings.length > 0 && (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 space-y-1">
              {warnings.map((w) => (
                <div key={w.label}>
                  {t('workspaceDetails.palette.contrastWarning', { pair: w.label, ratio: w.ratio.toFixed(2) })}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button type="button" onClick={save} disabled={busy}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50">
              {busy ? <Loader className="w-4 h-4 animate-spin" /> : justSaved ? <Check className="w-4 h-4" /> : <PaletteIcon className="w-4 h-4" />}
              {t('workspaceDetails.palette.save')}
            </button>
            <button type="button" onClick={reset} disabled={busy || !hasDefault}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50">
              <RefreshCw className="w-3.5 h-3.5" /> {t('workspaceDetails.palette.reset')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function PaletteColorField({ label, value, onChange, required, enabled, onToggle }) {
  const active = required || enabled;
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-gray-500">{label}</span>
        {!required && (
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            <input type="checkbox" checked={Boolean(enabled)} onChange={(e) => onToggle(e.target.checked)} />
          </label>
        )}
      </div>
      <div className="flex items-center gap-2">
        <input type="color" value={value || '#000000'} disabled={!active}
          onChange={(e) => onChange(e.target.value)}
          className="w-10 h-9 rounded border border-gray-200 disabled:opacity-40" />
        <input type="text" value={value || ''} disabled={!active}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls + ' font-mono text-xs disabled:opacity-40'} />
      </div>
    </div>
  );
}

export default WorkspaceDetails;

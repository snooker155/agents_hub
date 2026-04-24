import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  getProject, updateProject, getProjectTasks,
  cloneProjectRepo, getProjectGitStatus, pullProjectRepo,
  getProjectSwaggerSpec, proxyProjectApiRequest,
  getProjectFiles, getProjectFileContent,
  getProjectSpecFromCode,
} from '../api';
import {
  FolderGit2, Globe, Server, GitBranch, Github, Gitlab, ChevronLeft,
  RefreshCw, Play, Download, ExternalLink, CheckSquare, AlertCircle,
  Edit3, Save, X, Send, Code2, BookOpen, FileText, Tag, Clock,
  ArrowUpDown, Terminal, Folder, FolderOpen, ChevronRight, ChevronDown,
  Search, ChevronUp, Zap,
} from 'lucide-react';

const TABS = ['Overview', 'Tasks', 'Progress', 'Files'];

// ── File tree helpers ──────────────────────────────────────────
const buildFileTree = (paths) => {
  const root = { type: 'dir', children: {} };
  (paths || []).forEach((rawPath) => {
    const parts = String(rawPath || '').trim().split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, idx) => {
      if (!node.children[part]) {
        node.children[part] = idx === parts.length - 1
          ? { type: 'file', name: part, path: parts.join('/') }
          : { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });
  const toArray = (node, parentPath = '') =>
    Object.keys(node.children || {})
      .sort((a, b) => {
        const aT = node.children[a].type; const bT = node.children[b].type;
        if (aT !== bT) return aT === 'dir' ? -1 : 1;
        return a.localeCompare(b);
      })
      .map((name) => {
        const child = node.children[name];
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        return child.type === 'dir'
          ? { type: 'dir', name, path: fullPath, children: toArray(child, fullPath) }
          : { type: 'file', name, path: child.path || fullPath };
      });
  return toArray(root);
};

const parentDirPaths = (filePath) => {
  const parts = String(filePath || '').split('/').filter(Boolean);
  return parts.slice(1).map((_, i) => parts.slice(0, i + 1).join('/'));
};

const STATUS_COLORS = {
  todo: 'bg-gray-100 text-gray-600',
  ready: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  blocked: 'bg-red-100 text-red-700',
  done: 'bg-green-100 text-green-700',
  stopped: 'bg-gray-100 text-gray-500',
};

const METHOD_COLORS = {
  GET: 'bg-blue-100 text-blue-700',
  POST: 'bg-green-100 text-green-700',
  PUT: 'bg-yellow-100 text-yellow-700',
  PATCH: 'bg-orange-100 text-orange-700',
  DELETE: 'bg-red-100 text-red-700',
};

export default function ProjectDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('Overview');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);

  // Tasks tab
  const [tasks, setTasks] = useState([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // Files tab
  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState('');
  const [expandedFolders, setExpandedFolders] = useState(new Set());
  const [selectedFilePath, setSelectedFilePath] = useState('');
  const [selectedFileContent, setSelectedFileContent] = useState('');
  const [selectedFileSize, setSelectedFileSize] = useState(0);
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileContentError, setFileContentError] = useState('');

  // Repo tab
  const [gitStatus, setGitStatus] = useState(null);
  const [gitLoading, setGitLoading] = useState(false);
  const [cloning, setCloning] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [gitMsg, setGitMsg] = useState('');

  // API tab
  const [swaggerSpec, setSwaggerSpec] = useState(null);
  const [swaggerLoading, setSwaggerLoading] = useState(false);
  const [apiMethod, setApiMethod] = useState('GET');
  const [apiPath, setApiPath] = useState('/');
  const [apiBody, setApiBody] = useState('');
  const [apiHeaders, setApiHeaders] = useState('');
  const [apiResponse, setApiResponse] = useState(null);
  const [apiLoading, setApiLoading] = useState(false);
  const [selectedEndpointSpec, setSelectedEndpointSpec] = useState(null);
  const [apiPathParams, setApiPathParams] = useState({});
  const [apiQueryParams, setApiQueryParams] = useState({});
  const [endpointSearch, setEndpointSearch] = useState('');
  const [expandedTags, setExpandedTags] = useState(new Set(['default']));
  const [manualBackendUrl, setManualBackendUrl] = useState('');
  const [swaggerSource, setSwaggerSource] = useState(null); // 'live' | 'code' | filename
  const [specFromCodeLoading, setSpecFromCodeLoading] = useState(false);
  const [specFromCodeError, setSpecFromCodeError] = useState('');

  const fetchProject = async () => {
    try {
      const resp = await getProject(id);
      setProject(resp.data);
      const pd = resp.data;
      const derivedBase = pd.backend?.base_url ||
        (pd.backend?.port ? `http://localhost:${pd.backend.port}` : '');
      setManualBackendUrl(prev => prev || derivedBase);
      setEditForm({
        name: resp.data.name,
        description: resp.data.description || '',
        status: resp.data.status,
        type: resp.data.type,
        tags: (resp.data.tags || []).join(', '),
        frontend_port: resp.data.frontend?.port || '',
        frontend_dev_command: resp.data.frontend?.dev_command || '',
        frontend_enabled: resp.data.frontend?.enabled || false,
        backend_port: resp.data.backend?.port || '',
        backend_swagger_path: resp.data.backend?.swagger_path || '/docs',
        backend_enabled: resp.data.backend?.enabled || false,
        repo_url: resp.data.repo?.url || '',
        repo_branch: resp.data.repo?.branch || 'main',
      });
    } catch {
      navigate('/projects');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchProject(); }, [id]);

  const loadFileContent = useCallback(async (path) => {
    if (!path) return;
    setSelectedFilePath(path);
    setFileContentLoading(true);
    setFileContentError('');
    setSelectedFileContent('');
    setSelectedFileSize(0);
    try {
      const resp = await getProjectFileContent(id, path);
      setSelectedFileContent(resp.data.content || '');
      setSelectedFileSize(resp.data.size || 0);
    } catch (e) {
      setFileContentError(e.response?.data?.detail || 'Failed to load file');
    } finally {
      setFileContentLoading(false);
    }
  }, [id]);

  const loadFiles = useCallback(async () => {
    setFilesLoading(true);
    setFilesError('');
    try {
      const resp = await getProjectFiles(id);
      const list = resp.data.files || [];
      setFiles(list);
      if (list.length) {
        const top = new Set();
        list.forEach((p) => { if (p.includes('/')) top.add(p.split('/')[0]); });
        setExpandedFolders(top);
        loadFileContent(list[0]);
      }
    } catch (e) {
      setFilesError(e.response?.data?.detail || e.message || 'Failed to load files');
    } finally {
      setFilesLoading(false);
    }
  }, [id, loadFileContent]);

  useEffect(() => {
    if (activeTab === 'Tasks' || activeTab === 'Progress') loadTasks();
    if (activeTab === 'Files') loadFiles();
    if (activeTab === 'Repository') loadGitStatus();
    if (activeTab === 'API') loadSwagger(backendBase);
  }, [activeTab]);

  const loadTasks = async () => {
    setTasksLoading(true);
    try {
      const resp = await getProjectTasks(id);
      setTasks(resp.data);
    } catch { }
    finally { setTasksLoading(false); }
  };

  const loadGitStatus = async () => {
    setGitLoading(true);
    setGitMsg('');
    try {
      const resp = await getProjectGitStatus(id);
      setGitStatus(resp.data);
    } catch (e) {
      setGitStatus(null);
      setGitMsg(e.response?.data?.detail || 'Could not fetch git status');
    } finally { setGitLoading(false); }
  };

  const handleClone = async () => {
    setCloning(true);
    setGitMsg('');
    try {
      const resp = await cloneProjectRepo(id);
      setGitMsg(`Cloned successfully to ${resp.data.path}`);
      loadGitStatus();
    } catch (e) {
      setGitMsg(e.response?.data?.detail || 'Clone failed');
    } finally { setCloning(false); }
  };

  const handlePull = async () => {
    setPulling(true);
    setGitMsg('');
    try {
      const resp = await pullProjectRepo(id);
      setGitMsg(resp.data.output || 'Pulled successfully');
      loadGitStatus();
    } catch (e) {
      setGitMsg(e.response?.data?.detail || 'Pull failed');
    } finally { setPulling(false); }
  };

  const loadSwagger = async (baseOverride) => {
    if (!project?.backend?.enabled) return;
    const base = baseOverride !== undefined ? baseOverride : backendBase;
    if (!base) return;
    setSwaggerLoading(true);
    try {
      const resp = await getProjectSwaggerSpec(id, base);
      setSwaggerSpec(resp.data);
      setSwaggerSource('live');
    } catch {
      setSwaggerSpec(null);
      setSwaggerSource(null);
    } finally { setSwaggerLoading(false); }
  };

  const loadSpecFromCode = async () => {
    setSpecFromCodeLoading(true);
    setSpecFromCodeError('');
    try {
      const resp = await getProjectSpecFromCode(id);
      const { spec, source, detected_port } = resp.data;
      setSwaggerSpec(spec);
      setSwaggerSource(source);
      if (detected_port && !manualBackendUrl) {
        setManualBackendUrl(`http://localhost:${detected_port}`);
      }
      setExpandedTags(new Set(
        spec?.paths
          ? [...new Set(Object.values(spec.paths).flatMap(methods =>
              Object.values(methods).map(ep => ep.tags?.[0] || 'default')
            ))]
          : ['default']
      ));
    } catch (e) {
      setSpecFromCodeError(e.response?.data?.detail || e.message || 'Failed to extract spec from code');
    } finally {
      setSpecFromCodeLoading(false);
    }
  };

  const handleApiRequest = async () => {
    setApiLoading(true);
    setApiResponse(null);
    try {
      let parsedHeaders = {};
      if (apiHeaders.trim()) {
        parsedHeaders = JSON.parse(apiHeaders);
      }
      let parsedBody = null;
      if (apiBody.trim() && !['GET', 'DELETE'].includes(apiMethod)) {
        parsedBody = JSON.parse(apiBody);
      }
      const resp = await proxyProjectApiRequest(id, {
        method: apiMethod,
        path: computedApiUrl,
        headers: parsedHeaders,
        body: parsedBody,
        base_url: backendBase || undefined,
      });
      setApiResponse(resp.data);
    } catch (e) {
      setApiResponse({ error: e.response?.data?.detail || e.message });
    } finally { setApiLoading(false); }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateProject(id, {
        name: editForm.name,
        description: editForm.description,
        status: editForm.status,
        type: editForm.type,
        tags: editForm.tags ? editForm.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
        frontend: {
          enabled: editForm.frontend_enabled,
          port: editForm.frontend_port ? parseInt(editForm.frontend_port) : null,
          dev_command: editForm.frontend_dev_command || null,
        },
        backend: {
          enabled: editForm.backend_enabled,
          port: editForm.backend_port ? parseInt(editForm.backend_port) : null,
          swagger_path: editForm.backend_swagger_path || '/docs',
        },
        repo: {
          url: editForm.repo_url || null,
          branch: editForm.repo_branch || 'main',
        },
      });
      setEditing(false);
      fetchProject();
    } catch (e) {
      console.error(e);
    } finally { setSaving(false); }
  };

  const frontendUrl = project?.frontend?.url ||
    (project?.frontend?.port ? `http://localhost:${project.frontend.port}` : null);
  const backendBase = project?.backend?.base_url ||
    (project?.backend?.port ? `http://localhost:${project.backend.port}` : null) ||
    manualBackendUrl || null;
  const swaggerUrl = backendBase
    ? `${backendBase}${project?.backend?.swagger_path || '/docs'}`
    : null;

  // ── API tab helpers ───────────────────────────────────────────
  const resolveSchema = useCallback((schema) => {
    if (!schema || !swaggerSpec) return schema;
    if (schema.$ref) {
      const name = schema.$ref.split('/').pop();
      return swaggerSpec.components?.schemas?.[name] || null;
    }
    return schema;
  }, [swaggerSpec]);

  const generateExample = useCallback((schema, depth = 0) => {
    if (depth > 4 || !schema) return null;
    const s = schema.$ref ? resolveSchema(schema) : schema;
    if (!s) return null;
    if (s.example !== undefined) return s.example;
    const type = s.type || (s.properties ? 'object' : s.items ? 'array' : null);
    switch (type) {
      case 'object': {
        const obj = {};
        Object.entries(s.properties || {}).forEach(([k, v]) => { obj[k] = generateExample(v, depth + 1) ?? ''; });
        return obj;
      }
      case 'array': return [generateExample(s.items, depth + 1)];
      case 'string': return s.enum?.[0] ?? (s.format === 'date-time' ? '2024-01-01T00:00:00Z' : 'string');
      case 'integer': case 'number': return s.minimum ?? 0;
      case 'boolean': return false;
      default: return null;
    }
  }, [resolveSchema]);

  const selectEndpoint = useCallback((path, method, epSpec) => {
    setSelectedEndpointSpec({ path, method, spec: epSpec });
    setApiMethod(method.toUpperCase());
    setApiPath(path);
    setApiResponse(null);
    const pathParamNames = (path.match(/\{(\w+)\}/g) || []).map(p => p.slice(1, -1));
    setApiPathParams(Object.fromEntries(pathParamNames.map(p => [p, ''])));
    const qParams = (epSpec.parameters || []).filter(p => p.in === 'query');
    setApiQueryParams(Object.fromEntries(qParams.map(p => [p.name, ''])));
    const bodySchema = epSpec.requestBody?.content?.['application/json']?.schema;
    if (bodySchema) {
      const example = generateExample(bodySchema);
      setApiBody(example !== null ? JSON.stringify(example, null, 2) : '');
    } else {
      setApiBody('');
    }
  }, [generateExample]);

  const computedApiUrl = useMemo(() => {
    let p = apiPath;
    Object.entries(apiPathParams).forEach(([k, v]) => { p = p.replace(`{${k}}`, v || `{${k}}`); });
    const qParts = Object.entries(apiQueryParams).filter(([, v]) => v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    return qParts.length ? `${p}?${qParts.join('&')}` : p;
  }, [apiPath, apiPathParams, apiQueryParams]);

  const groupedEndpoints = useMemo(() => {
    if (!swaggerSpec?.paths) return {};
    const groups = {};
    const q = endpointSearch.toLowerCase();
    Object.entries(swaggerSpec.paths).forEach(([path, methods]) => {
      Object.entries(methods).forEach(([method, spec]) => {
        if (q && !path.toLowerCase().includes(q) && !(spec.summary || '').toLowerCase().includes(q)) return;
        const tag = (spec.tags?.[0]) || 'default';
        if (!groups[tag]) groups[tag] = [];
        groups[tag].push({ path, method, spec });
      });
    });
    return groups;
  }, [swaggerSpec, endpointSearch]);

  const fileTree = useMemo(() => buildFileTree(files), [files]);

  const toggleFolder = (path) => setExpandedFolders((prev) => {
    const next = new Set(prev);
    next.has(path) ? next.delete(path) : next.add(path);
    return next;
  });

  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleFolder(node.path)}
            className="flex items-center gap-1 w-full text-left px-2 py-1 hover:bg-gray-50 rounded text-sm text-gray-700"
            style={{ paddingLeft: `${depth * 12 + 8}px` }}
          >
            {open ? <ChevronDown className="w-3 h-3 text-gray-400 shrink-0" /> : <ChevronRight className="w-3 h-3 text-gray-400 shrink-0" />}
            {open ? <FolderOpen className="w-4 h-4 text-indigo-400 shrink-0" /> : <Folder className="w-4 h-4 text-indigo-300 shrink-0" />}
            <span className="truncate">{node.name}</span>
          </button>
          {open && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }
    return (
      <button
        key={node.path}
        type="button"
        onClick={() => loadFileContent(node.path)}
        className={`flex items-center gap-1 w-full text-left px-2 py-1 rounded text-sm truncate ${selectedFilePath === node.path ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'}`}
        style={{ paddingLeft: `${depth * 12 + 20}px` }}
      >
        <FileText className="w-3.5 h-3.5 shrink-0 text-gray-400" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  });

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading…</div>;
  }
  if (!project) return null;

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Link to="/projects" className="hover:text-indigo-600 flex items-center gap-1">
          <ChevronLeft className="w-4 h-4" /> Projects
        </Link>
        <span>/</span>
        <span className="text-gray-900 font-medium">{project.name}</span>
      </div>

      {/* Project Header */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        {editing ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Name</label>
                <input
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.name}
                  onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))}
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Description</label>
                <textarea
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none"
                  rows={2}
                  value={editForm.description}
                  onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
                <select className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.status} onChange={e => setEditForm(f => ({ ...f, status: e.target.value }))}>
                  <option value="active">Active</option>
                  <option value="archived">Archived</option>
                  <option value="completed">Completed</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Type</label>
                <select className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.type} onChange={e => setEditForm(f => ({ ...f, type: e.target.value }))}>
                  <option value="general">General</option>
                  <option value="code">Code</option>
                  <option value="research">Research</option>
                  <option value="documentation">Documentation</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">Tags (comma-separated)</label>
                <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                  value={editForm.tags} onChange={e => setEditForm(f => ({ ...f, tags: e.target.value }))} />
              </div>
            </div>
            {/* Repo */}
            <div className="border border-gray-100 rounded-xl p-4 space-y-2">
              <h4 className="text-xs font-semibold text-gray-600 flex items-center gap-1"><GitBranch className="w-3.5 h-3.5" /> Repository</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">URL</label>
                  <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                    value={editForm.repo_url} onChange={e => setEditForm(f => ({ ...f, repo_url: e.target.value }))} placeholder="https://github.com/org/repo.git" />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Branch</label>
                  <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                    value={editForm.repo_branch} onChange={e => setEditForm(f => ({ ...f, repo_branch: e.target.value }))} />
                </div>
              </div>
            </div>
            {/* Frontend */}
            <div className="border border-gray-100 rounded-xl p-4 space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={editForm.frontend_enabled} onChange={e => setEditForm(f => ({ ...f, frontend_enabled: e.target.checked }))} />
                <Globe className="w-3.5 h-3.5 text-blue-600" />
                <span className="text-xs font-semibold text-gray-600">Frontend</span>
              </label>
              {editForm.frontend_enabled && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Port</label>
                    <input type="number" className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                      value={editForm.frontend_port} onChange={e => setEditForm(f => ({ ...f, frontend_port: e.target.value }))} placeholder="5173" />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Dev Command</label>
                    <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                      value={editForm.frontend_dev_command} onChange={e => setEditForm(f => ({ ...f, frontend_dev_command: e.target.value }))} placeholder="npm run dev" />
                  </div>
                </div>
              )}
            </div>
            {/* Backend */}
            <div className="border border-gray-100 rounded-xl p-4 space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={editForm.backend_enabled} onChange={e => setEditForm(f => ({ ...f, backend_enabled: e.target.checked }))} />
                <Server className="w-3.5 h-3.5 text-green-600" />
                <span className="text-xs font-semibold text-gray-600">Backend</span>
              </label>
              {editForm.backend_enabled && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Port</label>
                    <input type="number" className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                      value={editForm.backend_port} onChange={e => setEditForm(f => ({ ...f, backend_port: e.target.value }))} placeholder="8000" />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Swagger Path</label>
                    <input className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                      value={editForm.backend_swagger_path} onChange={e => setEditForm(f => ({ ...f, backend_swagger_path: e.target.value }))} placeholder="/docs" />
                  </div>
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <button onClick={handleSave} disabled={saving}
                className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium">
                <Save className="w-3.5 h-3.5" /> {saving ? 'Saving…' : 'Save'}
              </button>
              <button onClick={() => setEditing(false)}
                className="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-xl font-bold text-gray-900">{project.name}</h1>
                <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">{project.type}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  project.status === 'active' ? 'bg-green-100 text-green-700' :
                  project.status === 'completed' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                }`}>{project.status}</span>
                <span className="text-xs text-gray-400">Workspace: <strong>{project.workspace}</strong></span>
              </div>
              {project.description && <p className="text-sm text-gray-500 mt-2">{project.description}</p>}
              <div className="flex items-center gap-2 mt-3 flex-wrap">
                {project.repo?.type && project.repo.type !== 'none' && (
                  <span className="flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                    <GitBranch className="w-3 h-3" /> {project.repo.type}
                    {project.repo.url && <a href={project.repo.url} target="_blank" rel="noreferrer" className="ml-1 underline">repo</a>}
                  </span>
                )}
                {project.frontend?.enabled && (
                  <span className="flex items-center gap-1 text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
                    <Globe className="w-3 h-3" /> Frontend
                    {frontendUrl && <a href={frontendUrl} target="_blank" rel="noreferrer" className="ml-1"><ExternalLink className="w-2.5 h-2.5" /></a>}
                  </span>
                )}
                {project.backend?.enabled && (
                  <span className="flex items-center gap-1 text-xs bg-green-50 text-green-600 px-2 py-0.5 rounded-full">
                    <Server className="w-3 h-3" /> Backend
                    {swaggerUrl && <a href={swaggerUrl} target="_blank" rel="noreferrer" className="ml-1"><ExternalLink className="w-2.5 h-2.5" /></a>}
                  </span>
                )}
                {project.tags?.map(tag => (
                  <span key={tag} className="text-xs text-gray-500 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
            <button onClick={() => setEditing(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50 shrink-0">
              <Edit3 className="w-3.5 h-3.5" /> Edit
            </button>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 gap-0">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'Overview' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <CheckSquare className="w-4 h-4 text-indigo-500" /> Tasks
            </h3>
            <div className="text-3xl font-bold text-gray-900">{project.tasks_count ?? 0}</div>
            <Link to={`/tasks?project=${project.id}`} className="text-xs text-indigo-600 hover:underline mt-1 block">
              View all tasks →
            </Link>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-gray-500" /> Repository
            </h3>
            {project.repo?.type !== 'none' ? (
              <>
                <p className="text-sm text-gray-600 font-medium capitalize">{project.repo?.type}</p>
                {project.repo?.url && <p className="text-xs text-gray-400 truncate">{project.repo.url}</p>}
                <p className="text-xs text-gray-400">Branch: {project.repo?.branch}</p>
              </>
            ) : (
              <p className="text-sm text-gray-400 italic">No repo configured</p>
            )}
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
              <Clock className="w-4 h-4 text-gray-400" /> Info
            </h3>
            <p className="text-xs text-gray-500">Created</p>
            <p className="text-sm text-gray-700">{new Date(project.created_at).toLocaleDateString()}</p>
            <p className="text-xs text-gray-500 mt-2">Updated</p>
            <p className="text-sm text-gray-700">{new Date(project.updated_at).toLocaleDateString()}</p>
          </div>
        </div>
      )}

      {activeTab === 'Tasks' && (
        <div className="bg-white rounded-xl border border-gray-200">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <h3 className="font-semibold text-gray-800 text-sm">Project Tasks</h3>
            <div className="flex gap-2">
              <button onClick={loadTasks} className="p-1.5 text-gray-400 hover:text-gray-600 rounded">
                <RefreshCw className="w-4 h-4" />
              </button>
              <Link to={`/tasks`}
                className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1">
                <ExternalLink className="w-3.5 h-3.5" /> Open Task Manager
              </Link>
            </div>
          </div>
          {tasksLoading ? (
            <div className="p-8 text-center text-gray-400">Loading…</div>
          ) : tasks.length === 0 ? (
            <div className="p-8 text-center text-gray-400">
              <CheckSquare className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p>No tasks linked to this project yet</p>
              <p className="text-xs mt-1">Create tasks and set their project_id to <code>{id}</code></p>
            </div>
          ) : (
            <div className="divide-y divide-gray-50">
              {tasks.map(task => (
                <div key={task.id} className="px-5 py-3 flex items-center justify-between hover:bg-gray-50">
                  <div className="min-w-0">
                    <Link to={`/tasks/${task.id}`} className="text-sm font-medium text-gray-800 hover:text-indigo-600 truncate block">
                      {task.title}
                    </Link>
                    {task.description && (
                      <p className="text-xs text-gray-400 truncate mt-0.5">{task.description}</p>
                    )}
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ml-4 shrink-0 ${STATUS_COLORS[task.status] || 'bg-gray-100 text-gray-600'}`}>
                    {task.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'Progress' && (() => {
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
        const total = tasks.length;
        const doneCnt = tasks.filter(t => t.status === 'done').length;
        const pct = total > 0 ? Math.round((doneCnt / total) * 100) : 0;
        const byCounts = {};
        tasks.forEach(t => { byCounts[t.status] = (byCounts[t.status] || 0) + 1; });

        return (
          <div className="space-y-6">
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
                Project Progress
              </h3>
              {tasksLoading ? (
                <div className="text-center py-8 text-gray-400">Loading…</div>
              ) : total === 0 ? (
                <div className="text-center py-8 text-gray-400">
                  <CheckSquare className="w-10 h-10 mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No tasks in this project yet.</p>
                </div>
              ) : (
                <>
                  <div className="flex items-end justify-between mb-2">
                    <span className="text-3xl font-bold text-gray-900">{pct}%</span>
                    <span className="text-sm text-gray-500">{doneCnt} / {total} tasks done</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-3 mb-6">
                    <div className="bg-green-500 h-3 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-6">
                    {statusOrder.map(s => {
                      const cnt = byCounts[s] || 0;
                      if (!cnt) return null;
                      const color = statusColors[s] || { badge: 'bg-gray-100 text-gray-500' };
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

                  {/* Task list */}
                  <div className="border-t border-gray-100 pt-4 mb-6">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Tasks</h4>
                    <div className="space-y-2">
                      {tasks.map(task => {
                        const color = statusColors[task.status] || { bar: 'bg-gray-300', badge: 'bg-gray-100 text-gray-500' };
                        return (
                          <div key={task.id} className="flex items-center justify-between gap-3 py-1.5">
                            <Link
                              to={`/tasks/${task.id}`}
                              className="text-sm text-gray-800 hover:text-indigo-600 truncate flex-1"
                            >
                              {task.title}
                            </Link>
                            <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ${color.badge}`}>
                              {statusLabels[task.status] || task.status}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Activity log */}
                  {(() => {
                    const logEntries = tasks.flatMap(t =>
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
                      <div className="border-t border-gray-100 pt-4">
                        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Activity Log</h4>
                        <div className="space-y-0 max-h-80 overflow-y-auto">
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
                </>
              )}
            </div>
          </div>
        );
      })()}

      {activeTab === 'Files' && (
        <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
          <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
            <FileText className="w-5 h-5 text-indigo-500" /> Project Files
          </h3>
          {filesLoading ? (
            <p className="text-sm text-gray-400">Loading files…</p>
          ) : filesError ? (
            <p className="text-sm text-red-500">{filesError}</p>
          ) : files.length ? (
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
                    <p className="text-sm text-gray-500">Loading…</p>
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
            <p className="text-sm text-gray-400">No files found in this project folder.</p>
          )}
        </div>
      )}

      {activeTab === 'Repository' && (
        <div className="space-y-4">
          {/* Actions */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center gap-3 flex-wrap">
              <h3 className="text-sm font-semibold text-gray-700 flex-1">
                <GitBranch className="w-4 h-4 inline mr-1.5 text-gray-500" />
                {project.repo?.type !== 'none' ? `${project.repo?.type} — ${project.repo?.url || 'no URL'}` : 'No repo configured'}
              </h3>
              {project.repo?.url && (
                <>
                  <button
                    onClick={handleClone} disabled={cloning}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                  >
                    <Download className="w-3.5 h-3.5" /> {cloning ? 'Cloning…' : 'Clone'}
                  </button>
                  <button
                    onClick={handlePull} disabled={pulling}
                    className="flex items-center gap-1.5 px-3 py-1.5 border border-gray-200 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-50"
                  >
                    <ArrowUpDown className="w-3.5 h-3.5" /> {pulling ? 'Pulling…' : 'Pull'}
                  </button>
                </>
              )}
              <button onClick={loadGitStatus} className="p-1.5 text-gray-400 hover:text-gray-600 rounded">
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
            {gitMsg && (
              <div className={`mt-3 p-3 rounded-lg text-xs whitespace-pre-wrap ${
                gitMsg.toLowerCase().includes('fail') || gitMsg.toLowerCase().includes('error')
                  ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'
              }`}>{gitMsg}</div>
            )}
          </div>

          {gitLoading ? (
            <div className="text-center py-8 text-gray-400">Loading git status…</div>
          ) : gitStatus ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                  Branch: <span className="text-gray-900 normal-case font-bold">{gitStatus.branch || 'unknown'}</span>
                </h4>
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Status</h4>
                <pre className="text-xs bg-gray-50 rounded-lg p-3 whitespace-pre-wrap text-gray-700 max-h-48 overflow-y-auto">
                  {gitStatus.status || 'Clean working tree'}
                </pre>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Recent Commits</h4>
                <pre className="text-xs bg-gray-50 rounded-lg p-3 whitespace-pre-wrap text-gray-700 max-h-48 overflow-y-auto">
                  {gitStatus.recent_commits || 'No commits'}
                </pre>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-gray-400">
              <Terminal className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">No local repo found. Clone the repo first.</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'Preview' && (
        <div className="space-y-4">
          {project.frontend?.enabled && frontendUrl ? (
            <>
              <div className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-3">
                <Globe className="w-4 h-4 text-blue-500" />
                <span className="text-sm text-gray-600">{frontendUrl}</span>
                <a href={frontendUrl} target="_blank" rel="noreferrer"
                  className="ml-auto flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800">
                  Open in new tab <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden" style={{ height: '70vh' }}>
                <iframe
                  src={frontendUrl}
                  title="Frontend Preview"
                  className="w-full h-full border-0"
                  sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
                />
              </div>
            </>
          ) : (
            <div className="text-center py-16 text-gray-400">
              <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="font-medium">No frontend configured</p>
              <p className="text-sm mt-1">Enable the frontend and set a port in the Overview tab</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'API' && (
        <div className="space-y-4">
          {!project.backend?.enabled ? (
            <div className="text-center py-16 text-gray-400">
              <Server className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="font-medium">No backend configured</p>
              <p className="text-sm mt-1">Enable the backend in the Overview tab</p>
            </div>
          ) : (
            <>
            {/* Toolbar */}
            <div className="bg-white rounded-xl border border-gray-200 p-3 space-y-2">
              <div className="flex items-center gap-3">
                {/* Load from code */}
                <button
                  onClick={loadSpecFromCode}
                  disabled={specFromCodeLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50 shrink-0"
                >
                  <Code2 className="w-3.5 h-3.5" />
                  {specFromCodeLoading ? 'Extracting…' : 'Load from Code'}
                </button>
                <span className="text-gray-300 text-xs">or</span>
                {/* Live server URL + load */}
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <Server className="w-4 h-4 text-gray-400 shrink-0" />
                  <input
                    className="flex-1 border border-gray-200 rounded-lg px-3 py-1.5 text-sm font-mono min-w-0"
                    placeholder="http://localhost:8080"
                    value={manualBackendUrl}
                    onChange={e => setManualBackendUrl(e.target.value)}
                  />
                  <button
                    onClick={() => loadSwagger(manualBackendUrl)}
                    disabled={swaggerLoading}
                    className="flex items-center gap-1.5 px-3 py-1.5 border border-gray-200 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-50 shrink-0"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> Load Live Spec
                  </button>
                </div>
              </div>
              {/* Status row */}
              {(swaggerSource || specFromCodeError) && (
                <div className="flex items-center gap-2 text-xs pl-1">
                  {specFromCodeError ? (
                    <span className="text-red-500">{specFromCodeError}</span>
                  ) : swaggerSource === 'live' ? (
                    <span className="text-green-600">Loaded from live server</span>
                  ) : swaggerSource === 'dynamic_import' ? (
                    <span className="text-green-600">Extracted via dynamic import</span>
                  ) : swaggerSource ? (
                    <span className="text-green-600">Loaded from <code className="font-mono">{swaggerSource}</code></span>
                  ) : null}
                </div>
              )}
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 items-start">

              {/* ── Left: Endpoint Browser ── */}
              <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 flex flex-col">
                {/* Header */}
                <div className="p-3 border-b border-gray-100 flex items-center justify-between gap-2">
                  <div>
                    <span className="text-sm font-semibold text-gray-800">
                      {swaggerSpec?.info?.title || 'Endpoints'}
                    </span>
                    {swaggerSpec?.info?.version && (
                      <span className="ml-2 text-xs text-gray-400">{swaggerSpec.info.version}</span>
                    )}
                  </div>
                  {swaggerUrl && (
                    <a href={swaggerUrl} target="_blank" rel="noreferrer"
                      className="flex items-center gap-1 text-xs text-indigo-500 hover:text-indigo-700 shrink-0">
                      Swagger <ExternalLink className="w-3 h-3" />
                    </a>
                  )}
                </div>

                {/* Search */}
                <div className="px-3 py-2 border-b border-gray-100">
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                    <input
                      className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 rounded-lg"
                      placeholder="Search endpoints…"
                      value={endpointSearch}
                      onChange={e => setEndpointSearch(e.target.value)}
                    />
                  </div>
                </div>

                {/* Endpoint list */}
                <div className="overflow-y-auto max-h-[32rem]">
                  {swaggerLoading ? (
                    <p className="text-xs text-gray-400 p-4 text-center">Loading spec…</p>
                  ) : !swaggerSpec ? (
                    <div className="p-4 text-center text-xs text-gray-400">
                      <p>Could not load API spec.</p>
                      <button onClick={loadSwagger} className="mt-1 text-indigo-500 hover:underline">Retry</button>
                    </div>
                  ) : Object.keys(groupedEndpoints).length === 0 ? (
                    <p className="text-xs text-gray-400 p-4 text-center">No endpoints match.</p>
                  ) : (
                    Object.entries(groupedEndpoints).map(([tag, endpoints]) => {
                      const isOpen = expandedTags.has(tag);
                      return (
                        <div key={tag}>
                          <button
                            onClick={() => setExpandedTags(prev => { const n = new Set(prev); n.has(tag) ? n.delete(tag) : n.add(tag); return n; })}
                            className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 border-b border-gray-100 text-xs font-semibold text-gray-600 uppercase tracking-wide"
                          >
                            {tag}
                            {isOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                          </button>
                          {isOpen && endpoints.map(({ path, method, spec: epSpec }) => {
                            const isSelected = selectedEndpointSpec?.path === path && selectedEndpointSpec?.method === method;
                            return (
                              <button
                                key={`${method}-${path}`}
                                onClick={() => selectEndpoint(path, method, epSpec)}
                                className={`w-full flex items-start gap-2 px-3 py-2.5 border-b border-gray-50 text-left hover:bg-indigo-50 transition-colors ${isSelected ? 'bg-indigo-50 border-l-2 border-l-indigo-400' : ''}`}
                              >
                                <span className={`shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded mt-0.5 ${METHOD_COLORS[method.toUpperCase()] || 'bg-gray-100 text-gray-600'}`}>
                                  {method.toUpperCase()}
                                </span>
                                <div className="min-w-0">
                                  <div className="text-xs text-gray-800 font-mono truncate">{path}</div>
                                  {epSpec.summary && <div className="text-[10px] text-gray-400 mt-0.5 truncate">{epSpec.summary}</div>}
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* ── Right: Request Builder ── */}
              <div className="lg:col-span-3 space-y-3">

                {/* Endpoint description */}
                {selectedEndpointSpec && (
                  <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${METHOD_COLORS[selectedEndpointSpec.method.toUpperCase()] || 'bg-gray-100 text-gray-600'}`}>
                        {selectedEndpointSpec.method.toUpperCase()}
                      </span>
                      <span className="text-sm font-mono text-gray-800">{selectedEndpointSpec.path}</span>
                    </div>
                    {selectedEndpointSpec.spec.description && (
                      <p className="text-xs text-gray-500 mt-1">{selectedEndpointSpec.spec.description}</p>
                    )}
                    {/* Parameters table */}
                    {selectedEndpointSpec.spec.parameters?.length > 0 && (
                      <div className="mt-3">
                        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Parameters</div>
                        <div className="space-y-1">
                          {selectedEndpointSpec.spec.parameters.map(p => (
                            <div key={p.name} className="flex items-center gap-2 text-xs">
                              <span className="font-mono text-indigo-700 w-28 shrink-0">{p.name}</span>
                              <span className="text-gray-400 w-14 shrink-0">{p.in}</span>
                              <span className="text-gray-400">{p.schema?.type || ''}</span>
                              {p.required && <span className="text-red-400 text-[10px]">required</span>}
                              {p.description && <span className="text-gray-400 truncate">{p.description}</span>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Request builder */}
                <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
                  <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wide flex items-center gap-1.5">
                    <Zap className="w-3.5 h-3.5" /> Request
                  </h3>

                  {/* Method + path + send */}
                  <div className="flex gap-2">
                    <select
                      className="border border-gray-200 rounded-lg px-2 py-2 text-sm font-bold text-gray-700 bg-gray-50"
                      value={apiMethod}
                      onChange={e => setApiMethod(e.target.value)}
                    >
                      {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m}>{m}</option>)}
                    </select>
                    <input
                      className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono"
                      value={apiPath}
                      onChange={e => setApiPath(e.target.value)}
                      placeholder="/api/endpoint"
                    />
                    <button
                      onClick={handleApiRequest}
                      disabled={apiLoading}
                      className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium shrink-0"
                    >
                      <Send className="w-3.5 h-3.5" /> {apiLoading ? 'Sending…' : 'Send'}
                    </button>
                  </div>

                  {/* Resolved URL preview */}
                  {computedApiUrl !== apiPath && (
                    <div className="text-xs text-gray-400 font-mono bg-gray-50 px-3 py-1.5 rounded-lg">
                      {backendBase}{computedApiUrl}
                    </div>
                  )}

                  {/* Path params */}
                  {Object.keys(apiPathParams).length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-1.5">Path Parameters</div>
                      <div className="grid grid-cols-2 gap-2">
                        {Object.entries(apiPathParams).map(([k, v]) => (
                          <div key={k} className="flex items-center gap-1.5">
                            <span className="text-xs font-mono text-indigo-600 shrink-0 w-24 truncate">{`{${k}}`}</span>
                            <input
                              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs"
                              placeholder={k}
                              value={v}
                              onChange={e => setApiPathParams(prev => ({ ...prev, [k]: e.target.value }))}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Query params */}
                  {Object.keys(apiQueryParams).length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-1.5">Query Parameters</div>
                      <div className="grid grid-cols-2 gap-2">
                        {Object.entries(apiQueryParams).map(([k, v]) => (
                          <div key={k} className="flex items-center gap-1.5">
                            <span className="text-xs font-mono text-gray-600 shrink-0 w-24 truncate">{k}</span>
                            <input
                              className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs"
                              placeholder="value"
                              value={v}
                              onChange={e => setApiQueryParams(prev => ({ ...prev, [k]: e.target.value }))}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Headers + Body */}
                  <div className="grid grid-cols-1 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-500 mb-1">Headers (JSON)</label>
                      <textarea
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono resize-none"
                        rows={2}
                        placeholder={'{"Authorization": "Bearer token"}'}
                        value={apiHeaders}
                        onChange={e => setApiHeaders(e.target.value)}
                      />
                    </div>
                    {!['GET', 'DELETE'].includes(apiMethod) && (
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Body (JSON)</label>
                        <textarea
                          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono resize-none"
                          rows={5}
                          placeholder={'{"key": "value"}'}
                          value={apiBody}
                          onChange={e => setApiBody(e.target.value)}
                        />
                      </div>
                    )}
                  </div>
                </div>

                {/* Response */}
                {apiResponse && (
                  <div className="bg-white rounded-xl border border-gray-200 p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Response</span>
                      {apiResponse.status_code && (
                        <span className={`text-xs px-2 py-0.5 rounded font-bold ${
                          apiResponse.status_code < 300 ? 'bg-green-100 text-green-700' :
                          apiResponse.status_code < 400 ? 'bg-yellow-100 text-yellow-700' :
                          'bg-red-100 text-red-700'
                        }`}>{apiResponse.status_code}</span>
                      )}
                    </div>
                    <pre className="bg-gray-900 rounded-lg p-4 text-xs whitespace-pre-wrap text-green-300 font-mono max-h-80 overflow-y-auto">
                      {apiResponse.error
                        ? apiResponse.error
                        : JSON.stringify(apiResponse.body, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

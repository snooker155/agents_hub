import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import {
  getProjects, createProject, deleteProject, getWorkspaces,
  getProjectRegistryChat, clearProjectRegistryChat, stopProjectRegistryChat,
  projectRegistryChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import ImportRepoModal from '../components/ImportRepoModal';
import {
  FolderGit2,
  Folder,
  Plus,
  Trash2,
  Tag,
  Globe,
  Server,
  Code2,
  BookOpen,
  FileText,
  Search,
  GitBranch,
  Github,
  Gitlab,
  Download,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const TYPE_CONFIG = {
  general:       { label: 'General',       icon: FolderGit2, color: 'bg-gray-100 text-gray-700' },
  code:          { label: 'Code',          icon: Code2,       color: 'bg-blue-100 text-blue-700' },
  research:      { label: 'Research',      icon: BookOpen,    color: 'bg-purple-100 text-purple-700' },
  documentation: { label: 'Docs',          icon: FileText,    color: 'bg-yellow-100 text-yellow-700' },
};

const STATUS_CONFIG = {
  active:    { label: 'Active',    color: 'bg-green-100 text-green-700' },
  archived:  { label: 'Archived', color: 'bg-gray-100 text-gray-500' },
  completed: { label: 'Done',     color: 'bg-blue-100 text-blue-700' },
};

const REPO_ICONS = {
  github:    Github,
  gitlab:    Gitlab,
  bitbucket: GitBranch,
  local:     FolderGit2,
};

const DEFAULT_FORM = {
  name: '', description: '', type: 'general', workspace: '',
  repo_type: 'none', repo_url: '', repo_branch: 'main',
  has_frontend: false, frontend_port: '', frontend_dev_command: '',
  has_backend: false, backend_port: '', backend_swagger_path: '/docs',
  tags: '',
};

/**
 * The registry's own chat, as one descriptor the column and the floating page
 * chat both draw: the Project Manager creates and retires projects in the
 * current workspace while the list updates beside it.
 *
 * Distinct from the two per-project chats. Those are about the contents of one
 * project (its graph, its tasks); this one is about which projects exist.
 *
 * The callbacks are memoised on the workspace because EntityChat loads its
 * transcript in an effect keyed on them.
 */
function useRegistryChatDescriptor(workspace, onProjectsChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getProjectRegistryChat(workspace), [workspace]);
  const clearChat = useCallback(() => clearProjectRegistryChat(workspace), [workspace]);
  const stopChat = useCallback(() => stopProjectRegistryChat(workspace), [workspace]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'projects') onProjectsChanged();
  }, [onProjectsChanged]);

  return useMemo(() => ({
    scope: `projects:${workspace || ''}`,
    path: projectRegistryChatUrl(workspace),
    loadChat, clearChat, stopChat, onEvent,
    title: t('projectManager.registryChat'),
    emptyHint: t('projectManager.registryChatHint'),
    suggestions: [
      t('projectManager.chatSuggestCreate'),
      t('projectManager.chatSuggestWhatExists'),
      t('projectManager.chatSuggestRetire'),
    ],
  }), [workspace, loadChat, clearChat, stopChat, onEvent, t]);
}

export default function ProjectManager() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ ...DEFAULT_FORM, workspace: selectedWorkspace || '' });
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [showImport, setShowImport] = useState(false);
  const [workspaceNames, setWorkspaceNames] = useState([]);
  const [importNotice, setImportNotice] = useState('');
  const chat = useChatColumn(false);

  const fetchProjects = useCallback(async () => {
    try {
      const resp = await getProjects(selectedWorkspace !== 'default' ? selectedWorkspace : undefined);
      setProjects(resp.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  // The same conversation the column shows, offered to the floating panel too.
  const registryChat = useRegistryChatDescriptor(selectedWorkspace, fetchProjects);
  usePageChat(registryChat);

  useEffect(() => {
    setForm(f => ({ ...f, workspace: selectedWorkspace || '' }));
  }, [selectedWorkspace]);

  const openImport = async () => {
    setShowImport(true);
    try {
      const resp = await getWorkspaces();
      setWorkspaceNames((resp.data || []).map(w => w.name || w).filter(Boolean));
    } catch (e) {
      console.error(e);
    }
  };

  const handleImported = (result) => {
    setShowImport(false);
    const issues = result?.issues;
    const parts = [`Project "${result?.project?.name}" imported`];
    if (issues?.error) parts.push(`issue import failed: ${issues.error}`);
    else if (issues) parts.push(`${issues.imported} issue${issues.imported === 1 ? '' : 's'} imported as tasks`);
    setImportNotice(parts.join(' — '));
    setTimeout(() => setImportNotice(''), 6000);
    fetchProjects();
  };

  const handleCreate = async () => {
    if (!form.name.trim() || !form.workspace) return;
    setCreating(true);
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim(),
        type: form.type,
        workspace: form.workspace,
        tags: form.tags ? form.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
        repo: form.repo_type !== 'none' ? {
          type: form.repo_type,
          url: form.repo_url || null,
          branch: form.repo_branch || 'main',
        } : undefined,
        frontend: form.has_frontend ? {
          enabled: true,
          port: form.frontend_port ? parseInt(form.frontend_port) : null,
          dev_command: form.frontend_dev_command || null,
        } : undefined,
        backend: form.has_backend ? {
          enabled: true,
          port: form.backend_port ? parseInt(form.backend_port) : null,
          swagger_path: form.backend_swagger_path || '/docs',
        } : undefined,
      };
      await createProject(payload);
      setShowCreate(false);
      setForm({ ...DEFAULT_FORM, workspace: selectedWorkspace || '' });
      fetchProjects();
    } catch (e) {
      console.error(e);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id, name) => {
    if (!confirm(`Delete project "${name}"?`)) return;
    try {
      await deleteProject(id);
      setProjects(prev => prev.filter(p => p.id !== id));
    } catch (e) {
      console.error(e);
    }
  };

  const filtered = projects.filter(p => {
    const matchSearch = !search || p.name.toLowerCase().includes(search.toLowerCase()) ||
      (p.description || '').toLowerCase().includes(search.toLowerCase());
    const matchType = filterType === 'all' || p.type === filterType;
    return matchSearch && matchType;
  });

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={FolderGit2}
        title={t('projectManager.projects')}
        description={t('projectManager.organizeTasksAndCodeBy')}
        actions={<>
          <ChatToggle open={chat.open} onToggle={chat.toggle}
                      label={t('projectManager.registryChat')} />
          <button
            onClick={openImport}
            className="flex items-center gap-2 px-4 py-2 border border-indigo-200 text-indigo-700 rounded-lg hover:bg-indigo-50 text-sm font-medium"
          >
            <Download className="w-4 h-4" /> {t('projectManager.importFromRepo')}
          </button>
          <button
            onClick={() => { setForm({ ...DEFAULT_FORM, workspace: selectedWorkspace || '' }); setShowCreate(true); }}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium"
          >
            <Plus className="w-4 h-4" /> {t('projectManager.newProject')}
          </button>
        </>}
      />

      {importNotice && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          {importNotice}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder={t('projectManager.searchProjects')}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 w-56"
          />
        </div>
        <div className="flex gap-1">
          {['all', ...Object.keys(TYPE_CONFIG)].map(t => (
            <button
              key={t}
              onClick={() => setFilterType(t)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                filterType === t ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {t === 'all' ? 'All' : TYPE_CONFIG[t]?.label}
            </button>
          ))}
        </div>
      </div>

      {/* Only the grid sits beside the chat. The notice and the filters stay
          full width above it: inside the narrowing column they rewrap when the
          chat opens, which pushes everything below them down — the toggle is
          supposed to change one column's width and nothing else. */}
      <div className={chat.gridClass}>
        <div className={`space-y-6 ${chat.mainClass}`}>
      {loading ? (
        <div className="text-center py-16 text-gray-400">{t('projectManager.loading')}</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <FolderGit2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="font-medium">{t('projectManager.noProjectsYet')}</p>
          <p className="text-sm mt-1">{t('projectManager.createYourFirstProjectTo')}</p>
        </div>
      ) : (
        /* Fixed-width tracks, not breakpoints and not fluid ones.
           Breakpoints measure the viewport while this grid lives in a column
           that narrows when the chat opens, so a fixed column count rewrapped
           every card's text. `1fr` tracks fixed that but resized every card
           instead. A fixed track does neither: the card is always 17rem, and
           opening the chat only changes how many fit on a row. The row is
           left-aligned, so the leftover strip on the right is simply empty. */
        <div className="grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,17rem)] gap-4 items-start">
          {filtered.map(project => {
            const typeConf = TYPE_CONFIG[project.type] || TYPE_CONFIG.general;
            const statusConf = STATUS_CONFIG[project.status] || STATUS_CONFIG.active;
            const TypeIcon = typeConf.icon;
            const RepoIcon = REPO_ICONS[project.repo?.type] || GitBranch;
            return (
              <div key={project.id} className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow flex flex-col">
                <div className="p-5 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${typeConf.color}`}>
                        <TypeIcon className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <Link
                          to={`/projects/${project.id}`}
                          className="font-semibold text-gray-900 hover:text-indigo-600 transition-colors truncate block"
                        >
                          {project.name}
                        </Link>
                        <span className="text-xs text-gray-400">{project.workspace}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusConf.color}`}>
                        {statusConf.label}
                      </span>
                      <button
                        onClick={() => handleDelete(project.id, project.name)}
                        className="p-1 text-gray-300 hover:text-red-500 rounded transition-colors"
                        title={t('projectManager.deleteProject')}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {project.description && (
                    <p className="text-sm text-gray-500 mt-3 line-clamp-2">{project.description}</p>
                  )}

                  {/* Indicators */}
                  <div className="flex items-center gap-2 mt-3 flex-wrap">
                    {project.repo?.type && project.repo.type !== 'none' && (
                      <span className="flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                        <RepoIcon className="w-3 h-3" />
                        {project.repo.type}
                      </span>
                    )}
                    {project.frontend?.enabled && (
                      <span className="flex items-center gap-1 text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
                        <Globe className="w-3 h-3" /> {t('projectManager.frontend')}
                      </span>
                    )}
                    {project.backend?.enabled && (
                      <span className="flex items-center gap-1 text-xs bg-green-50 text-green-600 px-2 py-0.5 rounded-full">
                        <Server className="w-3 h-3" /> {t('projectManager.backend')}
                      </span>
                    )}
                    {project.tasks_count > 0 && (
                      <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">
                        {project.tasks_count} task{project.tasks_count !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>

                  {/* Tags */}
                  {project.tags?.length > 0 && (
                    <div className="flex items-center gap-1 mt-2 flex-wrap">
                      <Tag className="w-3 h-3 text-gray-400" />
                      {project.tags.map(tag => (
                        <span key={tag} className="text-xs text-gray-500 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
        </div>

        {chat.open && (
          <ChatColumn>
            <EntityChat {...registryChat} {...FILL_COLUMN} />
          </ChatColumn>
        )}
      </div>

      {/* Import from repo modal */}
      {showImport && (
        <ImportRepoModal
          mode="import"
          workspaces={workspaceNames}
          defaultWorkspace={selectedWorkspace && selectedWorkspace !== 'default' ? selectedWorkspace : ''}
          onClose={() => setShowImport(false)}
          onDone={handleImported}
        />
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="p-6 border-b border-gray-100">
              <h2 className="text-lg font-bold text-gray-900">{t('projectManager.newProject')}</h2>
            </div>
            <div className="p-6 space-y-4">
              {/* Basic */}
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.projectName')}</label>
                  <input
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    placeholder={t('projectManager.myProject')}
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  />
                </div>
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.description')}</label>
                  <textarea
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
                    rows={2}
                    placeholder={t('projectManager.whatIsThisProjectAbout')}
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.type')}</label>
                  <select
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    value={form.type}
                    onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
                  >
                    {Object.entries(TYPE_CONFIG).map(([k, v]) => (
                      <option key={k} value={k}>{v.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.workspace')}</label>
                  <div className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-700 font-medium flex items-center gap-2">
                    <Folder className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                    {form.workspace || <span className="text-gray-400 italic">{t('projectManager.noWorkspaceSelected')}</span>}
                  </div>
                </div>
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.tagsCommaSeparated')}</label>
                  <input
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    placeholder={t('projectManager.pythonApiMl')}
                    value={form.tags}
                    onChange={e => setForm(f => ({ ...f, tags: e.target.value }))}
                  />
                </div>
              </div>

              {/* Repo — code only */}
              {form.type === 'code' && <div className="border border-gray-200 rounded-xl p-4 space-y-3">
                <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                  <GitBranch className="w-4 h-4" /> {t('projectManager.repository')}
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">{t('projectManager.repoType')}</label>
                    <select
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                      value={form.repo_type}
                      onChange={e => setForm(f => ({ ...f, repo_type: e.target.value }))}
                    >
                      <option value="none">{t('projectManager.none')}</option>
                      <option value="github">{t('projectManager.github')}</option>
                      <option value="gitlab">{t('projectManager.gitlab')}</option>
                      <option value="bitbucket">{t('projectManager.bitbucket')}</option>
                      <option value="local">{t('projectManager.local')}</option>
                    </select>
                  </div>
                  {form.repo_type !== 'none' && (
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.branch')}</label>
                      <input
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        value={form.repo_branch}
                        onChange={e => setForm(f => ({ ...f, repo_branch: e.target.value }))}
                        placeholder="main"
                      />
                    </div>
                  )}
                  {form.repo_type !== 'none' && form.repo_type !== 'local' && (
                    <div className="col-span-2">
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.repoUrl')}</label>
                      <input
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        placeholder="https://github.com/org/repo.git"
                        value={form.repo_url}
                        onChange={e => setForm(f => ({ ...f, repo_url: e.target.value }))}
                      />
                    </div>
                  )}
                </div>
              </div>}

              {/* Frontend — code only */}
              {form.type === 'code' && <div className="border border-gray-200 rounded-xl p-4 space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.has_frontend}
                    onChange={e => setForm(f => ({ ...f, has_frontend: e.target.checked }))}
                    className="rounded"
                  />
                  <Globe className="w-4 h-4 text-blue-600" />
                  <span className="text-sm font-semibold text-gray-700">{t('projectManager.hasFrontend')}</span>
                </label>
                {form.has_frontend && (
                  <div className="grid grid-cols-2 gap-3 pt-1">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.devPort')}</label>
                      <input
                        type="number"
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        placeholder="5173"
                        value={form.frontend_port}
                        onChange={e => setForm(f => ({ ...f, frontend_port: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.devCommand')}</label>
                      <input
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        placeholder="npm run dev"
                        value={form.frontend_dev_command}
                        onChange={e => setForm(f => ({ ...f, frontend_dev_command: e.target.value }))}
                      />
                    </div>
                  </div>
                )}
              </div>}

              {/* Backend — code only */}
              {form.type === 'code' && <div className="border border-gray-200 rounded-xl p-4 space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.has_backend}
                    onChange={e => setForm(f => ({ ...f, has_backend: e.target.checked }))}
                    className="rounded"
                  />
                  <Server className="w-4 h-4 text-green-600" />
                  <span className="text-sm font-semibold text-gray-700">{t('projectManager.hasBackend')}</span>
                </label>
                {form.has_backend && (
                  <div className="grid grid-cols-2 gap-3 pt-1">
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.port')}</label>
                      <input
                        type="number"
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        placeholder="8000"
                        value={form.backend_port}
                        onChange={e => setForm(f => ({ ...f, backend_port: e.target.value }))}
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">{t('projectManager.swaggerPath')}</label>
                      <input
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
                        placeholder="/docs"
                        value={form.backend_swagger_path}
                        onChange={e => setForm(f => ({ ...f, backend_swagger_path: e.target.value }))}
                      />
                    </div>
                  </div>
                )}
              </div>}
            </div>
            <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
              <button
                onClick={() => setShowCreate(false)}
                className="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50"
              >
                {t('projectManager.cancel')}
              </button>
              <button
                onClick={handleCreate}
                disabled={creating || !form.name.trim() || !form.workspace}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium"
              >
                {creating ? t('common.creating') : t('projectManager.createProject')}
              </button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}

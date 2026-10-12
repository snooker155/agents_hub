import { useState, useEffect, useCallback, useMemo } from 'react';
import { useWorkspace } from '../components/workspace';
import {
  getProjects, createProject, getWorkspaces,
  getProjectRegistryChat, clearProjectRegistryChat, stopProjectRegistryChat,
  projectRegistryChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import ImportRepoModal from '../components/ImportRepoModal';
import ProjectCard from '../components/projects/ProjectCard';
import { PROJECT_GRID, TYPE_CONFIG } from '../components/projects/projectCardConfig';
import {
  FolderGit2,
  Folder,
  Plus,
  Search,
  GitBranch,
  Download,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

const DEFAULT_FORM = {
  name: '', description: '', type: 'general', workspace: '',
  repo_type: 'none', repo_url: '', repo_branch: 'main',
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

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const fetchProjects = useCallback(() => getProjects(selectedWorkspace !== 'default' ? selectedWorkspace : undefined)
    .then((resp) => setProjects(resp.data))
    .catch((e) => { console.error(e); })
    .finally(() => setLoading(false)), [selectedWorkspace]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  // The same conversation the column shows, offered to the floating panel too.
  const registryChat = useRegistryChatDescriptor(selectedWorkspace, fetchProjects);
  usePageChat(registryChat);

  // The create form follows the workspace switcher (adjusted while rendering).
  const [formWorkspaceFor, setFormWorkspaceFor] = useState(selectedWorkspace);
  if (selectedWorkspace !== formWorkspaceFor) {
    setFormWorkspaceFor(selectedWorkspace);
    setForm(f => ({ ...f, workspace: selectedWorkspace || '' }));
  }

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
            className="pl-9 pr-4 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none w-56"
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
        <PageLoader label={t('projectManager.loading')} />
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <FolderGit2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="font-medium">{t('projectManager.noProjectsYet')}</p>
          <p className="text-sm mt-1">{t('projectManager.createYourFirstProjectTo')}</p>
        </div>
      ) : (
        /* The grid and the card are shared with a workspace's Projects tab. */
        <div className={PROJECT_GRID}>
          {filtered.map(project => <ProjectCard key={project.id} project={project} />)}
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
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
                    placeholder={t('projectManager.myProject')}
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  />
                </div>
                <div className="col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.description')}</label>
                  <textarea
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none resize-none"
                    rows={2}
                    placeholder={t('projectManager.whatIsThisProjectAbout')}
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">{t('projectManager.type')}</label>
                  <select
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
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
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
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

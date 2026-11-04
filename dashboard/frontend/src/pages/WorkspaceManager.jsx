import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getWorkspaces, createWorkspace, deleteWorkspace } from '../api';
import { FolderPlus, Folder, Plus, Trash2 } from 'lucide-react';

import { HeaderIconButton, PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

// The first few project names of a workspace row; past that, an ellipsis
// (the full list is the cell's tooltip).
const PROJECTS_SHOWN = 3;
function projectNames(ws) {
  const names = ws.projects || [];
  if (names.length === 0) return <span className="text-gray-400">—</span>;
  const shown = names.slice(0, PROJECTS_SHOWN).join(', ');
  return names.length > PROJECTS_SHOWN ? `${shown}, …` : shown;
}

const WorkspaceManager = () => {
  const { t } = useI18n();
  const [items, setItems] = useState([]);
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState('');

  // The core sets no state until the answer arrives, so the effect may call
  // it; `fetchData` (after a delete or create) raises the spinner first. A
  // promise chain, because the React Compiler lint treats an async function
  // called from an effect as a synchronous setState.
  const loadItems = () => getWorkspaces()
    .then((resp) => setItems(resp.data || []))
    .catch((e) => {
      console.error('Error loading workspaces', e);
    })
    .then(() => setLoading(false));

  const fetchData = () => {
    setLoading(true);
    return loadItems();
  };

  useEffect(() => { loadItems(); }, []);

  const handleDelete = async (ws, e) => {
    e.stopPropagation();
    // A personal workspace is made again at its person's next sign-in, and
    // the confirmation says so (common/personal_workspace.py).
    const question = !ws.personal
      ? t('workspaceManager.confirmDelete', { name: ws.name })
      : ws.own_personal
        ? t('workspaceManager.confirmDeleteOwnPersonal')
        : t('workspaceManager.confirmDeletePersonalOf', { owner: ws.personal_label || ws.personal_of });
    if (!window.confirm(question)) return;
    try {
      await deleteWorkspace(ws.name);
      fetchData();
    } catch {
      alert(t('workspaceManager.deleteFailed'));
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    try {
      const resp = await createWorkspace(name || null);
      const created = resp?.data;
      setName('');
      setShowModal(false);
      if (created?.name) {
        navigate(`/workspaces/${encodeURIComponent(created.name)}`);
      } else {
        fetchData();
      }
    } catch {
      alert(t('workspaceManager.createFailed'));
    }
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Folder}
        title={t('workspaceManager.workspaces')}
        description={t('workspaceManager.isolatedRootsForAgentsTasks')}
        actions={
          <HeaderIconButton
            icon={Plus}
            tone="primary"
            label={t('workspaceManager.newWorkspace')}
            onClick={() => setShowModal(true)}
          />
        }
      />

      {loading ? (
        <PageLoader label={t('workspaceManager.loadingWorkspaces')} />
      ) : (
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            {/* Below md the table is the names and their actions alone, and a
                header row over them said nothing but "Name": it goes. */}
            <thead className="bg-gray-50 max-md:hidden">
              <tr>
                <th className="px-4 md:px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.name')}</th>
                <th className="hidden md:table-cell px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.agents')}</th>
                <th className="hidden md:table-cell px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.projects')}</th>
                <th className="hidden md:table-cell px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.tasks')}</th>
                <th className="px-4 md:px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider"><span className="sr-only md:not-sr-only">{t('workspaceManager.actions')}</span></th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {items.length === 0 ? (
                <tr>
                  <td colSpan="5" className="px-4 md:px-6 py-4 text-center text-gray-500">{t('workspaceManager.noWorkspaces')}</td>
                </tr>
              ) : (
                items.map(ws => (
                  <tr
                    key={ws.name}
                    className="hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/workspaces/${encodeURIComponent(ws.name)}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/workspaces/${encodeURIComponent(ws.name)}`); }}
                  >
                    <td className="px-4 md:px-6 py-3 md:py-4 text-sm font-medium text-gray-800 max-w-0 md:max-w-none w-full md:w-auto">
                      <span className="break-all">{ws.name}</span>
                      {ws.personal && (
                        <span className="ml-2 text-[11px] font-medium text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                          {ws.own_personal
                            ? t('workspaceManager.personal')
                            : t('workspaceManager.personalOf', { name: ws.personal_label || ws.personal_of })}
                        </span>
                      )}
                      {/* Below md the three count columns are hidden and
                          their values ride under the name instead, so the
                          row fits a phone without sideways scrolling. */}
                      <div className="md:hidden mt-1 text-xs font-normal text-gray-500">
                        {t('workspaceManager.agents')} {ws.agents_count ?? 0}
                        {' · '}
                        {t('workspaceManager.tasks')} {ws.tasks_count ?? 0}
                      </div>
                      {(ws.projects || []).length > 0 && (
                        <div className="md:hidden mt-0.5 text-xs font-normal text-gray-500 truncate">
                          {t('workspaceManager.projects')}: {projectNames(ws)}
                        </div>
                      )}
                    </td>
                    <td className="hidden md:table-cell px-6 py-4 text-sm font-medium text-gray-800">{ws.agents_count ?? 0}</td>
                    <td className="hidden md:table-cell px-6 py-4 text-sm text-gray-600 max-w-xs truncate" title={(ws.projects || []).join(', ')}>
                      {projectNames(ws)}
                    </td>
                    <td className="hidden md:table-cell px-6 py-4 text-sm font-medium text-gray-800">{ws.tasks_count}</td>
                    <td className="px-4 md:px-6 py-3 md:py-4 text-right align-top md:align-middle">
                      {/* The backend says who may delete: an admin any but default,
                          an owner their workspace, a person their personal one;
                          readers and editors none. */}
                      {ws.can_delete ? (
                        <button
                          onClick={(e) => handleDelete(ws, e)}
                          className="text-gray-400 hover:text-red-600 transition-colors"
                          title={t('workspaceManager.deleteWorkspace')}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      ) : ws.name === 'default' ? (
                        <span className="text-xs text-gray-400 italic whitespace-nowrap">{t('workspaceManager.default')}</span>
                      ) : null}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4 flex items-center"><FolderPlus className="w-5 h-5 mr-2"/>{t('workspaceManager.createWorkspace')}</h3>
            <form onSubmit={handleCreate}>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('workspaceManager.nameOptional')}</label>
                <input
                  type="text"
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                  placeholder={t('workspaceManager.eGMyProject')}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-gray-700 hover:text-gray-900">{t('workspaceManager.cancel')}</button>
                <button type="submit" className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700">{t('workspaceManager.create')}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default WorkspaceManager;

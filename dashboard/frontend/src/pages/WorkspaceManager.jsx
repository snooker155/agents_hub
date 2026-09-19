import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getWorkspaces, createWorkspace, deleteWorkspace } from '../api';
import { FolderPlus, Folder, Plus, Trash2 } from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const WorkspaceManager = () => {
  const { t } = useI18n();
  const [items, setItems] = useState([]);
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [name, setName] = useState('');

  const fetchData = async () => {
    setLoading(true);
    try {
      const resp = await getWorkspaces();
      setItems(resp.data || []);
    } catch (e) {
      console.error('Error loading workspaces', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const handleDelete = async (wsName, e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete workspace "${wsName}"? This cannot be undone.`)) return;
    try {
      await deleteWorkspace(wsName);
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
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium transition-colors"
          >
            <Plus className="w-4 h-4" /> {t('workspaceManager.newWorkspace')}
          </button>
        }
      />

      {loading ? (
        <div className="text-center py-10">{t('workspaceManager.loadingWorkspaces')}</div>
      ) : (
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.name')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.path')}</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.tasks')}</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">{t('workspaceManager.actions')}</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {items.length === 0 ? (
                <tr>
                  <td colSpan="4" className="px-6 py-4 text-center text-gray-500">{t('workspaceManager.noWorkspaces')}</td>
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
                    <td className="px-6 py-4 text-sm font-medium text-gray-800">{ws.name}</td>
                    <td className="px-6 py-4 text-gray-600 text-xs truncate max-w-xs">{ws.path}</td>
                    <td className="px-6 py-4 text-sm font-medium text-gray-800">{ws.tasks_count}</td>
                    <td className="px-6 py-4 text-right">
                      {ws.name !== 'default' ? (
                        <button
                          onClick={(e) => handleDelete(ws.name, e)}
                          className="text-gray-400 hover:text-red-600 transition-colors"
                          title={t('workspaceManager.deleteWorkspace')}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      ) : (
                        <span className="text-xs text-gray-400 italic">{t('workspaceManager.default')}</span>
                      )}
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
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
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

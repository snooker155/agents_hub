import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getWorkspaces, createWorkspace } from '../api';
import { FolderPlus, Folder, Plus } from 'lucide-react';

const WorkspaceManager = () => {
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

  const handleCreate = async (e) => {
    e.preventDefault();
    try {
      await createWorkspace(name || null);
      setName('');
      setShowModal(false);
      fetchData();
    } catch (e) {
      alert('Failed to create workspace');
    }
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-semibold text-gray-800 flex items-center"><Folder className="w-6 h-6 mr-2"/> Workspaces</h2>
        <button
          onClick={() => setShowModal(true)}
          className="bg-indigo-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-5 h-5 mr-2" /> New Workspace
        </button>
      </div>

      {loading ? (
        <div className="text-center py-10">Loading workspaces...</div>
      ) : (
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Path</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Tasks</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {items.length === 0 ? (
                <tr>
                  <td colSpan="4" className="px-6 py-4 text-center text-gray-500">No workspaces</td>
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
                    <td className="px-6 py-4 font-medium text-gray-900">{ws.name}</td>
                    <td className="px-6 py-4 text-gray-600 font-mono text-xs truncate max-w-xs">{ws.path}</td>
                    <td className="px-6 py-4">{ws.tasks_count}</td>
                    <td className="px-6 py-4 text-right">
                      <Link to={`/workspaces/${encodeURIComponent(ws.name)}`} className="text-indigo-600 hover:text-indigo-900" onClick={(e) => e.stopPropagation()}>
                        Open
                      </Link>
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
            <h3 className="text-xl font-bold mb-4 flex items-center"><FolderPlus className="w-5 h-5 mr-2"/>Create Workspace</h3>
            <form onSubmit={handleCreate}>
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-1">Name (optional)</label>
                <input
                  type="text"
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                  placeholder="e.g. my-project"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="flex justify-end space-x-3">
                <button type="button" onClick={() => setShowModal(false)} className="px-4 py-2 text-gray-700 hover:text-gray-900">Cancel</button>
                <button type="submit" className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkspaceManager;

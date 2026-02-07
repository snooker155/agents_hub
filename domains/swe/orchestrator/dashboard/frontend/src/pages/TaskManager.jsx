import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, CheckCircle, Clock, AlertCircle, StopCircle, ChevronRight } from 'lucide-react';
import { getTasks, createTask, getWorkspaces } from '../api';

const TaskManager = () => {
  const [tasks, setTasks] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const navigate = useNavigate();
  const [showModal, setShowModal] = useState(false);
  const [newSchema, setNewSchema] = useState({
    title: '',
    description: '',
    workspace_name: '',
    should_decompose: false
  });
  const [loading, setLoading] = useState(true);

  const fetchTasks = async () => {
    try {
      const response = await getTasks();
      try {
          const wsResp = await getWorkspaces();
          setWorkspaces(wsResp.data);
      } catch (e) { console.error("failed to fetch workspaces", e); }
      // Only show top-level tasks on the main page
      const topLevelTasks = response.data.filter(t => !t.parent_id);

      // Enhance tasks with progress
      const allTasks = response.data;
      const enhancedTasks = topLevelTasks.map(task => {
        const subtasks = allTasks.filter(st => st.parent_id === task.id);
        const doneSubtasks = subtasks.filter(st => st.status === 'done').length;
        const progress = subtasks.length > 0 ? (doneSubtasks / subtasks.length) * 100 : (task.status === 'done' ? 100 : 0);
        return { ...task, subtasks, progress };
      });

      setTasks(enhancedTasks);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching tasks:', error);
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleCreateTask = async (e) => {
    e.preventDefault();
    try {
      await createTask(newSchema);
      setNewSchema({ title: '', description: '', workspace_name: '', should_decompose: false });
      setShowModal(false);
      fetchTasks();
    } catch (error) {
      console.error('Error creating task:', error);
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'done': return <CheckCircle className="w-5 h-5 text-green-500" />;
      case 'in_progress': return <Clock className="w-5 h-5 text-blue-500" />;
      case 'blocked': return <AlertCircle className="w-5 h-5 text-red-500" />;
      case 'stopped': return <StopCircle className="w-5 h-5 text-gray-500" />;
      default: return <Clock className="w-5 h-5 text-gray-400" />;
    }
  };

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-semibold text-gray-800">Tasks</h2>
        <button
          onClick={() => setShowModal(true)}
          className="bg-indigo-600 text-white px-4 py-2 rounded-md flex items-center hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-5 h-5 mr-2" />
          New Task
        </button>
      </div>

      {loading ? (
        <div className="text-center py-10">Loading tasks...</div>
      ) : (
        <div className="bg-white shadow-md rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Title</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Workspace</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Progress</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created At</th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {tasks.length === 0 ? (
                <tr>
                  <td colSpan="5" className="px-6 py-4 text-center text-gray-500">No tasks found</td>
                </tr>
              ) : (
                tasks.map((task) => (
                  <tr
                    key={task.id}
                    className="hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/tasks/${task.id}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/tasks/${task.id}`); }}
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        {getStatusIcon(task.status)}
                        <span className="ml-2 capitalize text-sm text-gray-900">{task.status.replace('_', ' ')}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="text-sm font-medium text-gray-900">{task.title}</div>
                      <div className="text-sm text-gray-500 truncate max-w-xs">{task.description}</div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                      <span className="font-mono text-xs bg-gray-100 px-2 py-1 rounded">{task.workspace || '-'}</span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="w-full bg-gray-200 rounded-full h-2.5 max-w-[150px]">
                        <div
                          className="bg-indigo-600 h-2.5 rounded-full"
                          style={{ width: `${task.progress}%` }}
                        ></div>
                      </div>
                      <span className="text-xs text-gray-500 mt-1">{Math.round(task.progress)}% ({task.subtasks.filter(st => st.status === 'done').length}/{task.subtasks.length} subtasks)</span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {new Date(task.created_at).toLocaleString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <Link to={`/tasks/${task.id}`} className="text-indigo-600 hover:text-indigo-900 flex items-center justify-end" onClick={(e) => e.stopPropagation()}>
                        Details <ChevronRight className="w-4 h-4 ml-1" />
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Task Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg max-w-md w-full p-6">
            <h3 className="text-xl font-bold mb-4">Create New Task</h3>
            <form onSubmit={handleCreateTask}>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
                <input
                  type="text"
                  required
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                  value={newSchema.title}
                  onChange={(e) => setNewSchema({ ...newSchema, title: e.target.value })}
                />
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                  rows="3"
                  value={newSchema.description}
                  onChange={(e) => setNewSchema({ ...newSchema, description: e.target.value })}
                ></textarea>
              </div>
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Workspace (optional)</label>
                <div className="flex space-x-2">
                    <select
                        className="flex-1 border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                        value={newSchema.workspace_name}
                        onChange={(e) => setNewSchema({ ...newSchema, workspace_name: e.target.value })}
                    >
                        <option value="">-- Auto-create --</option>
                        {workspaces.map(ws => (
                            <option key={ws.name} value={ws.name}>{ws.name}</option>
                        ))}
                    </select>
                    <input
                        type="text"
                        className="flex-1 border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
                        placeholder="Or new name..."
                        onChange={(e) => setNewSchema({ ...newSchema, workspace_name: e.target.value })}
                    />
                </div>
              </div>
              <div className="mb-6 flex items-center">
                <input
                    id="should_decompose"
                    type="checkbox"
                    className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
                    checked={newSchema.should_decompose}
                    onChange={(e) => setNewSchema({ ...newSchema, should_decompose: e.target.checked })}
                />
                <label htmlFor="should_decompose" className="ml-2 block text-sm text-gray-900">
                    Automatically decompose this task
                </label>
              </div>
              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 text-gray-700 hover:text-gray-900"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default TaskManager;

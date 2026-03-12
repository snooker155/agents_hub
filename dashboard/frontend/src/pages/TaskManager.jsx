import React, { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Plus, CheckCircle, Clock, AlertCircle, StopCircle, Loader, ExternalLink, Trash2 } from 'lucide-react';
import { getTasks, createTask, getWorkspaces, deleteTask } from '../api';
import { useWorkspace } from '../components/WorkspaceContext';

const STATUS_STYLES = {
  in_progress: { bg: 'bg-blue-100', text: 'text-blue-700', icon: Loader },
  done: { bg: 'bg-green-100', text: 'text-green-700', icon: CheckCircle },
  blocked: { bg: 'bg-red-100', text: 'text-red-700', icon: AlertCircle },
  stopped: { bg: 'bg-gray-100', text: 'text-gray-600', icon: StopCircle },
  todo: { bg: 'bg-gray-100', text: 'text-gray-500', icon: Clock },
};

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'in_progress' ? 'animate-spin' : ''}`} />
      {String(status || 'unknown').replace('_', ' ')}
    </span>
  );
}

const TaskManager = () => {
  const { selectedWorkspace } = useWorkspace();
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
  const [deletingById, setDeletingById] = useState({});

  const fetchTasks = async () => {
    try {
      const response = await getTasks(selectedWorkspace);
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
  }, [selectedWorkspace]);

  const handleCreateTask = async (e) => {
    e.preventDefault();
    try {
      const taskData = { ...newSchema };
      if (!taskData.workspace_name && selectedWorkspace) {
        taskData.workspace_name = selectedWorkspace;
      }
      await createTask(taskData);
      setNewSchema({ title: '', description: '', workspace_name: '', should_decompose: false });
      setShowModal(false);
      fetchTasks();
    } catch (error) {
      console.error('Error creating task:', error);
    }
  };

  const handleDeleteTask = async (taskId, title) => {
    const confirmed = window.confirm(`Delete task "${title}"? This cannot be undone.`);
    if (!confirmed) return;

    setDeletingById((prev) => ({ ...prev, [taskId]: true }));
    try {
      await deleteTask(taskId, { cascade: true });
      fetchTasks();
    } catch (error) {
      alert('Error deleting task: ' + (error.response?.data?.detail || error.message));
    } finally {
      setDeletingById((prev) => ({ ...prev, [taskId]: false }));
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
                      <StatusBadge status={task.status} />
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
                      <div className="flex items-center justify-end gap-2">
                        <Link
                          to={`/tasks/${task.id}`}
                          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-indigo-600 hover:text-indigo-900 hover:bg-indigo-50"
                          onClick={(e) => e.stopPropagation()}
                          title="Open task details"
                          aria-label="Open task details"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </Link>
                        <button
                          type="button"
                          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-red-600 hover:text-red-700 hover:bg-red-50 disabled:opacity-50"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteTask(task.id, task.title);
                          }}
                          disabled={!!deletingById[task.id]}
                          title="Delete task"
                          aria-label="Delete task"
                        >
                          {deletingById[task.id] ? (
                            <Loader className="w-4 h-4 animate-spin" />
                          ) : (
                            <Trash2 className="w-4 h-4" />
                          )}
                        </button>
                      </div>
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

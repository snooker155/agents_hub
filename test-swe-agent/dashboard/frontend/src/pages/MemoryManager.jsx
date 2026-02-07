import React, { useState, useEffect } from 'react';
import { Database, Plus, Trash2, FileText, ChevronRight, Save, X } from 'lucide-react';
import { getSharedMemories, createSharedMemory, deleteSharedMemory, addMemoryFile, getSharedMemory } from '../api';

const MemoryManager = () => {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedMemory, setSelectedMemory] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Create Form
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');

  // File Form
  const [fileName, setFileName] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [showFileForm, setShowFileForm] = useState(false);
  const [viewingFile, setViewingFile] = useState(null);

  const fetchMemories = async () => {
    setLoading(true);
    try {
      const resp = await getSharedMemories();
      setMemories(resp.data);
    } catch (error) {
      console.error('Error fetching memories:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMemories();
  }, []);

  const handleCreateMemory = async (e) => {
    e.preventDefault();
    try {
      await createSharedMemory({ name: newName, description: newDesc });
      setNewName('');
      setNewDesc('');
      setShowCreateModal(false);
      fetchMemories();
    } catch (error) {
      console.error('Error creating memory:', error);
    }
  };

  const handleDeleteMemory = async (id) => {
    if (!window.confirm('Are you sure you want to delete this memory pool?')) return;
    try {
      await deleteSharedMemory(id);
      if (selectedMemory?.id === id) setSelectedMemory(null);
      fetchMemories();
    } catch (error) {
      console.error('Error deleting memory:', error);
    }
  };

  const selectMemory = async (id) => {
    try {
      const resp = await getSharedMemory(id);
      setSelectedMemory(resp.data);
      setViewingFile(null);
    } catch (error) {
      console.error('Error fetching memory details:', error);
    }
  };

  const handleAddFile = async (e) => {
    e.preventDefault();
    try {
      await addMemoryFile(selectedMemory.id, { name: fileName, content: fileContent });
      setFileName('');
      setFileContent('');
      setShowFileForm(false);
      selectMemory(selectedMemory.id);
    } catch (error) {
      console.error('Error adding file:', error);
    }
  };

  if (loading && memories.length === 0) return <div className="text-center py-10">Loading memory pools...</div>;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Shared Memory Management</h1>
          <p className="text-gray-500">Manage data pools shared across agents.</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 flex items-center shadow-sm transition-colors"
        >
          <Plus className="w-4 h-4 mr-2" /> Create Memory Pool
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Memory List */}
        <div className="lg:col-span-1 bg-white shadow-md rounded-lg overflow-hidden border border-gray-200">
          <div className="bg-gray-50 px-4 py-3 border-b border-gray-200 flex items-center justify-between">
            <h3 className="font-bold text-gray-700 flex items-center">
              <Database className="w-4 h-4 mr-2 text-indigo-500" /> Memory Pools
            </h3>
            <span className="bg-indigo-100 text-indigo-800 text-xs font-medium px-2 py-0.5 rounded-full">
              {memories.length}
            </span>
          </div>
          <div className="divide-y divide-gray-100">
            {memories.length === 0 ? (
              <div className="p-8 text-center text-gray-500 italic text-sm">No memory pools created yet.</div>
            ) : (
              memories.map((m) => (
                <div
                  key={m.id}
                  onClick={() => selectMemory(m.id)}
                  className={`p-4 cursor-pointer hover:bg-indigo-50 transition-colors flex items-center justify-between ${
                    selectedMemory?.id === m.id ? 'bg-indigo-50 border-l-4 border-indigo-600' : ''
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <h4 className="font-medium text-gray-900 truncate">{m.name}</h4>
                    <p className="text-xs text-gray-500 truncate">{m.description || 'No description'}</p>
                  </div>
                  <div className="flex items-center space-x-2 ml-4">
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteMemory(m.id); }}
                      className="text-gray-400 hover:text-red-600 p-1"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <ChevronRight className={`w-4 h-4 text-gray-300 ${selectedMemory?.id === m.id ? 'text-indigo-600' : ''}`} />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Selected Memory Details */}
        <div className="lg:col-span-2">
          {selectedMemory ? (
            <div className="bg-white shadow-md rounded-lg border border-gray-200 h-full flex flex-col">
              <div className="p-6 border-b border-gray-200">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h2 className="text-xl font-bold text-gray-900">{selectedMemory.name}</h2>
                    <p className="text-sm text-gray-500 font-mono mt-1">ID: {selectedMemory.id}</p>
                  </div>
                  <button
                    onClick={() => setShowFileForm(true)}
                    className="text-indigo-600 hover:text-indigo-800 text-sm font-medium flex items-center border border-indigo-200 px-3 py-1 rounded hover:bg-indigo-50"
                  >
                    <Plus className="w-4 h-4 mr-1" /> Add File
                  </button>
                </div>
                <p className="text-gray-700 text-sm">{selectedMemory.description || 'No description provided.'}</p>
              </div>

              <div className="flex-1 flex overflow-hidden">
                {/* File List */}
                <div className="w-1/3 border-r border-gray-100 overflow-y-auto">
                  <div className="px-4 py-2 bg-gray-50 text-xs font-bold text-gray-500 uppercase tracking-wider border-b border-gray-100">
                    Files / Data
                  </div>
                  <div className="divide-y divide-gray-100">
                    {selectedMemory.files.length === 0 ? (
                      <div className="p-4 text-center text-gray-400 italic text-xs">No files added.</div>
                    ) : (
                      selectedMemory.files.map((f, idx) => (
                        <div
                          key={idx}
                          onClick={() => setViewingFile(f)}
                          className={`p-3 cursor-pointer hover:bg-gray-50 flex items-center text-sm ${
                            viewingFile?.name === f.name ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600'
                          }`}
                        >
                          <FileText className="w-4 h-4 mr-2 opacity-50" />
                          <span className="truncate">{f.name}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* File Content View */}
                <div className="flex-1 bg-gray-50 overflow-y-auto">
                  {showFileForm ? (
                    <div className="p-6 bg-white h-full">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="font-bold text-gray-900">Add New File</h3>
                        <button onClick={() => setShowFileForm(false)} className="text-gray-400 hover:text-gray-600">
                          <X className="w-5 h-5" />
                        </button>
                      </div>
                      <form onSubmit={handleAddFile} className="space-y-4">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">File Name</label>
                          <input
                            type="text"
                            required
                            value={fileName}
                            onChange={(e) => setFileName(e.target.value)}
                            placeholder="config.json, documentation.txt, etc."
                            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Content (Text/JSON)</label>
                          <textarea
                            required
                            value={fileContent}
                            onChange={(e) => setFileContent(e.target.value)}
                            rows={10}
                            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                        <button
                          type="submit"
                          className="w-full bg-indigo-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-indigo-700 flex items-center justify-center shadow-sm"
                        >
                          <Save className="w-4 h-4 mr-2" /> Save File to Memory
                        </button>
                      </form>
                    </div>
                  ) : viewingFile ? (
                    <div className="p-6">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="font-bold text-gray-900 flex items-center">
                          <FileText className="w-5 h-5 mr-2 text-indigo-500" /> {viewingFile.name}
                        </h3>
                        <span className="text-[10px] bg-gray-200 text-gray-600 px-2 py-0.5 rounded font-mono uppercase">
                          {viewingFile.name.split('.').pop()}
                        </span>
                      </div>
                      <div className="bg-white border border-gray-200 rounded p-4 font-mono text-xs whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">
                        {viewingFile.content}
                      </div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <FileText className="w-12 h-12 mb-3 opacity-20" />
                      <p>Select a file to view its content or add a new one.</p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-white shadow-md rounded-lg border border-gray-200 p-12 text-center h-full flex flex-col items-center justify-center">
              <Database className="w-16 h-16 text-gray-200 mb-4" />
              <h3 className="text-lg font-medium text-gray-900 mb-2">No Memory Pool Selected</h3>
              <p className="text-gray-500 max-w-xs mx-auto">Select a memory pool from the list or create a new one to manage its data.</p>
            </div>
          )}
        </div>
      </div>

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full overflow-hidden">
            <div className="p-6 border-b border-gray-100 flex justify-between items-center bg-indigo-50">
              <h3 className="text-lg font-bold text-indigo-900">Create New Memory Pool</h3>
              <button onClick={() => setShowCreateModal(false)} className="text-indigo-400 hover:text-indigo-600">
                <X className="w-6 h-6" />
              </button>
            </div>
            <form onSubmit={handleCreateMemory} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Pool Name</label>
                <input
                  type="text"
                  required
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. Project Context, API Docs"
                  className="w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description (Optional)</label>
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="What is this data for?"
                  rows={3}
                  className="w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              <div className="flex space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 bg-gray-100 text-gray-700 px-4 py-2 rounded-md hover:bg-gray-200 font-medium transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 font-medium transition-colors shadow-sm"
                >
                  Create Pool
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default MemoryManager;

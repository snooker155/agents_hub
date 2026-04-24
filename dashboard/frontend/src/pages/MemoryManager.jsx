import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  Database, Plus, Trash2, FileText, Save, X, Upload, Cpu, Users,
  Files, ChevronRight, RefreshCw, CheckCircle, AlertCircle, Clock,
  Zap, Search, Eye, Edit3, Link2, BarChart2, FileSearch, StickyNote, KeyRound,
} from 'lucide-react';
import {
  getSharedMemories, createSharedMemory, deleteSharedMemory,
  getSharedMemory, uploadMemoryFile, deleteMemoryFile,
  processMemoryFile, getRagFiles, getRagConfig, getAgents, updateAgentMemory,
  addMemoryNote, updateMemoryNote, deleteMemoryNote,
  addMemoryKV, updateMemoryKV, deleteMemoryKV,
} from '../api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const fmt = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const fmtBytes = (b) => {
  if (!b) return '0 B';
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
};

const STATUS_CONFIG = {
  raw:        { label: 'Raw',        color: 'bg-gray-100 text-gray-600',   icon: FileText },
  processing: { label: 'Processing', color: 'bg-yellow-100 text-yellow-700', icon: Clock },
  indexed:    { label: 'Indexed',    color: 'bg-green-100 text-green-700',  icon: CheckCircle },
  failed:     { label: 'Failed',     color: 'bg-red-100 text-red-700',     icon: AlertCircle },
};

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.raw;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <Icon className="w-3 h-3" /> {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tab: Memory Pools
// ---------------------------------------------------------------------------
function PoolsTab({ memories, onRefresh, workspaceFilter }) {
  const [selected, setSelected] = useState(null);
  const [contentTab, setContentTab] = useState('files'); // 'files' | 'notes' | 'kv'

  // Files state
  const [viewingFile, setViewingFile] = useState(null);
  const [editingFile, setEditingFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [processing, setProcessing] = useState(null);
  const [chunkSize, setChunkSize] = useState(500);
  const [showProcessConfig, setShowProcessConfig] = useState(null);
  const fileInputRef = useRef(null);

  // Notes state
  const [viewingNote, setViewingNote] = useState(null);
  const [showAddNote, setShowAddNote] = useState(false);
  const [editingNote, setEditingNote] = useState(null);
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');

  // KV state
  const [editingKV, setEditingKV] = useState(null); // {key, value, description}
  const [showAddKV, setShowAddKV] = useState(false);
  const [kvKey, setKvKey] = useState('');
  const [kvValue, setKvValue] = useState('');
  const [kvDesc, setKvDesc] = useState('');

  // Pool create state
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');

  const selectPool = useCallback(async (id) => {
    try {
      const resp = await getSharedMemory(id);
      setSelected(resp.data);
      setViewingFile(null); setEditingFile(null);
      setViewingNote(null); setShowAddNote(false); setEditingNote(null);
      setShowAddKV(false); setEditingKV(null);
    } catch {}
  }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    await createSharedMemory({ name: newName, description: newDesc, workspace: workspaceFilter || null });
    setNewName(''); setNewDesc(''); setShowCreate(false);
    onRefresh();
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this memory pool?')) return;
    await deleteSharedMemory(id);
    if (selected?.id === id) setSelected(null);
    onRefresh();
  };

  // ---- Files ----
  const handleSaveEdit = async (e) => {
    e.preventDefault();
    const { updateMemoryFile } = await import('../api');
    await updateMemoryFile(selected.id, editingFile.name, { content: fileContent });
    setEditingFile(null); setFileContent('');
    await selectPool(selected.id);
  };

  const handleDeleteFile = async (name) => {
    if (!window.confirm(`Delete "${name}"?`)) return;
    await deleteMemoryFile(selected.id, name);
    if (viewingFile?.name === name) setViewingFile(null);
    await selectPool(selected.id);
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    await uploadMemoryFile(selected.id, fd);
    await selectPool(selected.id);
    e.target.value = '';
  };

  const handleProcess = async (file) => {
    setProcessing(file.name);
    setShowProcessConfig(null);
    try {
      await processMemoryFile(selected.id, file.name, { chunk_size: chunkSize, overlap: 50 });
      await selectPool(selected.id);
    } finally { setProcessing(null); }
  };

  const startEditFile = (f) => { setEditingFile(f); setFileContent(f.content); setViewingFile(null); };

  // ---- Notes ----
  const handleAddNote = async (e) => {
    e.preventDefault();
    await addMemoryNote(selected.id, { title: noteTitle, content: noteContent });
    setNoteTitle(''); setNoteContent(''); setShowAddNote(false);
    await selectPool(selected.id);
  };

  const handleSaveNote = async (e) => {
    e.preventDefault();
    await updateMemoryNote(selected.id, editingNote.id, { title: noteTitle, content: noteContent });
    setEditingNote(null); setNoteTitle(''); setNoteContent('');
    await selectPool(selected.id);
  };

  const handleDeleteNote = async (id) => {
    if (!window.confirm('Delete this note?')) return;
    await deleteMemoryNote(selected.id, id);
    if (viewingNote?.id === id) setViewingNote(null);
    await selectPool(selected.id);
  };

  const startEditNote = (n) => { setEditingNote(n); setNoteTitle(n.title); setNoteContent(n.content); setViewingNote(null); setShowAddNote(false); };

  // ---- KV ----
  const handleAddKV = async (e) => {
    e.preventDefault();
    await addMemoryKV(selected.id, { key: kvKey, value: kvValue, description: kvDesc });
    setKvKey(''); setKvValue(''); setKvDesc(''); setShowAddKV(false);
    await selectPool(selected.id);
  };

  const handleSaveKV = async (e) => {
    e.preventDefault();
    await updateMemoryKV(selected.id, editingKV.key, { value: kvValue, description: kvDesc });
    setEditingKV(null); setKvValue(''); setKvDesc('');
    await selectPool(selected.id);
  };

  const handleDeleteKV = async (key) => {
    if (!window.confirm(`Delete key "${key}"?`)) return;
    await deleteMemoryKV(selected.id, key);
    await selectPool(selected.id);
  };

  const startEditKV = (kv) => { setEditingKV(kv); setKvValue(kv.value); setKvDesc(kv.description || ''); setShowAddKV(false); };

  const files = selected?.files || [];
  const notes = selected?.notes || [];
  const kvPairs = selected?.kv_pairs || [];

  const CONTENT_TABS = [
    { id: 'files', label: 'Files', icon: FileText, count: files.length },
    { id: 'notes', label: 'Notes', icon: StickyNote, count: notes.length },
    { id: 'kv',    label: 'Key-Value', icon: KeyRound, count: kvPairs.length },
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Pool List */}
      <div className="lg:col-span-1 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-gray-50">
          <h3 className="font-semibold text-gray-700 flex items-center gap-2">
            <Database className="w-4 h-4 text-indigo-500" /> Memory Pools
            <span className="bg-indigo-100 text-indigo-700 text-xs px-2 py-0.5 rounded-full">{memories.length}</span>
          </h3>
          <button onClick={() => setShowCreate(true)} className="text-indigo-600 hover:text-indigo-800 p-1 rounded hover:bg-indigo-50" title="Create pool">
            <Plus className="w-4 h-4" />
          </button>
        </div>
        <div className="divide-y divide-gray-100 overflow-y-auto flex-1">
          {memories.length === 0 ? (
            <p className="p-6 text-center text-gray-400 text-sm italic">No memory pools yet.</p>
          ) : memories.map((m) => (
            <div
              key={m.id}
              onClick={() => selectPool(m.id)}
              className={`p-3 cursor-pointer hover:bg-indigo-50 transition-colors flex items-center justify-between ${selected?.id === m.id ? 'bg-indigo-50 border-l-4 border-indigo-500' : ''}`}
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium text-gray-900 text-sm truncate">{m.name}</p>
                <p className="text-xs text-gray-500 truncate">{m.description || 'No description'}</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {(m.files || []).length} file{(m.files || []).length !== 1 ? 's' : ''}
                  {' · '}{(m.notes || []).length} note{(m.notes || []).length !== 1 ? 's' : ''}
                  {' · '}{(m.kv_pairs || []).length} kv
                </p>
              </div>
              <div className="flex items-center gap-1 ml-2 shrink-0">
                <button onClick={(e) => { e.stopPropagation(); handleDelete(m.id); }} className="text-gray-300 hover:text-red-500 p-1" title="Delete pool">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
                <ChevronRight className={`w-4 h-4 ${selected?.id === m.id ? 'text-indigo-500' : 'text-gray-300'}`} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pool Detail */}
      <div className="lg:col-span-2 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col min-h-[500px]">
        {selected ? (
          <>
            {/* Pool header */}
            <div className="px-5 py-4 border-b border-gray-100 flex items-start justify-between">
              <div>
                <h2 className="text-lg font-bold text-gray-900">{selected.name}</h2>
                <p className="text-xs text-gray-400 mt-0.5">{selected.id}</p>
                {selected.description && <p className="text-sm text-gray-600 mt-1">{selected.description}</p>}
              </div>
              <div className="flex gap-2">
                {contentTab === 'files' && (
                  <>
                    <input ref={fileInputRef} type="file" className="hidden" accept=".txt,.md,.json,.yaml,.yml,.csv,.xml" onChange={handleUpload} />
                    <button onClick={() => fileInputRef.current?.click()} className="flex items-center gap-1 text-sm border border-gray-200 text-gray-600 px-3 py-1.5 rounded-lg hover:bg-gray-50">
                      <Upload className="w-3.5 h-3.5" /> Upload
                    </button>
                  </>
                )}
                {contentTab === 'notes' && (
                  <button onClick={() => { setShowAddNote(true); setEditingNote(null); setViewingNote(null); }} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> Add Note
                  </button>
                )}
                {contentTab === 'kv' && (
                  <button onClick={() => { setShowAddKV(true); setEditingKV(null); }} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> Add Pair
                  </button>
                )}
              </div>
            </div>

            {/* Content type tabs */}
            <div className="flex border-b border-gray-100 bg-gray-50 px-4">
              {CONTENT_TABS.map(t => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.id}
                    onClick={() => setContentTab(t.id)}
                    className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                      contentTab === t.id ? 'border-indigo-500 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" /> {t.label}
                    <span className={`text-xs px-1.5 py-0.5 rounded-full ${contentTab === t.id ? 'bg-indigo-100 text-indigo-600' : 'bg-gray-200 text-gray-500'}`}>{t.count}</span>
                  </button>
                );
              })}
            </div>

            {/* Files tab */}
            {contentTab === 'files' && (
              <div className="flex flex-1 overflow-hidden">
                <div className="w-64 shrink-0 border-r border-gray-100 overflow-y-auto">
                  {files.length === 0 ? (
                    <p className="p-4 text-xs text-gray-400 italic text-center">No files yet.</p>
                  ) : files.map((f, idx) => (
                    <div
                      key={idx}
                      onClick={() => { setViewingFile(f); setShowAddFile(false); setEditingFile(null); }}
                      className={`p-3 cursor-pointer border-b border-gray-50 hover:bg-gray-50 ${viewingFile?.name === f.name ? 'bg-indigo-50' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-1">
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm truncate font-medium ${viewingFile?.name === f.name ? 'text-indigo-700' : 'text-gray-700'}`}>{f.name}</p>
                          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                            <StatusBadge status={f.rag_status || 'raw'} />
                            {f.rag_chunks > 0 && <span className="text-xs text-gray-400">{f.rag_chunks} chunks</span>}
                          </div>
                          <p className="text-xs text-gray-400 mt-0.5">{fmtBytes(f.size_bytes)}</p>
                        </div>
                        <div className="flex flex-col gap-0.5 ml-1 shrink-0">
                          <button onClick={(e) => { e.stopPropagation(); startEditFile(f); }} className="text-gray-300 hover:text-indigo-500 p-0.5"><Edit3 className="w-3 h-3" /></button>
                          <button onClick={(e) => { e.stopPropagation(); handleDeleteFile(f.name); }} className="text-gray-300 hover:text-red-500 p-0.5"><Trash2 className="w-3 h-3" /></button>
                        </div>
                      </div>
                      {(f.rag_status === 'raw' || !f.rag_status) && (
                        <div className="mt-2">
                          {showProcessConfig === f.name ? (
                            <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                              <input type="number" value={chunkSize} onChange={e => setChunkSize(Number(e.target.value))} className="w-16 border border-gray-200 rounded px-1 py-0.5 text-xs" min={100} max={2000} title="Chunk size" />
                              <button onClick={() => handleProcess(f)} disabled={processing === f.name} className="flex items-center gap-0.5 text-xs bg-indigo-600 text-white px-1.5 py-0.5 rounded hover:bg-indigo-700 disabled:opacity-50">
                                {processing === f.name ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />} Go
                              </button>
                              <button onClick={() => setShowProcessConfig(null)} className="text-gray-400 hover:text-gray-600"><X className="w-3 h-3" /></button>
                            </div>
                          ) : (
                            <button onClick={(e) => { e.stopPropagation(); setShowProcessConfig(f.name); }} className="text-xs text-indigo-500 hover:text-indigo-700 flex items-center gap-0.5">
                              <Zap className="w-3 h-3" /> Index for RAG
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="flex-1 overflow-y-auto bg-gray-50">
                  {editingFile ? (
                    <div className="p-5 bg-white h-full">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="font-semibold text-gray-900 flex items-center gap-2"><Edit3 className="w-4 h-4 text-indigo-500" /> Edit: {editingFile.name}</h3>
                        <button onClick={() => setEditingFile(null)} className="text-gray-400 hover:text-gray-600"><X className="w-4 h-4" /></button>
                      </div>
                      <form onSubmit={handleSaveEdit} className="space-y-4">
                        <textarea value={fileContent} onChange={e => setFileContent(e.target.value)} rows={14} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                        <button type="submit" className="w-full bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center justify-center gap-2"><Save className="w-4 h-4" /> Save Changes</button>
                      </form>
                    </div>
                  ) : viewingFile ? (
                    <div className="p-5">
                      <div className="flex justify-between items-center mb-4">
                        <div>
                          <h3 className="font-semibold text-gray-900 flex items-center gap-2"><FileText className="w-4 h-4 text-indigo-500" /> {viewingFile.name}</h3>
                          <div className="flex items-center gap-3 mt-1">
                            <StatusBadge status={viewingFile.rag_status || 'raw'} />
                            {viewingFile.rag_chunks > 0 && <span className="text-xs text-gray-500">{viewingFile.rag_chunks} chunks · size {viewingFile.rag_chunk_size}</span>}
                            <span className="text-xs text-gray-400">{fmtBytes(viewingFile.size_bytes)}</span>
                            {viewingFile.added_at && <span className="text-xs text-gray-400">Added {fmt(viewingFile.added_at)}</span>}
                          </div>
                          {viewingFile.rag_processed_at && <p className="text-xs text-gray-400 mt-0.5">Processed {fmt(viewingFile.rag_processed_at)}</p>}
                        </div>
                        <button onClick={() => startEditFile(viewingFile)} className="flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50"><Edit3 className="w-3.5 h-3.5" /> Edit</button>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-lg p-4 text-xs whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">{viewingFile.content}</div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <Eye className="w-10 h-10 mb-3 opacity-20" />
                      <p className="text-sm">Select a file to view its content,<br />or upload one above.</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Notes tab */}
            {contentTab === 'notes' && (
              <div className="flex flex-1 overflow-hidden">
                <div className="w-64 shrink-0 border-r border-gray-100 overflow-y-auto">
                  {notes.length === 0 ? (
                    <p className="p-4 text-xs text-gray-400 italic text-center">No notes yet.</p>
                  ) : notes.map((n) => (
                    <div
                      key={n.id}
                      onClick={() => { setViewingNote(n); setShowAddNote(false); setEditingNote(null); }}
                      className={`p-3 cursor-pointer border-b border-gray-50 hover:bg-gray-50 ${viewingNote?.id === n.id ? 'bg-indigo-50' : ''}`}
                    >
                      <div className="flex items-start justify-between gap-1">
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm truncate font-medium ${viewingNote?.id === n.id ? 'text-indigo-700' : 'text-gray-700'}`}>{n.title}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">{n.content?.slice(0, 60)}{n.content?.length > 60 ? '…' : ''}</p>
                          <p className="text-xs text-gray-300 mt-0.5">{fmt(n.created_at)}</p>
                        </div>
                        <div className="flex flex-col gap-0.5 ml-1 shrink-0">
                          <button onClick={(e) => { e.stopPropagation(); startEditNote(n); }} className="text-gray-300 hover:text-indigo-500 p-0.5"><Edit3 className="w-3 h-3" /></button>
                          <button onClick={(e) => { e.stopPropagation(); handleDeleteNote(n.id); }} className="text-gray-300 hover:text-red-500 p-0.5"><Trash2 className="w-3 h-3" /></button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="flex-1 overflow-y-auto bg-gray-50">
                  {showAddNote || editingNote ? (
                    <div className="p-5 bg-white h-full">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="font-semibold text-gray-900">{editingNote ? 'Edit Note' : 'New Note'}</h3>
                        <button onClick={() => { setShowAddNote(false); setEditingNote(null); }} className="text-gray-400 hover:text-gray-600"><X className="w-4 h-4" /></button>
                      </div>
                      <form onSubmit={editingNote ? handleSaveNote : handleAddNote} className="space-y-4">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
                          <input required value={noteTitle} onChange={e => setNoteTitle(e.target.value)} placeholder="Note title…" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">Content</label>
                          <textarea required value={noteContent} onChange={e => setNoteContent(e.target.value)} rows={12} placeholder="Write your note here…" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                        </div>
                        <button type="submit" className="w-full bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center justify-center gap-2"><Save className="w-4 h-4" /> {editingNote ? 'Save Changes' : 'Save'}</button>
                      </form>
                    </div>
                  ) : viewingNote ? (
                    <div className="p-5">
                      <div className="flex justify-between items-center mb-4">
                        <div>
                          <h3 className="font-semibold text-gray-900 flex items-center gap-2"><StickyNote className="w-4 h-4 text-indigo-500" /> {viewingNote.title}</h3>
                          <p className="text-xs text-gray-400 mt-0.5">{fmt(viewingNote.created_at)}</p>
                        </div>
                        <button onClick={() => startEditNote(viewingNote)} className="flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50"><Edit3 className="w-3.5 h-3.5" /> Edit</button>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">{viewingNote.content}</div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <StickyNote className="w-10 h-10 mb-3 opacity-20" />
                      <p className="text-sm">Select a note to read it,<br />or add a new one.</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Key-Value tab */}
            {contentTab === 'kv' && (
              <div className="flex-1 overflow-y-auto p-5">
                {(showAddKV || editingKV) && (
                  <form onSubmit={editingKV ? handleSaveKV : handleAddKV} className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 mb-4 space-y-3">
                    <div className="flex justify-between items-center">
                      <h4 className="text-sm font-semibold text-indigo-800">{editingKV ? `Edit "${editingKV.key}"` : 'New Key-Value Pair'}</h4>
                      <button type="button" onClick={() => { setShowAddKV(false); setEditingKV(null); }} className="text-indigo-400 hover:text-indigo-600"><X className="w-4 h-4" /></button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      {!editingKV && (
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Key</label>
                          <input required value={kvKey} onChange={e => setKvKey(e.target.value)} placeholder="MY_KEY" className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono" />
                        </div>
                      )}
                      <div className={editingKV ? 'col-span-2' : ''}>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Value</label>
                        <input required value={kvValue} onChange={e => setKvValue(e.target.value)} placeholder="value" className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono" />
                      </div>
                    </div>
                    <button type="submit" className="bg-indigo-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center gap-2"><Save className="w-3.5 h-3.5" /> {editingKV ? 'Update' : 'Add'}</button>
                  </form>
                )}
                {kvPairs.length === 0 && !showAddKV ? (
                  <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-center">
                    <KeyRound className="w-10 h-10 mb-3 opacity-20" />
                    <p className="text-sm">No key-value pairs yet.<br />Add one to store structured config.</p>
                  </div>
                ) : (
                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-gray-50 border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                          <th className="px-4 py-2 text-left w-1/4">Key</th>
                          <th className="px-4 py-2 text-left w-1/3">Value</th>
                          <th className="px-4 py-2 text-left">Description</th>
                          <th className="px-4 py-2 w-16" />
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {kvPairs.map((kv) => (
                          <tr key={kv.key} className={`hover:bg-gray-50 ${editingKV?.key === kv.key ? 'bg-indigo-50' : ''}`}>
                            <td className="px-4 py-2.5 font-mono text-indigo-700 font-medium">{kv.key}</td>
                            <td className="px-4 py-2.5 font-mono text-gray-700 truncate max-w-[160px]">{kv.value}</td>
                            <td className="px-4 py-2.5 text-gray-400 text-xs truncate">{kv.description || '—'}</td>
                            <td className="px-4 py-2.5">
                              <div className="flex items-center gap-1 justify-end">
                                <button onClick={() => startEditKV(kv)} className="text-gray-300 hover:text-indigo-500 p-0.5"><Edit3 className="w-3.5 h-3.5" /></button>
                                <button onClick={() => handleDeleteKV(kv.key)} className="text-gray-300 hover:text-red-500 p-0.5"><Trash2 className="w-3.5 h-3.5" /></button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center p-12 text-center">
            <Database className="w-14 h-14 text-gray-200 mb-4" />
            <h3 className="text-lg font-semibold text-gray-600 mb-1">No Pool Selected</h3>
            <p className="text-gray-400 text-sm max-w-xs">Select a memory pool from the list or create one to get started.</p>
          </div>
        )}
      </div>

      {/* Create Pool Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-indigo-50">
              <h3 className="text-lg font-bold text-indigo-900">New Memory Pool</h3>
              <button onClick={() => setShowCreate(false)} className="text-indigo-400 hover:text-indigo-600"><X className="w-5 h-5" /></button>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input required value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Project Docs, API Reference"
                  className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="What is this for?" rows={3}
                  className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
              </div>
              <div className="flex gap-3 pt-1">
                <button type="button" onClick={() => setShowCreate(false)} className="flex-1 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-200 font-medium">Cancel</button>
                <button type="submit" className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 font-medium">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Agent Connections
// ---------------------------------------------------------------------------
function AgentsTab({ memories, workspaceFilter }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(null); // { agentId, agentName }
  const [selectedPoolId, setSelectedPoolId] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const resp = await getAgents(workspaceFilter);
        setAgents(resp.data);
      } catch {} finally { setLoading(false); }
    };
    load();
  }, [workspaceFilter]);

  const poolById = Object.fromEntries(memories.map(m => [m.id, m]));

  const handleAssign = async () => {
    setSaving(true);
    try {
      await updateAgentMemory(assigning.agentId, {
        memory_type: selectedPoolId ? 'shared' : 'none',
        memory_data: selectedPoolId || null,
      });
      const resp = await getAgents(workspaceFilter);
      setAgents(resp.data);
      setAssigning(null);
    } catch {} finally { setSaving(false); }
  };

  const agentsWithMemory = agents.filter(a => a.memory_type === 'shared' && a.memory_data);
  const poolUsage = {};
  agentsWithMemory.forEach(a => {
    const pid = a.memory_data;
    poolUsage[pid] = (poolUsage[pid] || 0) + 1;
  });

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Total Agents', value: agents.length, icon: Users, color: 'text-indigo-600 bg-indigo-50' },
          { label: 'With Memory', value: agentsWithMemory.length, icon: Link2, color: 'text-green-600 bg-green-50' },
          { label: 'Memory Pools', value: memories.length, icon: Database, color: 'text-purple-600 bg-purple-50' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4">
            <div className={`p-3 rounded-xl ${color}`}><Icon className="w-5 h-5" /></div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value}</p>
              <p className="text-xs text-gray-500">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Pool usage summary */}
      {memories.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2"><BarChart2 className="w-4 h-4 text-indigo-500" /> Pool Usage</h3>
          </div>
          <div className="divide-y divide-gray-100">
            {memories.map(m => (
              <div key={m.id} className="px-5 py-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-800">{m.name}</p>
                  <p className="text-xs text-gray-400">{(m.files || []).length} files · {(m.files || []).filter(f => f.rag_status === 'indexed').length} indexed</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-500">{poolUsage[m.id] || 0} agent{(poolUsage[m.id] || 0) !== 1 ? 's' : ''}</span>
                  <div className="w-24 bg-gray-100 rounded-full h-1.5">
                    <div className="bg-indigo-500 h-1.5 rounded-full" style={{ width: `${Math.min(100, ((poolUsage[m.id] || 0) / Math.max(1, agents.length)) * 100)}%` }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agent list */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
          <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2"><Users className="w-4 h-4 text-indigo-500" /> Agents</h3>
        </div>
        {agents.length === 0 ? (
          <p className="p-6 text-center text-gray-400 text-sm italic">No agents registered.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                <th className="px-5 py-2 text-left">Agent</th>
                <th className="px-5 py-2 text-left">Memory Pool</th>
                <th className="px-5 py-2 text-left">Status</th>
                <th className="px-5 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {agents.map(a => {
                const pool = a.memory_type === 'shared' && a.memory_data ? poolById[a.memory_data] : null;
                return (
                  <tr key={a.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <p className="font-medium text-gray-900">{a.name || a.id}</p>
                      <p className="text-xs text-gray-400">{a.domain || '—'}</p>
                    </td>
                    <td className="px-5 py-3">
                      {pool ? (
                        <div>
                          <p className="font-medium text-indigo-700">{pool.name}</p>
                          <p className="text-xs text-gray-400">{(pool.files || []).length} files</p>
                        </div>
                      ) : (
                        <span className="text-gray-400 text-xs italic">None</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {pool ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full font-medium">
                          <CheckCircle className="w-3 h-3" /> Connected
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-100 text-gray-500 text-xs rounded-full">
                          No memory
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button
                        onClick={() => { setAssigning({ agentId: a.id, agentName: a.name || a.id }); setSelectedPoolId(a.memory_data || ''); }}
                        className="text-xs border border-indigo-200 text-indigo-600 px-2 py-1 rounded-lg hover:bg-indigo-50"
                      >
                        {pool ? 'Change' : 'Assign'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Assign Modal */}
      {assigning && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-sm w-full overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-indigo-50">
              <h3 className="font-bold text-indigo-900">Assign Memory — {assigning.agentName}</h3>
              <button onClick={() => setAssigning(null)} className="text-indigo-400 hover:text-indigo-600"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Memory Pool</label>
                <select value={selectedPoolId} onChange={e => setSelectedPoolId(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                  <option value="">— None (no shared memory) —</option>
                  {memories.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </div>
              <div className="flex gap-3">
                <button onClick={() => setAssigning(null)} className="flex-1 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-200 font-medium text-sm">Cancel</button>
                <button onClick={handleAssign} disabled={saving} className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-2">
                  {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: RAG Pipeline
// ---------------------------------------------------------------------------
const PROVIDER_LABELS = {
  none: 'Not configured', openai: 'OpenAI', 'sentence-transformers': 'Sentence-Transformers',
  ollama: 'Ollama', google: 'Google',
};
const DB_LABELS = {
  none: 'None', chroma: 'ChromaDB', pinecone: 'Pinecone', qdrant: 'Qdrant',
};

function VectorDbStatusCard({ ragCfg }) {
  if (!ragCfg) return null;
  const active = ragCfg.is_configured;
  return (
    <div className={`rounded-xl border p-4 flex items-start gap-4 ${active ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200'}`}>
      <div className={`p-2.5 rounded-xl shrink-0 ${active ? 'bg-green-100' : 'bg-gray-200'}`}>
        <Database className={`w-5 h-5 ${active ? 'text-green-700' : 'text-gray-500'}`} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-semibold text-sm text-gray-800">Vector DB Status</p>
          {active
            ? <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full font-medium"><CheckCircle className="w-3 h-3" /> Active</span>
            : <span className="text-xs bg-gray-200 text-gray-600 px-2 py-0.5 rounded-full">Not configured</span>}
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-600">
          <span><span className="text-gray-400">Vector DB:</span> {DB_LABELS[ragCfg.vector_db] || ragCfg.vector_db}</span>
          <span><span className="text-gray-400">Embedding:</span> {PROVIDER_LABELS[ragCfg.embedding_provider] || ragCfg.embedding_provider}</span>
          {ragCfg.vector_db !== 'none' && ragCfg.vector_db_collection && (
            <span><span className="text-gray-400">Collection:</span> {ragCfg.vector_db_collection}</span>
          )}
          {ragCfg.embedding_provider !== 'none' && ragCfg.embedding_model && (
            <span><span className="text-gray-400">Model:</span> <code className="bg-white rounded px-1">{ragCfg.embedding_model}</code></span>
          )}
        </div>
        {!active && (
          <p className="text-xs text-gray-500 mt-2">
            Configure a vector database and embedding model in <strong>Settings → RAG & Vectors</strong> to enable full vector search.
            Without it, files are still chunked and stored as plain text.
          </p>
        )}
      </div>
    </div>
  );
}

function FileMetaChips({ f }) {
  if (!f.rag_chunks) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      <span className="inline-flex items-center gap-1 text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full">
        {f.rag_chunks} chunks
      </span>
      {f.vectorized && (
        <span className="inline-flex items-center gap-1 text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full">
          <CheckCircle className="w-3 h-3" /> vectorized
        </span>
      )}
      {f.embedding_dims > 0 && (
        <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{f.embedding_dims}d</span>
      )}
      {f.embedding_model && (
        <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-mono">{f.embedding_model}</span>
      )}
      {f.vector_db && f.vector_db !== 'none' && (
        <span className="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded-full">{DB_LABELS[f.vector_db] || f.vector_db}</span>
      )}
    </div>
  );
}

function ProcessingPanel({ state, fileName }) {
  const logRef = useRef(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [state?.log]);

  if (!state) return null;
  const pct = state.total > 0 ? Math.round((state.progress / state.total) * 100) : (state.active ? 5 : 100);
  const isError = !!state.error;

  return (
    <div className={`rounded-xl border p-4 space-y-3 ${isError ? 'bg-red-50 border-red-200' : state.done ? 'bg-green-50 border-green-200' : 'bg-indigo-50 border-indigo-200'}`}>
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          {state.active && <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />}
          {state.done && !isError && <CheckCircle className="w-4 h-4 text-green-600" />}
          {isError && <AlertCircle className="w-4 h-4 text-red-500" />}
          {fileName}
        </p>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
          isError ? 'bg-red-100 text-red-700' : state.done ? 'bg-green-100 text-green-700' : 'bg-indigo-100 text-indigo-700'
        }`}>
          {isError ? 'Failed' : state.done ? 'Done' : 'Processing…'}
        </span>
      </div>

      {/* Progress bar */}
      {(state.active || state.done) && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-gray-500">
            <span>Embedding progress</span>
            <span>{state.progress}/{state.total > 0 ? state.total : '?'} chunks</span>
          </div>
          <div className="w-full bg-white rounded-full h-2 border border-gray-200 overflow-hidden">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${isError ? 'bg-red-400' : 'bg-indigo-500'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      {/* Log */}
      {state.log?.length > 0 && (
        <div
          ref={logRef}
          className="bg-white rounded-lg border border-gray-200 p-3 text-xs font-mono space-y-1 max-h-36 overflow-y-auto"
        >
          {state.log.map((line, i) => (
            <p key={i} className={
              line.startsWith('❌') ? 'text-red-600' :
              line.startsWith('✅') ? 'text-green-700' :
              'text-gray-700'
            }>{line}</p>
          ))}
        </div>
      )}

      {/* Post-vectorization metadata */}
      {state.done && state.metadata && (
        <div className="bg-white rounded-lg border border-gray-200 p-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
          <span><span className="text-gray-400">Chunks:</span> <strong>{state.metadata.chunks ?? '—'}</strong></span>
          <span><span className="text-gray-400">Vectorized:</span> <strong>{state.metadata.vectorized ? 'Yes' : 'No (text only)'}</strong></span>
          {state.metadata.embedding_provider && (
            <span><span className="text-gray-400">Provider:</span> <strong>{PROVIDER_LABELS[state.metadata.embedding_provider] || state.metadata.embedding_provider}</strong></span>
          )}
          {state.metadata.embedding_model && (
            <span><span className="text-gray-400">Model:</span> <code className="bg-gray-100 rounded px-1">{state.metadata.embedding_model}</code></span>
          )}
          {state.metadata.embedding_dims > 0 && (
            <span><span className="text-gray-400">Dimensions:</span> <strong>{state.metadata.embedding_dims}</strong></span>
          )}
          {state.metadata.vector_db && state.metadata.vector_db !== 'none' && (
            <span><span className="text-gray-400">Vector DB:</span> <strong>{DB_LABELS[state.metadata.vector_db] || state.metadata.vector_db}</strong></span>
          )}
          {state.metadata.vector_db_collection && (
            <span><span className="text-gray-400">Collection:</span> <strong>{state.metadata.vector_db_collection}</strong></span>
          )}
          {state.metadata.upserted > 0 && (
            <span><span className="text-gray-400">Upserted:</span> <strong>{state.metadata.upserted} vectors</strong></span>
          )}
        </div>
      )}
    </div>
  );
}

function RagPipelineTab({ memories, onRefresh }) {
  const [poolId, setPoolId] = useState(memories[0]?.id || '');
  const [pool, setPool] = useState(null);
  const [chunkSize, setChunkSize] = useState(500);
  const [overlap, setOverlap] = useState(50);
  const [procState, setProcState] = useState({});
  const [dragging, setDragging] = useState(false);
  const [ragCfg, setRagCfg] = useState(null);
  const fileInputRef = useRef(null);

  const loadPool = useCallback(async (id) => {
    if (!id) return;
    try {
      const resp = await getSharedMemory(id);
      setPool(resp.data);
    } catch {}
  }, []);

  useEffect(() => {
    getRagConfig().then(r => setRagCfg(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (poolId) loadPool(poolId);
  }, [poolId, loadPool]);

  useEffect(() => {
    if (!poolId && memories.length > 0) setPoolId(memories[0].id);
  }, [memories, poolId]);

  const handleUploadFiles = async (files) => {
    if (!poolId) return;
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      try { await uploadMemoryFile(poolId, fd); } catch {}
    }
    await loadPool(poolId);
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    setDragging(false);
    await handleUploadFiles([...e.dataTransfer.files]);
  };

  const _applyEvent = (fileName, event) => {
    setProcState(s => {
      const curr = s[fileName] || { active: true, log: [], progress: 0, total: 0, done: false, error: null, metadata: null };
      const log = [...(curr.log || [])];
      switch (event.type) {
        case 'chunked':
          log.push(`📄 Split into ${event.chunks} chunks`);
          return { ...s, [fileName]: { ...curr, log, total: event.chunks } };
        case 'embedding_start':
          log.push(`🧠 Embedding with ${PROVIDER_LABELS[event.provider] || event.provider} — model: ${event.model} (${event.total} chunks)`);
          return { ...s, [fileName]: { ...curr, log } };
        case 'embedding_progress':
          return { ...s, [fileName]: { ...curr, progress: event.done, total: event.total } };
        case 'storing':
          log.push(`💾 Storing in ${DB_LABELS[event.db] || event.db} › collection "${event.collection}"`);
          return { ...s, [fileName]: { ...curr, log } };
        case 'done': {
          const meta = { ...event };
          delete meta.type;
          if (event.vectorized)
            log.push(`✅ Done — ${event.upserted ?? meta.chunks} vectors stored (${event.embedding_dims}d)`);
          else
            log.push(`✅ Done — indexed as plain text (no vector DB configured)`);
          return { ...s, [fileName]: { ...curr, log, active: false, done: true, progress: curr.total || 0, metadata: { ...meta, chunks: curr.total } } };
        }
        case 'error':
          log.push(`❌ ${event.message}`);
          return { ...s, [fileName]: { ...curr, log, active: false, error: event.message } };
        default:
          return s;
      }
    });
  };

  const handleProcess = async (fileName) => {
    setProcState(s => ({ ...s, [fileName]: { active: true, log: [], progress: 0, total: 0, done: false, error: null, metadata: null } }));
    const url = `http://localhost:8000/api/shared-memory/${poolId}/files/${encodeURIComponent(fileName)}/process-stream?chunk_size=${chunkSize}&overlap=${overlap}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try { _applyEvent(fileName, JSON.parse(line.slice(6))); } catch {}
          }
        }
      }
    } catch (err) {
      setProcState(s => ({ ...s, [fileName]: { ...(s[fileName] || {}), active: false, error: err.message, log: [...(s[fileName]?.log || []), `❌ ${err.message}`] } }));
    }
    await loadPool(poolId);
  };

  const handleProcessAll = async () => {
    const rawFiles = (pool?.files || []).filter(f => !f.rag_status || f.rag_status === 'raw');
    for (const f of rawFiles) await handleProcess(f.name);
  };

  const files = pool?.files || [];
  const rawCount = files.filter(f => !f.rag_status || f.rag_status === 'raw').length;
  const indexedCount = files.filter(f => f.rag_status === 'indexed').length;
  const vectorizedCount = files.filter(f => f.vectorized).length;
  const showVectorCols = ragCfg?.is_configured;
  const activeProcessing = Object.entries(procState).filter(([, s]) => s.active || s.done || s.error);

  return (
    <div className="space-y-5">
      {/* Vector DB status */}
      <VectorDbStatusCard ragCfg={ragCfg} />

      {/* Pool selector + config */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex-1 min-w-48">
            <label className="block text-sm font-medium text-gray-700 mb-1">Target Memory Pool</label>
            <select value={poolId} onChange={e => setPoolId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
              {memories.length === 0 ? (
                <option value="">No pools — create one first</option>
              ) : (
                memories.map(m => <option key={m.id} value={m.id}>{m.name}</option>)
              )}
            </select>
          </div>
          <div className="w-36">
            <label className="block text-sm font-medium text-gray-700 mb-1">Chunk Size (chars)</label>
            <input type="number" value={chunkSize} onChange={e => setChunkSize(Number(e.target.value))}
              min={100} max={4000} step={50}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
          </div>
          <div className="w-32">
            <label className="block text-sm font-medium text-gray-700 mb-1">Overlap</label>
            <input type="number" value={overlap} onChange={e => setOverlap(Number(e.target.value))}
              min={0} max={200} step={10}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
          </div>
          {rawCount > 0 && (
            <button onClick={handleProcessAll}
              className="flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 text-sm font-medium">
              <Zap className="w-4 h-4" /> Process All Raw ({rawCount})
            </button>
          )}
        </div>
      </div>

      {/* Stats bar */}
      {pool && (
        <div className={`grid gap-4 ${showVectorCols ? 'grid-cols-4' : 'grid-cols-3'}`}>
          {[
            { label: 'Total Files', value: files.length, color: 'bg-gray-50 text-gray-600' },
            { label: 'Raw / Pending', value: rawCount, color: 'bg-yellow-50 text-yellow-700' },
            { label: 'Indexed', value: indexedCount, color: 'bg-green-50 text-green-700' },
            ...(showVectorCols ? [{ label: 'Vectorized', value: vectorizedCount, color: 'bg-indigo-50 text-indigo-700' }] : []),
          ].map(({ label, value, color }) => (
            <div key={label} className={`rounded-xl border border-gray-200 p-4 text-center ${color}`}>
              <p className="text-2xl font-bold">{value}</p>
              <p className="text-xs font-medium mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Upload zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors cursor-pointer ${
          dragging ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-gray-50'
        }`}
        onClick={() => fileInputRef.current?.click()}
      >
        <input ref={fileInputRef} type="file" multiple className="hidden"
          accept=".txt,.md,.json,.yaml,.yml,.csv,.xml,.rst,.log"
          onChange={e => handleUploadFiles([...e.target.files]).then(() => e.target.value = '')} />
        <Upload className={`w-10 h-10 mx-auto mb-3 ${dragging ? 'text-indigo-500' : 'text-gray-300'}`} />
        <p className="text-gray-600 font-medium">Drop files here or click to upload</p>
        <p className="text-xs text-gray-400 mt-1">Supports .txt, .md, .json, .yaml, .csv, .xml and other text formats</p>
      </div>

      {/* Active / completed processing panels */}
      {activeProcessing.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
              <Zap className="w-4 h-4 text-indigo-500" /> Processing Log
            </h3>
            <button
              onClick={() => setProcState(s => {
                const next = { ...s };
                Object.keys(next).forEach(k => { if (!next[k].active) delete next[k]; });
                return next;
              })}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Clear done
            </button>
          </div>
          {activeProcessing.map(([name, state]) => (
            <ProcessingPanel key={name} fileName={name} state={state} />
          ))}
        </div>
      )}

      {/* File processing list */}
      {pool && files.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2">
              <Files className="w-4 h-4 text-indigo-500" /> Files in "{pool.name}"
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-5 py-2 text-left">File</th>
                  <th className="px-5 py-2 text-left">Status</th>
                  <th className="px-5 py-2 text-left">Size</th>
                  <th className="px-5 py-2 text-left">Processed</th>
                  <th className="px-5 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {files.map((f, i) => {
                  const ps = procState[f.name];
                  const isActive = ps?.active;
                  return (
                    <tr key={i} className={`hover:bg-gray-50 ${isActive ? 'bg-indigo-50/40' : ''}`}>
                      <td className="px-5 py-3">
                        <p className="font-medium text-gray-800">{f.name}</p>
                        <FileMetaChips f={f} />
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-1.5">
                          {isActive
                            ? <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-full font-medium animate-pulse"><RefreshCw className="w-3 h-3 animate-spin" /> Indexing</span>
                            : <StatusBadge status={f.rag_status || 'raw'} />
                          }
                          {f.rag_error && !isActive && (
                            <span title={f.rag_error} className="text-red-400 cursor-help"><AlertCircle className="w-3.5 h-3.5" /></span>
                          )}
                        </div>
                      </td>
                      <td className="px-5 py-3 text-gray-500 text-xs">{fmtBytes(f.size_bytes)}</td>
                      <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.rag_processed_at)}</td>
                      <td className="px-5 py-3 text-right">
                        <button
                          onClick={() => handleProcess(f.name)}
                          disabled={isActive}
                          className={`flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg disabled:opacity-50 ml-auto ${
                            !f.rag_status || f.rag_status === 'raw'
                              ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                              : 'border border-gray-200 text-gray-500 hover:bg-gray-50'
                          }`}
                        >
                          {isActive ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
                          {isActive ? 'Indexing…' : (!f.rag_status || f.rag_status === 'raw' ? 'Process' : 'Re-index')}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Indexed Files
// ---------------------------------------------------------------------------
function IndexedFilesTab() {
  const [ragFiles, setRagFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterPool, setFilterPool] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const resp = await getRagFiles();
        setRagFiles(resp.data);
      } catch {} finally { setLoading(false); }
    };
    load();
  }, []);

  const pools = [...new Set(ragFiles.map(f => f.memory_name))];
  const filtered = ragFiles.filter(f => {
    const matchSearch = !search || f.name.toLowerCase().includes(search.toLowerCase()) || f.memory_name.toLowerCase().includes(search.toLowerCase());
    const matchPool = !filterPool || f.memory_name === filterPool;
    return matchSearch && matchPool;
  });

  const totalChunks = filtered.reduce((sum, f) => sum + (f.rag_chunks || 0), 0);
  const vectorizedCount = filtered.filter(f => f.vectorized).length;

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
    </div>
  );

  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Indexed Files', value: ragFiles.length, icon: FileSearch, color: 'text-indigo-600 bg-indigo-50' },
          { label: 'Total Chunks', value: totalChunks, icon: BarChart2, color: 'text-green-600 bg-green-50' },
          { label: 'Vectorized', value: vectorizedCount, icon: CheckCircle, color: 'text-teal-600 bg-teal-50' },
          { label: 'Memory Pools', value: pools.length, icon: Database, color: 'text-purple-600 bg-purple-50' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4">
            <div className={`p-3 rounded-xl ${color}`}><Icon className="w-5 h-5" /></div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value}</p>
              <p className="text-xs text-gray-500">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search files…"
            className="w-full border border-gray-200 rounded-lg pl-9 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
        </div>
        <select value={filterPool} onChange={e => setFilterPool(e.target.value)}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
          <option value="">All pools</option>
          {pools.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="p-12 text-center">
            <FileSearch className="w-12 h-12 text-gray-200 mx-auto mb-3" />
            <p className="text-gray-400 text-sm">
              {ragFiles.length === 0 ? 'No files have been RAG-indexed yet.' : 'No files match your filters.'}
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider bg-gray-50">
                <th className="px-5 py-3 text-left">File</th>
                <th className="px-5 py-3 text-left">Pool</th>
                <th className="px-5 py-3 text-left">Status</th>
                <th className="px-5 py-3 text-left">Chunks</th>
                <th className="px-5 py-3 text-left">Vectorized</th>
                <th className="px-5 py-3 text-left">Embedding</th>
                <th className="px-5 py-3 text-left">Dims</th>
                <th className="px-5 py-3 text-left">Size</th>
                <th className="px-5 py-3 text-left">Processed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((f, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                      <span className="font-medium text-gray-900">{f.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className="bg-indigo-50 text-indigo-700 text-xs px-2 py-0.5 rounded-full font-medium">{f.memory_name}</span>
                  </td>
                  <td className="px-5 py-3"><StatusBadge status={f.rag_status} /></td>
                  <td className="px-5 py-3 font-medium text-gray-800">{f.rag_chunks || '—'}</td>
                  <td className="px-5 py-3">
                    {f.vectorized
                      ? <span className="inline-flex items-center gap-1 text-xs text-green-700 bg-green-50 px-2 py-0.5 rounded-full font-medium"><CheckCircle className="w-3 h-3" /> Yes</span>
                      : <span className="text-xs text-gray-400">Text only</span>}
                  </td>
                  <td className="px-5 py-3 text-gray-500 text-xs">
                    {f.embedding_model ? <code className="bg-gray-100 rounded px-1 py-0.5">{f.embedding_model}</code> : '—'}
                  </td>
                  <td className="px-5 py-3 text-gray-500 text-xs">{f.embedding_dims || '—'}</td>
                  <td className="px-5 py-3 text-gray-500 text-xs">{fmtBytes(f.size_bytes)}</td>
                  <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.rag_processed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------
const TABS = [
  { id: 'pools',   label: 'Memory Pools',     icon: Database   },
  { id: 'agents',  label: 'Agent Connections', icon: Users      },
  { id: 'rag',     label: 'RAG Pipeline',      icon: Cpu        },
  { id: 'indexed', label: 'Indexed Files',     icon: FileSearch },
];

export default function MemoryManager() {
  const { selectedWorkspace, workspaceFilter } = useWorkspace();
  const [activeTab, setActiveTab] = useState('pools');
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getSharedMemories(workspaceFilter);
      setMemories(resp.data);
    } catch {} finally { setLoading(false); }
  }, [workspaceFilter]);

  useEffect(() => { fetchMemories(); }, [fetchMemories]);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Shared Memory</h1>
          <p className="text-sm text-gray-500 mt-0.5">Manage agent memory pools, RAG indexing pipeline, and data access.</p>
        </div>
        <button onClick={fetchMemories} className="flex items-center gap-2 border border-gray-200 text-gray-600 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-500' : ''}`} /> Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === tab.id ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span className="hidden sm:inline">{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === 'pools'   && <PoolsTab memories={memories} onRefresh={fetchMemories} workspaceFilter={workspaceFilter} />}
      {activeTab === 'agents'  && <AgentsTab memories={memories} workspaceFilter={workspaceFilter} />}
      {activeTab === 'rag'     && <RagPipelineTab memories={memories} onRefresh={fetchMemories} />}
      {activeTab === 'indexed' && <IndexedFilesTab />}
    </div>
  );
}

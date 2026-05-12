import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  Database, Plus, Trash2, FileText, Save, X, Upload, Cpu, Users,
  Files, ChevronRight, RefreshCw, CheckCircle, AlertCircle, Clock,
  Zap, Search, Edit3, Link2, BarChart2, FileSearch, StickyNote, Layers,
  Activity, BookOpen, Share2, Sparkles,
} from 'lucide-react';
import {
  getSharedMemories, createSharedMemory, deleteSharedMemory,
  getSharedMemory, uploadMemoryFile, deleteMemoryFile,
  indexMemoryFile, deindexMemoryFile, listMemoryFiles,
  getRagConfig, getAgents, updateAgentMemory,
  addMemoryNote, updateMemoryNote, deleteMemoryNote,
  upsertMemoryStructuredSlot, deleteMemoryStructuredSlot,
  listMemoryEpisodes, getMemoryEpisodesStats, deleteMemoryEpisode,
  getMemoryGraph, getMemoryGraphStats, linkMemoryGraph,
  deleteMemoryGraphNode, deleteMemoryGraphEdge, extractMemoryGraph,
} from '../api';

const EPISODE_KIND_COLOR = {
  interaction: 'bg-blue-100 text-blue-700',
  task:        'bg-indigo-100 text-indigo-700',
  decision:    'bg-purple-100 text-purple-700',
  error:       'bg-red-100 text-red-700',
  observation: 'bg-gray-100 text-gray-700',
};
const EPISODE_OUTCOME_COLOR = {
  success: 'bg-green-100 text-green-700',
  failure: 'bg-red-100 text-red-700',
  partial: 'bg-yellow-100 text-yellow-700',
  'n/a':   'bg-gray-100 text-gray-500',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const fmt = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const STATUS_CONFIG = {
  raw:        { label: 'Raw',        color: 'bg-gray-100 text-gray-600',    icon: FileText },
  processing: { label: 'Processing', color: 'bg-yellow-100 text-yellow-700', icon: Clock },
  indexed:    { label: 'Indexed',    color: 'bg-green-100 text-green-700',   icon: CheckCircle },
  failed:     { label: 'Failed',     color: 'bg-red-100 text-red-700',      icon: AlertCircle },
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
  const [contentTab, setContentTab] = useState('structured'); // 'structured' | 'notes'

  // Notes state
  const [viewingNote, setViewingNote] = useState(null);
  const [showAddNote, setShowAddNote] = useState(false);
  const [editingNote, setEditingNote] = useState(null);
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');

  // Pool create state
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');

  // Structured slot state
  const [editingSlot, setEditingSlot] = useState(null);
  const [showAddSlot, setShowAddSlot] = useState(false);
  const [slotName, setSlotName] = useState('');
  const [slotMode, setSlotMode] = useState('structured'); // 'simple' | 'structured' | 'json'
  const [slotSimpleValue, setSlotSimpleValue] = useState('');
  const [slotFields, setSlotFields] = useState([{ key: '', value: '' }]);
  const [slotDataRaw, setSlotDataRaw] = useState('{}');
  const [slotDataError, setSlotDataError] = useState('');

  const selectPool = useCallback(async (id) => {
    try {
      const resp = await getSharedMemory(id);
      setSelected(resp.data);
      setViewingNote(null); setShowAddNote(false); setEditingNote(null);
      setShowAddSlot(false); setEditingSlot(null);
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

  const startEditNote = (n) => {
    setEditingNote(n); setNoteTitle(n.title); setNoteContent(n.content);
    setViewingNote(null); setShowAddNote(false);
  };

  // ---- Structured slots ----
  const isSimpleSlot = (data) => data && Object.keys(data).length === 1 && 'value' in data;

  const resetSlotForm = () => {
    setSlotName('');
    setSlotMode('structured');
    setSlotSimpleValue('');
    setSlotFields([{ key: '', value: '' }]);
    setSlotDataRaw('{}');
    setSlotDataError('');
  };

  const openAddSlot = () => {
    setShowAddSlot(true); setEditingSlot(null);
    resetSlotForm();
  };

  const startEditSlot = (slot, data) => {
    setEditingSlot({ slot });
    setSlotName(slot);
    setSlotDataError(''); setShowAddSlot(false);
    if (isSimpleSlot(data)) {
      setSlotMode('simple');
      setSlotSimpleValue(String(data.value ?? ''));
      setSlotFields([{ key: '', value: '' }]);
      setSlotDataRaw(JSON.stringify(data, null, 2));
    } else {
      setSlotMode('structured');
      const entries = Object.entries(data || {});
      setSlotFields(entries.length ? entries.map(([k, v]) => ({
        key: k,
        value: typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''),
      })) : [{ key: '', value: '' }]);
      setSlotSimpleValue('');
      setSlotDataRaw(JSON.stringify(data || {}, null, 2));
    }
  };

  const coerceFieldValue = (raw) => {
    const trimmed = (raw ?? '').trim();
    if (trimmed === '') return '';
    if (trimmed === 'true') return true;
    if (trimmed === 'false') return false;
    if (trimmed === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { return JSON.parse(trimmed); } catch { /* fall through to string */ }
    }
    return raw;
  };

  const handleSaveSlot = async (e) => {
    e.preventDefault();
    let data;
    if (slotMode === 'simple') {
      data = { value: slotSimpleValue };
    } else if (slotMode === 'json') {
      try { data = JSON.parse(slotDataRaw); }
      catch { setSlotDataError('Invalid JSON'); return; }
      if (typeof data !== 'object' || Array.isArray(data) || data === null) {
        setSlotDataError('Top-level value must be a JSON object'); return;
      }
    } else {
      data = {};
      for (const { key, value } of slotFields) {
        const k = key.trim();
        if (!k) continue;
        if (k in data) { setSlotDataError(`Duplicate key: ${k}`); return; }
        data[k] = coerceFieldValue(value);
      }
      if (Object.keys(data).length === 0) {
        setSlotDataError('Add at least one field'); return;
      }
    }
    setSlotDataError('');
    await upsertMemoryStructuredSlot(selected.id, slotName, { data });
    setShowAddSlot(false); setEditingSlot(null);
    resetSlotForm();
    await selectPool(selected.id);
  };

  const updateSlotField = (idx, patch) => {
    setSlotFields(prev => prev.map((f, i) => i === idx ? { ...f, ...patch } : f));
  };
  const addSlotField = () => setSlotFields(prev => [...prev, { key: '', value: '' }]);
  const removeSlotField = (idx) => {
    setSlotFields(prev => prev.length === 1 ? [{ key: '', value: '' }] : prev.filter((_, i) => i !== idx));
  };

  const switchSlotMode = (next) => {
    if (next === slotMode) return;
    if (next === 'json') {
      // Serialize current draft into the JSON textarea
      let preview = {};
      if (slotMode === 'simple') {
        preview = { value: slotSimpleValue };
      } else {
        for (const { key, value } of slotFields) {
          const k = key.trim();
          if (!k) continue;
          preview[k] = coerceFieldValue(value);
        }
      }
      setSlotDataRaw(JSON.stringify(preview, null, 2));
    } else if (next === 'structured' && slotMode === 'json') {
      try {
        const parsed = JSON.parse(slotDataRaw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const entries = Object.entries(parsed);
          setSlotFields(entries.length ? entries.map(([k, v]) => ({
            key: k,
            value: typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''),
          })) : [{ key: '', value: '' }]);
        }
      } catch { /* keep existing fields */ }
    } else if (next === 'simple' && slotMode === 'structured') {
      // Take the first field's value as the simple value, if any
      const first = slotFields.find(f => f.key.trim());
      setSlotSimpleValue(first ? first.value : '');
    }
    setSlotDataError('');
    setSlotMode(next);
  };

  const handleDeleteSlot = async (slot) => {
    if (!window.confirm(`Delete slot "${slot}"?`)) return;
    await deleteMemoryStructuredSlot(selected.id, slot);
    await selectPool(selected.id);
  };

  const allNotes = selected?.notes || [];
  const isJournalNote = (n) => n.title?.startsWith('journal:');
  const notes = allNotes.filter(n => !isJournalNote(n));
  const journals = allNotes.filter(isJournalNote);
  const structuredSlots = Object.entries(selected?.structured_data || {});
  const [episodeStats, setEpisodeStats] = useState(null);
  const [graphStats, setGraphStats] = useState(null);

  useEffect(() => {
    if (!selected?.id) { setEpisodeStats(null); setGraphStats(null); return; }
    let cancelled = false;
    getMemoryEpisodesStats(selected.id)
      .then(r => { if (!cancelled) setEpisodeStats(r.data); })
      .catch(() => { if (!cancelled) setEpisodeStats(null); });
    getMemoryGraphStats(selected.id)
      .then(r => { if (!cancelled) setGraphStats(r.data); })
      .catch(() => { if (!cancelled) setGraphStats(null); });
    return () => { cancelled = true; };
  }, [selected?.id, contentTab]);

  const CONTENT_TABS = [
    { id: 'structured', label: 'Structured',  icon: Layers,     count: structuredSlots.length },
    { id: 'notes',      label: 'Notes',       icon: StickyNote, count: notes.length },
    { id: 'journals',   label: 'Journals',    icon: BookOpen,   count: journals.length },
    { id: 'episodes',   label: 'Episodes',    icon: Activity,   count: episodeStats?.total ?? 0 },
    { id: 'graph',      label: 'Graph',       icon: Share2,     count: graphStats?.node_count ?? 0 },
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
                {(() => {
                  const all = m.notes || [];
                  const journalCount = all.filter(n => n.title?.startsWith('journal:')).length;
                  const noteCount = all.length - journalCount;
                  return (
                    <p className="text-xs text-gray-400 mt-0.5">
                      {noteCount} note{noteCount !== 1 ? 's' : ''}
                      {' · '}{journalCount} journal{journalCount !== 1 ? 's' : ''}
                      {' · '}{Object.keys(m.structured_data || {}).length} slots
                    </p>
                  );
                })()}
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
                {contentTab === 'notes' && (
                  <button onClick={() => { setShowAddNote(true); setEditingNote(null); setViewingNote(null); }} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> Add Note
                  </button>
                )}
                {contentTab === 'structured' && (
                  <button onClick={openAddSlot} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> Add Slot
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
                    onClick={() => {
                      setContentTab(t.id);
                      setViewingNote(null); setShowAddNote(false); setEditingNote(null);
                    }}
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
                  ) : viewingNote && !isJournalNote(viewingNote) ? (
                    <div className="p-5">
                      <div className="flex justify-between items-center mb-4">
                        <div>
                          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                            <StickyNote className="w-4 h-4 text-indigo-500" /> {viewingNote.title}
                          </h3>
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

            {/* Journals tab — read-only entries written by agents */}
            {contentTab === 'journals' && (
              <div className="flex flex-1 overflow-hidden">
                <div className="w-64 shrink-0 border-r border-gray-100 overflow-y-auto">
                  {journals.length === 0 ? (
                    <p className="p-4 text-xs text-gray-400 italic text-center">No journal entries yet.</p>
                  ) : journals.map((n) => (
                    <div
                      key={n.id}
                      onClick={() => { setViewingNote(n); setShowAddNote(false); setEditingNote(null); }}
                      className={`p-3 cursor-pointer border-b border-gray-50 hover:bg-gray-50 ${viewingNote?.id === n.id ? 'bg-amber-50' : ''}`}
                    >
                      <div className="min-w-0">
                        <p className={`text-sm truncate font-medium ${viewingNote?.id === n.id ? 'text-amber-700' : 'text-gray-700'}`}>{n.title.replace(/^journal:\s*/, '')}</p>
                        <p className="text-xs text-gray-400 mt-0.5 truncate">{n.content?.slice(0, 60)}{n.content?.length > 60 ? '…' : ''}</p>
                        <p className="text-xs text-gray-300 mt-0.5">{fmt(n.created_at)}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="flex-1 overflow-y-auto bg-gray-50">
                  {viewingNote && isJournalNote(viewingNote) ? (
                    <div className="p-5">
                      <div className="flex justify-between items-center mb-4">
                        <div>
                          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                            <BookOpen className="w-4 h-4 text-amber-500" /> {viewingNote.title.replace(/^journal:\s*/, '')}
                            <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">read-only</span>
                          </h3>
                          <p className="text-xs text-gray-400 mt-0.5">{fmt(viewingNote.created_at)}</p>
                        </div>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">{viewingNote.content}</div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <BookOpen className="w-10 h-10 mb-3 opacity-20" />
                      <p className="text-sm">Select a journal entry to read it.</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Structured tab — covers both simple kv slots and complex JSON slots */}
            {contentTab === 'structured' && (
              <div className="flex-1 overflow-y-auto p-5 space-y-3">
                {(showAddSlot || editingSlot) && (
                  <form onSubmit={handleSaveSlot} className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 space-y-3">
                    <div className="flex justify-between items-center">
                      <h4 className="text-sm font-semibold text-indigo-800">
                        {editingSlot ? `Edit "${editingSlot.slot}"` : 'New Slot'}
                      </h4>
                      <button type="button" onClick={() => { setShowAddSlot(false); setEditingSlot(null); resetSlotForm(); }} className="text-indigo-400 hover:text-indigo-600"><X className="w-4 h-4" /></button>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Name</label>
                      <input required value={slotName} onChange={e => setSlotName(e.target.value)} disabled={!!editingSlot} placeholder="api_version"
                        className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono disabled:bg-gray-100 disabled:text-gray-400" />
                    </div>

                    {/* Mode selector */}
                    <div className="flex gap-1 bg-white border border-gray-200 rounded-lg p-0.5 w-fit">
                      {[
                        { id: 'structured', label: 'Structured' },
                        { id: 'simple',     label: 'Simple value' },
                        { id: 'json',       label: 'Raw JSON' },
                      ].map(({ id: mid, label }) => (
                        <button
                          key={mid}
                          type="button"
                          onClick={() => switchSlotMode(mid)}
                          className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                            slotMode === mid ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-100'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>

                    {slotMode === 'simple' && (
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Value</label>
                        <input
                          value={slotSimpleValue}
                          onChange={e => setSlotSimpleValue(e.target.value)}
                          placeholder="A single value"
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
                        />
                        <p className="text-[11px] text-gray-500 mt-1">Stored as <code className="bg-white px-1 rounded">{`{ "value": ... }`}</code>.</p>
                      </div>
                    )}

                    {slotMode === 'structured' && (
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Fields</label>
                        <div className="space-y-2">
                          {slotFields.map((f, i) => (
                            <div key={i} className="flex gap-2 items-start">
                              <input
                                value={f.key}
                                onChange={e => updateSlotField(i, { key: e.target.value })}
                                placeholder="key"
                                className="w-1/3 border border-gray-300 rounded-lg px-3 py-1.5 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                              />
                              <input
                                value={f.value}
                                onChange={e => updateSlotField(i, { value: e.target.value })}
                                placeholder="value"
                                className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                              />
                              <button
                                type="button"
                                onClick={() => removeSlotField(i)}
                                disabled={slotFields.length === 1 && !f.key && !f.value}
                                className="text-gray-300 hover:text-red-500 p-1.5 disabled:opacity-30"
                                title="Remove field"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                        <button
                          type="button"
                          onClick={addSlotField}
                          className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                        >
                          <Plus className="w-3.5 h-3.5" /> Add field
                        </button>
                        <p className="text-[11px] text-gray-500 mt-2">Numbers, booleans, <code>null</code>, and JSON arrays/objects are auto-detected. Everything else is stored as a string.</p>
                      </div>
                    )}

                    {slotMode === 'json' && (
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Data (JSON object)</label>
                        <textarea
                          rows={6}
                          value={slotDataRaw}
                          onChange={e => { setSlotDataRaw(e.target.value); setSlotDataError(''); }}
                          className={`w-full border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none ${slotDataError ? 'border-red-400 bg-red-50' : 'border-gray-300'}`}
                        />
                      </div>
                    )}

                    {slotDataError && <p className="text-xs text-red-500">{slotDataError}</p>}

                    <button type="submit" className="bg-indigo-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center gap-2">
                      <Save className="w-3.5 h-3.5" /> {editingSlot ? 'Update' : 'Save'}
                    </button>
                  </form>
                )}
                {structuredSlots.length === 0 && !showAddSlot ? (
                  <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-center">
                    <Layers className="w-10 h-10 mb-3 opacity-20" />
                    <p className="text-sm">No data stored yet.<br />Add a value for simple facts or a slot for structured records.</p>
                  </div>
                ) : structuredSlots.map(([slot, data]) => {
                  const simple = isSimpleSlot(data);
                  if (simple) return (
                    <div key={slot} className={`bg-white border rounded-xl flex items-center justify-between px-4 py-2.5 ${editingSlot?.slot === slot ? 'border-indigo-300' : 'border-gray-200'}`}>
                      <div className="flex items-center gap-4 min-w-0">
                        <span className="font-mono text-sm font-semibold text-indigo-700 shrink-0">{slot}</span>
                        <span className="font-mono text-sm text-gray-800 truncate">{data?.value ?? '—'}</span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0 ml-2">
                        <button onClick={() => startEditSlot(slot, data)} className="text-gray-300 hover:text-indigo-500 p-1"><Edit3 className="w-3.5 h-3.5" /></button>
                        <button onClick={() => handleDeleteSlot(slot)} className="text-gray-300 hover:text-red-500 p-1"><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </div>
                  );
                  return (
                    <div key={slot} className={`bg-white border rounded-xl overflow-hidden ${editingSlot?.slot === slot ? 'border-indigo-300' : 'border-gray-200'}`}>
                      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-50 border-b border-gray-100">
                        <span className="font-mono text-sm font-semibold text-indigo-700">{slot}</span>
                        <div className="flex items-center gap-1">
                          <button onClick={() => startEditSlot(slot, data)} className="text-gray-300 hover:text-indigo-500 p-1"><Edit3 className="w-3.5 h-3.5" /></button>
                          <button onClick={() => handleDeleteSlot(slot)} className="text-gray-300 hover:text-red-500 p-1"><Trash2 className="w-3.5 h-3.5" /></button>
                        </div>
                      </div>
                      <div className="px-4 py-3">
                        <table className="w-full text-xs">
                          <tbody className="divide-y divide-gray-50">
                            {Object.entries(data || {}).map(([k, v]) => (
                              <tr key={k}>
                                <td className="py-1.5 pr-4 font-mono text-gray-500 w-1/3 align-top">{k}</td>
                                <td className="py-1.5 font-mono text-gray-800 break-all">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Episodes tab */}
            {contentTab === 'episodes' && (
              <EpisodesPanel poolId={selected.id} stats={episodeStats} onChange={() => {
                getMemoryEpisodesStats(selected.id).then(r => setEpisodeStats(r.data)).catch(() => {});
              }} />
            )}

            {/* Graph tab */}
            {contentTab === 'graph' && (
              <GraphPanel poolId={selected.id} stats={graphStats} onChange={() => {
                getMemoryGraphStats(selected.id).then(r => setGraphStats(r.data)).catch(() => {});
              }} />
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
                <input required value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Project Notes, API Config"
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
// Episodes panel — discrete event log scoped to a pool
// ---------------------------------------------------------------------------
function EpisodesPanel({ poolId, stats, onChange }) {
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    if (!poolId) return;
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (kindFilter) params.kind = kindFilter;
      if (outcomeFilter) params.outcome = outcomeFilter;
      if (query.trim()) params.query = query.trim();
      const r = await listMemoryEpisodes(poolId, params);
      setEpisodes(r.data?.episodes || []);
    } catch {
      setEpisodes([]);
    } finally {
      setLoading(false);
    }
  }, [poolId, kindFilter, outcomeFilter, query]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (epId) => {
    if (!window.confirm('Delete this episode?')) return;
    try {
      await deleteMemoryEpisode(poolId, epId);
      await load();
      onChange?.();
    } catch { /* ignore */ }
  };

  const cap = stats?.cap;
  const total = stats?.total ?? 0;

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-gray-500">
          {total} episode{total === 1 ? '' : 's'} stored{cap ? ` · cap ${cap}` : ''}
          {stats?.by_kind && Object.keys(stats.by_kind).length > 0 && (
            <span className="ml-2">
              ({Object.entries(stats.by_kind).map(([k, v]) => `${k}: ${v}`).join(', ')})
            </span>
          )}
        </div>
        <button onClick={load} className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1">
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select value={kindFilter} onChange={e => setKindFilter(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
          <option value="">All kinds</option>
          <option value="interaction">Interaction</option>
          <option value="task">Task</option>
          <option value="decision">Decision</option>
          <option value="error">Error</option>
          <option value="observation">Observation</option>
        </select>
        <select value={outcomeFilter} onChange={e => setOutcomeFilter(e.target.value)}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
          <option value="">All outcomes</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
          <option value="partial">Partial</option>
          <option value="n/a">N/A</option>
        </select>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="keyword search…"
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white flex-1 min-w-[140px]"
        />
      </div>

      {episodes.length === 0 ? (
        <p className="p-6 text-center text-gray-400 text-sm italic">
          {loading ? 'Loading…' : 'No episodes match the current filter.'}
        </p>
      ) : (
        <div className="space-y-2">
          {episodes.map((e) => {
            const kindCls = EPISODE_KIND_COLOR[e.kind] || 'bg-gray-100 text-gray-700';
            const outcomeCls = e.outcome ? (EPISODE_OUTCOME_COLOR[e.outcome] || 'bg-gray-100 text-gray-500') : null;
            return (
              <div key={e.id} className="border border-gray-200 rounded-lg p-3 bg-white">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded ${kindCls}`}>{e.kind}</span>
                    {outcomeCls && (
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded ${outcomeCls}`}>{e.outcome}</span>
                    )}
                    {e.actor && <span className="text-xs text-gray-500">actor: <span className="font-mono">{e.actor}</span></span>}
                    {e.subject && <span className="text-xs text-gray-500">subject: <span className="font-mono">{e.subject}</span></span>}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] text-gray-400">{fmt(e.occurred_at)}</span>
                    <button onClick={() => handleDelete(e.id)} className="text-gray-300 hover:text-red-500 p-1">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <p className="text-sm text-gray-800 mt-2 whitespace-pre-wrap break-words">{e.summary}</p>
                {e.tags && e.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {e.tags.map(t => (
                      <span key={t} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{t}</span>
                    ))}
                  </div>
                )}
                {e.details && Object.keys(e.details).length > 0 && (
                  <details className="mt-2">
                    <summary className="text-[11px] text-gray-400 cursor-pointer hover:text-gray-600">details</summary>
                    <pre className="text-[11px] bg-gray-50 rounded p-2 mt-1 overflow-x-auto">{JSON.stringify(e.details, null, 2)}</pre>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Graph panel — knowledge graph scoped to a pool
// ---------------------------------------------------------------------------

const GRAPH_TYPE_PALETTE = [
  ['#6366f1', '#eef2ff'], ['#10b981', '#ecfdf5'], ['#f59e0b', '#fffbeb'],
  ['#ef4444', '#fef2f2'], ['#3b82f6', '#eff6ff'], ['#a855f7', '#faf5ff'],
  ['#14b8a6', '#f0fdfa'], ['#ec4899', '#fdf2f8'],
];
function colorForType(type, allTypes) {
  const idx = allTypes.indexOf(type);
  return GRAPH_TYPE_PALETTE[(idx >= 0 ? idx : 0) % GRAPH_TYPE_PALETTE.length];
}

function GraphPanel({ poolId, stats, onChange }) {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState('list'); // 'list' | 'visual'
  const [showAdd, setShowAdd] = useState(false);
  const [showExtract, setShowExtract] = useState(false);
  const [extractText, setExtractText] = useState('');
  const [extractRunning, setExtractRunning] = useState(false);
  const [extractResult, setExtractResult] = useState(null);

  // Add-edge form state
  const [srcType, setSrcType] = useState('');
  const [srcName, setSrcName] = useState('');
  const [tgtType, setTgtType] = useState('');
  const [tgtName, setTgtName] = useState('');
  const [relation, setRelation] = useState('');

  const load = useCallback(async () => {
    if (!poolId) return;
    setLoading(true);
    try {
      const r = await getMemoryGraph(poolId);
      setNodes(r.data?.nodes || []);
      setEdges(r.data?.edges || []);
    } catch {
      setNodes([]); setEdges([]);
    } finally {
      setLoading(false);
    }
  }, [poolId]);

  useEffect(() => { load(); }, [load]);

  const handleAddEdge = async (e) => {
    e.preventDefault();
    try {
      await linkMemoryGraph(poolId, {
        source: { type: srcType.trim(), name: srcName.trim() },
        target: { type: tgtType.trim(), name: tgtName.trim() },
        relation: relation.trim(),
      });
      setSrcType(''); setSrcName(''); setTgtType(''); setTgtName(''); setRelation('');
      setShowAdd(false);
      await load();
      onChange?.();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to add edge');
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm('Delete this node and any edges attached to it?')) return;
    try {
      await deleteMemoryGraphNode(poolId, nodeId);
      await load();
      onChange?.();
    } catch { /* ignore */ }
  };

  const handleDeleteEdge = async (edgeId) => {
    if (!window.confirm('Delete this edge?')) return;
    try {
      await deleteMemoryGraphEdge(poolId, edgeId);
      await load();
      onChange?.();
    } catch { /* ignore */ }
  };

  const handleExtract = async (e) => {
    e.preventDefault();
    if (!extractText.trim()) return;
    setExtractRunning(true);
    setExtractResult(null);
    try {
      const r = await extractMemoryGraph(poolId, extractText.trim());
      setExtractResult(r.data);
      await load();
      onChange?.();
    } catch (err) {
      setExtractResult({ ok: false, errors: [err?.response?.data?.detail || 'Extraction failed'] });
    } finally {
      setExtractRunning(false);
    }
  };

  const allTypes = Array.from(new Set(nodes.map(n => n.type)));

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-gray-500">
          {nodes.length} node{nodes.length === 1 ? '' : 's'}, {edges.length} edge{edges.length === 1 ? '' : 's'}
          {stats?.node_cap && ` · cap ${stats.node_cap}/${stats.edge_cap}`}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex border border-gray-200 rounded overflow-hidden">
            <button onClick={() => setView('list')}
              className={`text-xs px-2 py-1 ${view === 'list' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              List
            </button>
            <button onClick={() => setView('visual')}
              className={`text-xs px-2 py-1 ${view === 'visual' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              Visualize
            </button>
          </div>
          <button onClick={() => setShowExtract(s => !s)}
            className="text-xs flex items-center gap-1 border border-purple-200 text-purple-700 px-2 py-1 rounded hover:bg-purple-50">
            <Sparkles className="w-3 h-3" /> Extract
          </button>
          <button onClick={() => setShowAdd(s => !s)}
            className="text-xs flex items-center gap-1 border border-indigo-200 text-indigo-600 px-2 py-1 rounded hover:bg-indigo-50">
            <Plus className="w-3 h-3" /> Edge
          </button>
          <button onClick={load} className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>
      </div>

      {/* Add-edge form */}
      {showAdd && (
        <form onSubmit={handleAddEdge} className="border border-indigo-100 rounded-lg p-3 bg-indigo-50/30 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            <input value={srcType} onChange={e => setSrcType(e.target.value)} placeholder="source type" required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={srcName} onChange={e => setSrcName(e.target.value)} placeholder="source name" required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={relation} onChange={e => setRelation(e.target.value)} placeholder="relation" required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={tgtType} onChange={e => setTgtType(e.target.value)} placeholder="target type" required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={tgtName} onChange={e => setTgtName(e.target.value)} placeholder="target name" required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700">Add</button>
            <button type="button" onClick={() => setShowAdd(false)} className="text-xs border border-gray-200 px-3 py-1 rounded">Cancel</button>
          </div>
        </form>
      )}

      {/* Auto-extract form */}
      {showExtract && (
        <form onSubmit={handleExtract} className="border border-purple-100 rounded-lg p-3 bg-purple-50/30 space-y-2">
          <p className="text-xs text-purple-800">
            Paste prose and the LLM will extract entities + relations and add them to the graph.
          </p>
          <textarea value={extractText} onChange={e => setExtractText(e.target.value)} rows={4}
            placeholder="e.g. Alice owns the auth-rewrite project. The mobile-app depends on auth-rewrite. Bob blocks ship-v2…"
            className="w-full text-xs border border-gray-200 rounded px-2 py-1.5 font-mono" />
          <div className="flex items-center gap-2">
            <button type="submit" disabled={extractRunning || !extractText.trim()}
              className="text-xs bg-purple-600 text-white px-3 py-1 rounded hover:bg-purple-700 disabled:opacity-50 flex items-center gap-1">
              <Sparkles className="w-3 h-3" /> {extractRunning ? 'Extracting…' : 'Run extraction'}
            </button>
            <button type="button" onClick={() => { setShowExtract(false); setExtractResult(null); }}
              className="text-xs border border-gray-200 px-3 py-1 rounded">Close</button>
          </div>
          {extractResult && (
            <div className="text-xs text-gray-700 bg-white border border-gray-200 rounded p-2">
              {extractResult.ok === false
                ? <span className="text-red-600">Failed: {(extractResult.errors || []).join('; ') || 'unknown error'}</span>
                : <span>Found {extractResult.triples_found || 0}, persisted {extractResult.triples_persisted || 0} triples.</span>}
            </div>
          )}
        </form>
      )}

      {nodes.length === 0 && edges.length === 0 ? (
        <p className="p-6 text-center text-gray-400 text-sm italic">
          {loading ? 'Loading…' : 'Graph is empty. Add an edge or run extraction to seed it.'}
        </p>
      ) : view === 'visual' ? (
        <GraphVisual nodes={nodes} edges={edges} allTypes={allTypes} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-100 text-xs font-semibold text-gray-700">
              Nodes ({nodes.length})
            </div>
            <div className="divide-y divide-gray-50 max-h-96 overflow-y-auto">
              {nodes.map(n => {
                const [fg, bg] = colorForType(n.type, allTypes);
                return (
                  <div key={n.id} className="flex items-start justify-between gap-2 px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="px-1.5 py-0.5 rounded font-medium" style={{ color: fg, background: bg }}>{n.type}</span>
                        <span className="font-mono text-gray-800 truncate">{n.name}</span>
                      </div>
                      {n.properties && Object.keys(n.properties).length > 0 && (
                        <details className="mt-1">
                          <summary className="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">properties</summary>
                          <pre className="text-[10px] bg-gray-50 rounded p-1 mt-1 overflow-x-auto">{JSON.stringify(n.properties, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                    <button onClick={() => handleDeleteNode(n.id)} className="text-gray-300 hover:text-red-500 p-0.5 shrink-0">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-100 text-xs font-semibold text-gray-700">
              Edges ({edges.length})
            </div>
            <div className="divide-y divide-gray-50 max-h-96 overflow-y-auto">
              {edges.map(e => {
                const src = nodes.find(n => n.id === e.source_id);
                const tgt = nodes.find(n => n.id === e.target_id);
                if (!src || !tgt) return null;
                return (
                  <div key={e.id} className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
                    <span className="font-mono text-gray-700 truncate flex-1">
                      <span className="text-gray-500">{src.type}:</span>{src.name}
                      <span className="mx-1 text-indigo-500">─[{e.relation}]→</span>
                      <span className="text-gray-500">{tgt.type}:</span>{tgt.name}
                    </span>
                    <button onClick={() => handleDeleteEdge(e.id)} className="text-gray-300 hover:text-red-500 p-0.5 shrink-0">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dependency-free force-directed SVG layout. Tiny iterative spring sim that
// converges in ~120 ticks for graphs under ~150 nodes — good enough for this UI.
// ---------------------------------------------------------------------------
function GraphVisual({ nodes, edges, allTypes }) {
  const W = 720, H = 480;
  const positions = useMemo(() => {
    if (nodes.length === 0) return new Map();
    const pos = new Map();
    nodes.forEach((n, i) => {
      const angle = (i / nodes.length) * Math.PI * 2;
      pos.set(n.id, {
        x: W / 2 + Math.cos(angle) * (Math.min(W, H) / 3),
        y: H / 2 + Math.sin(angle) * (Math.min(W, H) / 3),
        vx: 0, vy: 0,
      });
    });
    const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.6;
    const iterations = 140;
    // Deterministic jitter so re-renders produce the same layout.
    const jitter = (i, j) => (Math.sin(i * 12.9898 + j * 78.233) * 43758.5453) % 1;
    for (let it = 0; it < iterations; it++) {
      const t = 1 - it / iterations;
      // Repulsion (every pair).
      for (let i = 0; i < nodes.length; i++) {
        const a = pos.get(nodes[i].id);
        for (let j = i + 1; j < nodes.length; j++) {
          const b = pos.get(nodes[j].id);
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = jitter(i, j) * 0.1; dy = jitter(j, i) * 0.1; d2 = 0.02; }
          const force = (k * k) / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * force, fy = (dy / d) * force;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // Attraction along edges.
      for (const e of edges) {
        const a = pos.get(e.source_id), b = pos.get(e.target_id);
        if (!a || !b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (d * d) / k;
        const fx = (dx / d) * force, fy = (dy / d) * force;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
      // Apply with cooling damping.
      for (const n of nodes) {
        const p = pos.get(n.id);
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 0.001;
        const cap = Math.min(speed, 30 * t);
        p.x += (p.vx / speed) * cap;
        p.y += (p.vy / speed) * cap;
        p.vx *= 0.85; p.vy *= 0.85;
        // Keep inside the viewport with margin.
        p.x = Math.max(40, Math.min(W - 40, p.x));
        p.y = Math.max(30, Math.min(H - 30, p.y));
      }
    }
    return pos;
  }, [nodes, edges]);

  return (
    <div className="border border-gray-200 rounded-lg bg-white overflow-hidden">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minHeight: 360 }}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>
        {edges.map(e => {
          const a = positions.get(e.source_id), b = positions.get(e.target_id);
          if (!a || !b) return null;
          const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
          return (
            <g key={e.id}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#cbd5e1" strokeWidth="1" markerEnd="url(#arrow)" />
              <text x={mx} y={my - 3} fontSize="9" fill="#64748b" textAnchor="middle" pointerEvents="none">{e.relation}</text>
            </g>
          );
        })}
        {nodes.map(n => {
          const p = positions.get(n.id);
          if (!p) return null;
          const [fg, bg] = colorForType(n.type, allTypes);
          return (
            <g key={n.id}>
              <circle cx={p.x} cy={p.y} r="14" fill={bg} stroke={fg} strokeWidth="1.5" />
              <text x={p.x} y={p.y + 4} fontSize="9" fill={fg} textAnchor="middle" fontWeight="600" pointerEvents="none">
                {n.name.length > 14 ? n.name.slice(0, 13) + '…' : n.name}
              </text>
              <title>{n.type}: {n.name}</title>
            </g>
          );
        })}
      </svg>
      <div className="px-3 py-2 border-t border-gray-100 bg-gray-50 flex flex-wrap gap-2">
        {allTypes.map(t => {
          const [fg, bg] = colorForType(t, allTypes);
          return (
            <span key={t} className="text-[10px] font-medium px-1.5 py-0.5 rounded" style={{ color: fg, background: bg }}>
              {t}
            </span>
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Tab: Agent Connections
// ---------------------------------------------------------------------------
function AgentsTab({ memories, workspaceFilter }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(null);
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
                  <p className="text-xs text-gray-400">{(m.notes || []).length} notes · {Object.keys(m.structured_data || {}).length} slots</p>
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
                        <p className="font-medium text-indigo-700">{pool.name}</p>
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

function RagPipelineTab({ memories, workspaceFilter }) {
  const [poolId, setPoolId] = useState(memories[0]?.id || '');
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [indexing, setIndexing] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [ragCfg, setRagCfg] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    getRagConfig().then(r => setRagCfg(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!poolId && memories.length > 0) setPoolId(memories[0].id);
  }, [memories, poolId]);

  const loadFiles = useCallback(async (id, workspace) => {
    if (!id || !workspace) return;
    setLoading(true);
    try {
      const resp = await listMemoryFiles(id, workspace);
      setFiles(resp.data.files || []);
    } catch { setFiles([]); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (poolId && workspaceFilter) loadFiles(poolId, workspaceFilter);
    else setFiles([]);
  }, [poolId, workspaceFilter, loadFiles]);

  const handleUploadFiles = async (fileList) => {
    if (!poolId || !workspaceFilter) return;
    setUploading(true);
    try {
      for (const file of fileList) {
        await uploadMemoryFile(poolId, workspaceFilter, file);
      }
      await loadFiles(poolId, workspaceFilter);
    } catch {} finally { setUploading(false); }
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    setDragging(false);
    await handleUploadFiles([...e.dataTransfer.files]);
  };

  const handleIndex = async (filename) => {
    setIndexing(filename);
    try {
      await indexMemoryFile(poolId, filename, workspaceFilter);
      await loadFiles(poolId, workspaceFilter);
    } catch {} finally { setIndexing(null); }
  };

  const handleDeindex = async (filename) => {
    setIndexing(filename);
    try {
      await deindexMemoryFile(poolId, filename);
      await loadFiles(poolId, workspaceFilter);
    } catch {} finally { setIndexing(null); }
  };

  const handleDelete = async (filename) => {
    if (!window.confirm(`Delete "${filename}"?`)) return;
    try {
      await deleteMemoryFile(poolId, filename, workspaceFilter);
      await loadFiles(poolId, workspaceFilter);
    } catch {}
  };

  const handleIndexAll = async () => {
    const pending = files.filter(f => f.status !== 'indexed');
    for (const f of pending) await handleIndex(f.filename);
  };

  const indexedCount = files.filter(f => f.status === 'indexed').length;
  const pendingCount = files.length - indexedCount;
  const totalChunks = files.reduce((s, f) => s + (f.chunks || 0), 0);
  const showVectorCols = ragCfg?.is_configured;

  return (
    <div className="space-y-5">
      {/* Vector DB status */}
      <VectorDbStatusCard ragCfg={ragCfg} />

      {/* Pool selector */}
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
          {pendingCount > 0 && (
            <button onClick={handleIndexAll}
              className="flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 text-sm font-medium">
              <Zap className="w-4 h-4" /> Index All Pending ({pendingCount})
            </button>
          )}
          <button onClick={() => loadFiles(poolId, workspaceFilter)} disabled={loading}
            className="flex items-center gap-1 border border-gray-200 text-gray-500 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Stats bar */}
      {files.length > 0 && (
        <div className={`grid gap-4 ${showVectorCols ? 'grid-cols-4' : 'grid-cols-3'}`}>
          {[
            { label: 'Total Files', value: files.length, color: 'bg-gray-50 text-gray-600' },
            { label: 'Pending', value: pendingCount, color: 'bg-yellow-50 text-yellow-700' },
            { label: 'Indexed', value: indexedCount, color: 'bg-green-50 text-green-700' },
            ...(showVectorCols ? [{ label: 'Total Chunks', value: totalChunks, color: 'bg-indigo-50 text-indigo-700' }] : []),
          ].map(({ label, value, color }) => (
            <div key={label} className={`rounded-xl border border-gray-200 p-4 text-center ${color}`}>
              <p className="text-2xl font-bold">{value}</p>
              <p className="text-xs font-medium mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Upload zone */}
      {workspaceFilter ? (
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
            onChange={e => { handleUploadFiles([...e.target.files]); e.target.value = ''; }} />
          {uploading
            ? <RefreshCw className="w-10 h-10 mx-auto mb-3 animate-spin text-indigo-400" />
            : <Upload className={`w-10 h-10 mx-auto mb-3 ${dragging ? 'text-indigo-500' : 'text-gray-300'}`} />
          }
          <p className="text-gray-600 font-medium">{uploading ? 'Uploading…' : 'Drop files here or click to upload'}</p>
          <p className="text-xs text-gray-400 mt-1">Supports .txt, .md, .json, .yaml, .csv, .xml and other text formats</p>
        </div>
      ) : (
        <div className="border-2 border-dashed rounded-xl p-10 text-center border-gray-200 bg-gray-50">
          <AlertCircle className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="text-gray-400 text-sm">Select a workspace to upload files.</p>
        </div>
      )}

      {/* File list */}
      {poolId && files.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2">
              <Files className="w-4 h-4 text-indigo-500" /> Files in "{memories.find(m => m.id === poolId)?.name || poolId}"
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-5 py-2 text-left">File</th>
                  <th className="px-5 py-2 text-left">Status</th>
                  <th className="px-5 py-2 text-left">Chunks</th>
                  <th className="px-5 py-2 text-left">Indexed at</th>
                  <th className="px-5 py-2 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {files.map((f) => {
                  const isIndexed = f.status === 'indexed';
                  const isBusy = indexing === f.filename;
                  return (
                    <tr key={f.filename} className={`hover:bg-gray-50 ${isBusy ? 'bg-indigo-50/40' : ''}`}>
                      <td className="px-5 py-3">
                        <p className="font-medium text-gray-800">{f.filename}</p>
                      </td>
                      <td className="px-5 py-3">
                        {isBusy
                          ? <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-full font-medium animate-pulse"><RefreshCw className="w-3 h-3 animate-spin" /> Working</span>
                          : <StatusBadge status={isIndexed ? 'indexed' : 'raw'} />
                        }
                      </td>
                      <td className="px-5 py-3 text-gray-500 text-xs">{isIndexed ? (f.chunks || '—') : '—'}</td>
                      <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.indexed_at)}</td>
                      <td className="px-5 py-3 text-right">
                        <div className="flex items-center gap-1 justify-end">
                          {isBusy ? (
                            <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-400" />
                          ) : isIndexed ? (
                            <button onClick={() => handleDeindex(f.filename)}
                              className="flex items-center gap-1 text-xs border border-gray-200 text-gray-500 px-2 py-1 rounded-lg hover:bg-gray-50">
                              De-index
                            </button>
                          ) : (
                            <button onClick={() => handleIndex(f.filename)}
                              className="flex items-center gap-1 text-xs bg-indigo-600 text-white px-2 py-1 rounded-lg hover:bg-indigo-700">
                              <Zap className="w-3 h-3" /> Index
                            </button>
                          )}
                          <button onClick={() => handleDelete(f.filename)} className="text-gray-300 hover:text-red-500 p-0.5 ml-1">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
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
function IndexedFilesTab({ memories }) {
  const [search, setSearch] = useState('');
  const [filterPool, setFilterPool] = useState('');

  // Flatten rag_files from all pools
  const ragFiles = memories.flatMap(m =>
    (m.rag_files || [])
      .filter(f => f.status === 'indexed')
      .map(f => ({ ...f, memory_name: m.name, memory_id: m.id }))
  );

  const pools = [...new Set(ragFiles.map(f => f.memory_name))];
  const filtered = ragFiles.filter(f => {
    const matchSearch = !search || f.filename.toLowerCase().includes(search.toLowerCase()) || f.memory_name.toLowerCase().includes(search.toLowerCase());
    const matchPool = !filterPool || f.memory_name === filterPool;
    return matchSearch && matchPool;
  });

  const totalChunks = filtered.reduce((sum, f) => sum + (f.chunks || 0), 0);

  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Indexed Files', value: ragFiles.length, icon: FileSearch, color: 'text-indigo-600 bg-indigo-50' },
          { label: 'Total Chunks', value: totalChunks, icon: BarChart2, color: 'text-green-600 bg-green-50' },
          { label: 'Showing', value: filtered.length, icon: CheckCircle, color: 'text-teal-600 bg-teal-50' },
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
                <th className="px-5 py-3 text-left">Indexed at</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((f, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                      <span className="font-medium text-gray-900">{f.filename}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className="bg-indigo-50 text-indigo-700 text-xs px-2 py-0.5 rounded-full font-medium">{f.memory_name}</span>
                  </td>
                  <td className="px-5 py-3"><StatusBadge status="indexed" /></td>
                  <td className="px-5 py-3 font-medium text-gray-800">{f.chunks || '—'}</td>
                  <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.indexed_at)}</td>
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
  const { workspaceFilter } = useWorkspace();
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
      {activeTab === 'rag'     && <RagPipelineTab memories={memories} workspaceFilter={workspaceFilter} />}
      {activeTab === 'indexed' && <IndexedFilesTab memories={memories} />}
    </div>
  );
}

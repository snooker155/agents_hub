/**
 * Tab: Memory Pools — the pool list, and the selected pool's core memory
 * blocks, structured slots, notes, journals, episodes and graph.
 */
import { useState, useEffect, useCallback } from 'react';
import {
  Database, Plus, Trash2, X, Save, ChevronRight, Edit3,
  StickyNote, Layers, Activity, BookOpen, Share2, Brain,
} from 'lucide-react';
import {
  getSharedMemory, createSharedMemory, deleteSharedMemory,
  addMemoryNote, updateMemoryNote, deleteMemoryNote,
  upsertMemoryBlock, deleteMemoryBlock,
  upsertMemoryStructuredSlot, deleteMemoryStructuredSlot,
  getMemoryEpisodesStats, getMemoryGraphStats,
} from '../../api';
import { SlotValue } from '../SlotValue';
import { coerceSlotValue, isSlotContainer } from '../slotUtils';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { DEFAULT_BLOCK_NAMES, fmt } from './helpers';
import { EpisodesPanel } from './EpisodesPanel';
import { GraphPanel } from './GraphPanel';

function PoolsTab({ memories, onRefresh, workspaceFilter, onPoolSelected }) {
  const { t } = useI18n();
  const toast = useToast();
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

  // Core memory block state. Drafts are keyed by block name and live only until
  // the block is saved, so an unsaved edit survives re-rendering the card but
  // never masks what the pool actually holds.
  const [blockDrafts, setBlockDrafts] = useState({});
  const [savingBlock, setSavingBlock] = useState('');
  const [showAddBlock, setShowAddBlock] = useState(false);
  const [newBlockName, setNewBlockName] = useState('');
  const [newBlockLimit, setNewBlockLimit] = useState('2000');
  const [newBlockDesc, setNewBlockDesc] = useState('');

  const selectPool = useCallback(async (id) => {
    try {
      const resp = await getSharedMemory(id);
      setSelected(resp.data);
      // The chat beside this tab binds to whatever is open here.
      onPoolSelected?.(id);
      setViewingNote(null); setShowAddNote(false); setEditingNote(null);
      setShowAddSlot(false); setEditingSlot(null);
      setBlockDrafts({}); setShowAddBlock(false);
    } catch (e) {
      toast.error(t('memoryManager.errors.openMemory'), errorDetail(e));
    }
  }, [t, toast, onPoolSelected]);

  const handleCreate = async (e) => {
    e.preventDefault();
    await createSharedMemory({ name: newName, description: newDesc, workspace: workspaceFilter || null });
    setNewName(''); setNewDesc(''); setShowCreate(false);
    onRefresh();
  };

  const handleDelete = async (id) => {
    if (!window.confirm(t('memoryManager.confirmDeletePool'))) return;
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
    if (!window.confirm(t('memoryManager.confirmDeleteNote'))) return;
    await deleteMemoryNote(selected.id, id);
    if (viewingNote?.id === id) setViewingNote(null);
    await selectPool(selected.id);
  };

  const startEditNote = (n) => {
    setEditingNote(n); setNoteTitle(n.title); setNoteContent(n.content);
    setViewingNote(null); setShowAddNote(false);
  };

  // ---- Core memory blocks ----
  // A block is rendered into every system prompt, so the limit is a real budget
  // rather than a formality: the counter turns red before the save is refused.
  const blocks = selected?.blocks || [];
  const blockValue = (b) => (blockDrafts[b.name] !== undefined ? blockDrafts[b.name] : (b.value || ''));
  const isDefaultBlock = (name) => DEFAULT_BLOCK_NAMES.includes(name);

  const setBlockDraft = (name, value) => setBlockDrafts(prev => ({ ...prev, [name]: value }));

  const handleSaveBlock = async (block) => {
    setSavingBlock(block.name);
    try {
      const resp = await upsertMemoryBlock(selected.id, block.name, { value: blockValue(block) });
      setSelected(resp.data);
      setBlockDrafts(prev => {
        const next = { ...prev };
        delete next[block.name];
        return next;
      });
      toast.success(t('memoryManager.blocks.saved', { name: block.name }));
    } catch (e) {
      toast.error(t('memoryManager.blocks.errors.save'), errorDetail(e));
    } finally {
      setSavingBlock('');
    }
  };

  const handleDeleteBlock = async (block) => {
    if (!window.confirm(t('memoryManager.blocks.confirmDelete', { name: block.name }))) return;
    try {
      const resp = await deleteMemoryBlock(selected.id, block.name);
      setSelected(resp.data);
    } catch (e) {
      toast.error(t('memoryManager.blocks.errors.delete'), errorDetail(e));
    }
  };

  const handleAddBlock = async (e) => {
    e.preventDefault();
    const name = newBlockName.trim();
    if (!name) return;
    try {
      const limit = parseInt(newBlockLimit, 10);
      const resp = await upsertMemoryBlock(selected.id, name, {
        value: '',
        limit_chars: Number.isFinite(limit) && limit > 0 ? limit : 2000,
        description: newBlockDesc.trim(),
      });
      setSelected(resp.data);
      setShowAddBlock(false);
      setNewBlockName(''); setNewBlockLimit('2000'); setNewBlockDesc('');
    } catch (e) {
      toast.error(t('memoryManager.blocks.errors.create'), errorDetail(e));
    }
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
      catch { setSlotDataError(t('memoryManager.invalidJson')); return; }
      if (typeof data !== 'object' || Array.isArray(data) || data === null) {
        setSlotDataError(t('memoryManager.mustBeJsonObject')); return;
      }
    } else {
      data = {};
      for (const { key, value } of slotFields) {
        const k = key.trim();
        if (!k) continue;
        if (k in data) { setSlotDataError(t('memoryManager.duplicateKey', { key: k })); return; }
        data[k] = coerceFieldValue(value);
      }
      if (Object.keys(data).length === 0) {
        setSlotDataError(t('memoryManager.addAtLeastOneField')); return;
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
  // Each payload carries the pool it was fetched for, so the counts below can be
  // derived: selecting another pool shows nothing rather than the previous
  // pool's numbers, and no effect has to reset them.
  const [episodeStatsFor, setEpisodeStats] = useState(null);
  const [graphStatsFor, setGraphStats] = useState(null);

  useEffect(() => {
    const poolId = selected?.id;
    if (!poolId) return undefined;
    let cancelled = false;
    getMemoryEpisodesStats(poolId)
      .then(r => { if (!cancelled) setEpisodeStats({ poolId, data: r.data }); })
      .catch(() => { if (!cancelled) setEpisodeStats({ poolId, data: null }); });
    getMemoryGraphStats(poolId)
      .then(r => { if (!cancelled) setGraphStats({ poolId, data: r.data }); })
      .catch(() => { if (!cancelled) setGraphStats({ poolId, data: null }); });
    return () => { cancelled = true; };
  }, [selected?.id, contentTab]);

  // The payload has to be there before its pool can match: with no pool
  // selected and nothing fetched yet, `?.poolId` and `selected?.id` are both
  // undefined, and comparing them would claim a match on a null payload.
  const statsFor = (payload) => (payload && payload.poolId === selected?.id ? payload.data : null);
  const episodeStats = statsFor(episodeStatsFor);
  const graphStats = statsFor(graphStatsFor);

  const CONTENT_TABS = [
    { id: 'blocks',     label: t('memoryManager.contentTabs.blocks'),     icon: Brain,      count: blocks.length },
    { id: 'structured', label: t('memoryManager.contentTabs.structured'), icon: Layers,     count: structuredSlots.length },
    { id: 'notes',      label: t('memoryManager.contentTabs.notes'),      icon: StickyNote, count: notes.length },
    { id: 'journals',   label: t('memoryManager.contentTabs.journals'),   icon: BookOpen,   count: journals.length },
    { id: 'episodes',   label: t('memoryManager.contentTabs.episodes'),   icon: Activity,   count: episodeStats?.total ?? 0 },
    { id: 'graph',      label: t('memoryManager.contentTabs.graph'),      icon: Share2,     count: graphStats?.node_count ?? 0 },
  ];

  return (
    /* `items-start` so the two cards keep their own heights: the detail pane
       grows with whatever the pool holds, and stretching the list to match it
       left a column of empty white under the last pool. The list scrolls
       inside its own viewport-bound height instead. */
    /* The pool list is a fixed-width picker: it keeps its width whether or not
       the chat is open, and the detail pane beside it absorbs the difference.
       Fractional columns would have resized both every time. */
    <div className="grid grid-cols-1 lg:grid-cols-[16rem_minmax(0,1fr)] gap-6 items-start">
      {/* Pool List */}
      <div className="self-start lg:max-h-[calc(100vh-12rem)] bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-gray-50">
          <h3 className="font-semibold text-gray-700 flex items-center gap-2">
            <Database className="w-4 h-4 text-indigo-500" /> Memory Pools
            <span className="bg-indigo-100 text-indigo-700 text-xs px-2 py-0.5 rounded-full">{memories.length}</span>
          </h3>
          <button onClick={() => setShowCreate(true)} className="text-indigo-600 hover:text-indigo-800 p-1 rounded hover:bg-indigo-50" title={t('memoryManager.createPool')}>
            <Plus className="w-4 h-4" />
          </button>
        </div>
        <div className="divide-y divide-gray-100 overflow-y-auto flex-1">
          {memories.length === 0 ? (
            <p className="p-6 text-center text-gray-400 text-sm italic">{t('memoryManager.noMemoryPoolsYet')}</p>
          ) : memories.map((m) => (
            <div
              key={m.id}
              onClick={() => selectPool(m.id)}
              className={`p-3 cursor-pointer hover:bg-indigo-50 transition-colors flex items-center justify-between ${selected?.id === m.id ? 'bg-indigo-50 border-l-4 border-indigo-500' : ''}`}
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium text-gray-900 text-sm truncate">{m.name}</p>
                <p className="text-xs text-gray-500 truncate">{m.description || t('memoryManager.noDescription')}</p>
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
                <button onClick={(e) => { e.stopPropagation(); handleDelete(m.id); }} className="text-gray-300 hover:text-red-500 p-1" title={t('memoryManager.deletePool')}>
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
                <ChevronRight className={`w-4 h-4 ${selected?.id === m.id ? 'text-indigo-500' : 'text-gray-300'}`} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pool Detail */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col min-h-[500px] min-w-0">
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
                    <Plus className="w-3.5 h-3.5" /> {t('memoryManager.addNote')}
                  </button>
                )}
                {contentTab === 'structured' && (
                  <button onClick={openAddSlot} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> {t('memoryManager.addSlot')}
                  </button>
                )}
                {contentTab === 'blocks' && (
                  <button onClick={() => setShowAddBlock(true)} className="flex items-center gap-1 text-sm border border-indigo-200 text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                    <Plus className="w-3.5 h-3.5" /> {t('memoryManager.blocks.addBlock')}
                  </button>
                )}
              </div>
            </div>

            {/* Content type tabs */}
            <div className="flex border-b border-gray-100 bg-gray-50 px-4">
              {CONTENT_TABS.map(tab => {
                const Icon = tab.icon;
                return (
                  <button
                    key={tab.id}
                    onClick={() => {
                      setContentTab(tab.id);
                      setViewingNote(null); setShowAddNote(false); setEditingNote(null);
                    }}
                    className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                      contentTab === tab.id ? 'border-indigo-500 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" /> {tab.label}
                    <span className={`text-xs px-1.5 py-0.5 rounded-full ${contentTab === tab.id ? 'bg-indigo-100 text-indigo-600' : 'bg-gray-200 text-gray-500'}`}>{tab.count}</span>
                  </button>
                );
              })}
            </div>

            {/* Blocks tab — the always-in-context layer of the prompt */}
            {contentTab === 'blocks' && (
              <div className="flex-1 overflow-y-auto p-5 space-y-3">
                <p className="text-xs text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
                  {t('memoryManager.blocks.intro')}
                </p>

                {showAddBlock && (
                  <form onSubmit={handleAddBlock} className="bg-indigo-50 border border-indigo-100 rounded-xl p-4 space-y-3">
                    <div className="flex justify-between items-center">
                      <h4 className="text-sm font-semibold text-indigo-800">{t('memoryManager.blocks.newBlock')}</h4>
                      <button type="button" onClick={() => setShowAddBlock(false)} className="text-indigo-400 hover:text-indigo-600"><X className="w-4 h-4" /></button>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="sm:col-span-2">
                        <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.blocks.name')}</label>
                        <input required value={newBlockName} onChange={e => setNewBlockName(e.target.value)} placeholder={t('memoryManager.blocks.namePlaceholder')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.blocks.limit')}</label>
                        <input type="number" min="1" value={newBlockLimit} onChange={e => setNewBlockLimit(e.target.value)}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.blocks.description')}</label>
                      <input value={newBlockDesc} onChange={e => setNewBlockDesc(e.target.value)} placeholder={t('memoryManager.blocks.descriptionPlaceholder')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                    </div>
                    <button type="submit" className="bg-indigo-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center gap-2">
                      <Plus className="w-3.5 h-3.5" /> {t('memoryManager.blocks.addBlock')}
                    </button>
                  </form>
                )}

                {blocks.length === 0 ? (
                  <p className="p-6 text-center text-gray-400 text-sm italic">{t('memoryManager.blocks.noBlocksYet')}</p>
                ) : blocks.map((block) => {
                  const value = blockValue(block);
                  const limit = block.limit_chars || 0;
                  const over = limit > 0 && value.length > limit;
                  const dirty = blockDrafts[block.name] !== undefined && blockDrafts[block.name] !== (block.value || '');
                  return (
                    <div key={block.name} className="bg-white border border-gray-200 rounded-xl p-4 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-gray-900 font-mono flex items-center gap-2">
                            {block.name}
                            {block.read_only && (
                              <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">{t('memoryManager.readOnly')}</span>
                            )}
                          </p>
                          {block.description && <p className="text-xs text-gray-500 mt-0.5">{block.description}</p>}
                        </div>
                        {!block.read_only && !isDefaultBlock(block.name) && (
                          <button onClick={() => handleDeleteBlock(block)} className="text-gray-300 hover:text-red-500 p-1 shrink-0" title={t('memoryManager.blocks.deleteBlock')}>
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                      <textarea
                        value={value}
                        disabled={block.read_only}
                        onChange={(e) => setBlockDraft(block.name, e.target.value)}
                        rows={5}
                        placeholder={t('memoryManager.blocks.valuePlaceholder')}
                        className={`w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:outline-none disabled:bg-gray-50 disabled:text-gray-500 ${
                          over ? 'border-red-400 focus:ring-red-400' : 'border-gray-300 focus:ring-indigo-500'
                        }`}
                      />
                      <div className="flex items-center justify-between gap-2">
                        <span className={`text-xs font-medium ${over ? 'text-red-600' : 'text-gray-400'}`}>
                          {t('memoryManager.blocks.counter', { used: value.length, limit })}
                          {over ? ` · ${t('memoryManager.blocks.overBy', { count: value.length - limit })}` : ''}
                        </span>
                        {!block.read_only && (
                          <button
                            onClick={() => handleSaveBlock(block)}
                            disabled={over || savingBlock === block.name || !dirty}
                            className="flex items-center gap-1 text-sm bg-indigo-600 text-white px-3 py-1.5 rounded-lg hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <Save className="w-3.5 h-3.5" /> {savingBlock === block.name ? t('common.saving') : t('common.save')}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Notes tab */}
            {contentTab === 'notes' && (
              <div className="flex flex-1 overflow-hidden">
                <div className="w-64 shrink-0 border-r border-gray-100 overflow-y-auto">
                  {notes.length === 0 ? (
                    <p className="p-4 text-xs text-gray-400 italic text-center">{t('memoryManager.noNotesYet')}</p>
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
                        <h3 className="font-semibold text-gray-900">{editingNote ? t('memoryManager.editNote') : t('memoryManager.newNote')}</h3>
                        <button onClick={() => { setShowAddNote(false); setEditingNote(null); }} className="text-gray-400 hover:text-gray-600"><X className="w-4 h-4" /></button>
                      </div>
                      <form onSubmit={editingNote ? handleSaveNote : handleAddNote} className="space-y-4">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.title')}</label>
                          <input required value={noteTitle} onChange={e => setNoteTitle(e.target.value)} placeholder={t('memoryManager.noteTitle')} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.content')}</label>
                          <textarea required value={noteContent} onChange={e => setNoteContent(e.target.value)} rows={12} placeholder={t('memoryManager.writeYourNoteHere')} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                        </div>
                        <button type="submit" className="w-full bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 flex items-center justify-center gap-2"><Save className="w-4 h-4" /> {editingNote ? t('memoryManager.saveChanges') : t('common.save')}</button>
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
                        <button onClick={() => startEditNote(viewingNote)} className="flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50"><Edit3 className="w-3.5 h-3.5" /> {t('memoryManager.edit')}</button>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">{viewingNote.content}</div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <StickyNote className="w-10 h-10 mb-3 opacity-20" />
                      <p className="text-sm">{t('memoryManager.selectANoteToRead')}<br />{t('memoryManager.orAddANewOne')}</p>
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
                    <p className="p-4 text-xs text-gray-400 italic text-center">{t('memoryManager.noJournalEntriesYet')}</p>
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
                            <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">{t('memoryManager.readOnly')}</span>
                          </h3>
                          <p className="text-xs text-gray-400 mt-0.5">{fmt(viewingNote.created_at)}</p>
                        </div>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-lg p-4 text-sm whitespace-pre-wrap overflow-x-auto shadow-inner min-h-[300px]">{viewingNote.content}</div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center">
                      <BookOpen className="w-10 h-10 mb-3 opacity-20" />
                      <p className="text-sm">{t('memoryManager.selectAJournalEntryTo')}</p>
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
                        {editingSlot ? t('memoryManager.editSlot', { slot: editingSlot.slot }) : t('memoryManager.newSlot')}
                      </h4>
                      <button type="button" onClick={() => { setShowAddSlot(false); setEditingSlot(null); resetSlotForm(); }} className="text-indigo-400 hover:text-indigo-600"><X className="w-4 h-4" /></button>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.name')}</label>
                      <input required value={slotName} onChange={e => setSlotName(e.target.value)} disabled={!!editingSlot} placeholder={t('memoryManager.apiVersion')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono disabled:bg-gray-100 disabled:text-gray-400" />
                    </div>

                    {/* Mode selector */}
                    <div className="flex gap-1 bg-white border border-gray-200 rounded-lg p-0.5 w-fit">
                      {[
                        { id: 'structured', label: t('memoryManager.structured') },
                        { id: 'simple',     label: t('memoryManager.simpleValue') },
                        { id: 'json',       label: t('memoryManager.rawJson') },
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
                        <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.value')}</label>
                        <input
                          value={slotSimpleValue}
                          onChange={e => setSlotSimpleValue(e.target.value)}
                          placeholder={t('memoryManager.aSingleValue')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
                        />
                        <p className="text-[11px] text-gray-500 mt-1">{t('memoryManager.storedAs')} <code className="bg-white px-1 rounded">{`{ "value": ... }`}</code>.</p>
                      </div>
                    )}

                    {slotMode === 'structured' && (
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.fields')}</label>
                        <div className="space-y-2">
                          {slotFields.map((f, i) => (
                            <div key={i} className="flex gap-2 items-start">
                              <input
                                value={f.key}
                                onChange={e => updateSlotField(i, { key: e.target.value })}
                                placeholder={t('memoryManager.key')}
                                className="w-1/3 border border-gray-300 rounded-lg px-3 py-1.5 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                              />
                              <input
                                value={f.value}
                                onChange={e => updateSlotField(i, { value: e.target.value })}
                                placeholder={t('memoryManager.value2')}
                                className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-xs font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                              />
                              <button
                                type="button"
                                onClick={() => removeSlotField(i)}
                                disabled={slotFields.length === 1 && !f.key && !f.value}
                                className="text-gray-300 hover:text-red-500 p-1.5 disabled:opacity-30"
                                title={t('memoryManager.removeField')}
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
                          <Plus className="w-3.5 h-3.5" /> {t('memoryManager.addField')}
                        </button>
                        <p className="text-[11px] text-gray-500 mt-2">{t('memoryManager.numbersBooleans')} <code>null</code>{t('memoryManager.andJsonArraysObjectsAre')}</p>
                      </div>
                    )}

                    {slotMode === 'json' && (
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">{t('memoryManager.dataJsonObject')}</label>
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
                    <p className="text-sm">{t('memoryManager.noDataStoredYet')}<br />{t('memoryManager.addAValueForSimple')}</p>
                  </div>
                ) : structuredSlots.map(([slot, data]) => {
                  const simple = isSimpleSlot(data) && !isSlotContainer(coerceSlotValue(data.value));
                  if (simple) return (
                    <div key={slot} className={`bg-white border rounded-xl flex items-center justify-between px-4 py-2.5 ${editingSlot?.slot === slot ? 'border-indigo-300' : 'border-gray-200'}`}>
                      <div className="flex items-center gap-4 min-w-0">
                        <span className="font-mono text-sm font-semibold text-indigo-700 shrink-0">{slot}</span>
                        <span className="text-sm min-w-0">
                          {data?.value == null ? <span className="font-mono text-gray-800">—</span>
                            : <SlotValue value={data.value} max={120} />}
                        </span>
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
                                <td className="py-1.5 text-gray-800 break-words"><SlotValue value={v} max={160} /></td>
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
            <h3 className="text-lg font-semibold text-gray-600 mb-1">{t('memoryManager.noPoolSelected')}</h3>
            <p className="text-gray-400 text-sm max-w-xs">{t('memoryManager.selectAMemoryPoolFrom')}</p>
          </div>
        )}
      </div>

      {/* Create Pool Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-indigo-50">
              <h3 className="text-lg font-bold text-indigo-900">{t('memoryManager.newMemoryPool')}</h3>
              <button onClick={() => setShowCreate(false)} className="text-indigo-400 hover:text-indigo-600"><X className="w-5 h-5" /></button>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.name')}</label>
                <input required value={newName} onChange={e => setNewName(e.target.value)} placeholder={t('memoryManager.eGProjectNotesApi')}
                  className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.description')}</label>
                <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder={t('memoryManager.whatIsThisFor')} rows={3}
                  className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
              </div>
              <div className="flex gap-3 pt-1">
                <button type="button" onClick={() => setShowCreate(false)} className="flex-1 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-200 font-medium">{t('memoryManager.cancel')}</button>
                <button type="submit" className="flex-1 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 font-medium">{t('memoryManager.create')}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export { PoolsTab };

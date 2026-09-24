/**
 * The Code side panel: every `kind: "code"` view produced in this
 * conversation, grouped by filename (an agent revising a snippet may answer
 * with a new view of the same filename rather than a new version of the same
 * one, so grouping happens on the frontend, across view ids), with an editor
 * for the selected snippet and its version history, run and save actions.
 *
 * The list is derived from the conversation's own run ids (`listViews` per
 * run, the same filter the Views gallery and ViewCard use elsewhere) rather
 * than tracked separately, so a newly arrived code view_ref in the transcript
 * shows up here the moment `conversationRunIds` (Chat.jsx) picks up its run.
 */
import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle, Check, Copy, Download, Edit3, FolderGit2, History, Loader2,
  MessageSquare, Play, Save,
} from 'lucide-react';
import { useChatPage } from './context';
import { getView, listViews } from '../../api';
import { getCodeDiff, getCodeVersions, runCode, saveCodeToProject, saveCodeVersion } from '../../api/code';
import { DiffView } from './panels';
import CodeEditor from './CodeEditor';
import { isRunnable } from '../../lib/highlight';

// Resolves the theme actually applied to <html> (ThemeContext toggles the
// `dark` class there), so the editor picks the right palette under 'system'
// too, and follows a later toggle without remounting the whole panel.
function useResolvedDark() {
  const [dark, setDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  );
  useEffect(() => {
    const el = document.documentElement;
    const sync = () => setDark(el.classList.contains('dark'));
    const observer = new MutationObserver(sync);
    observer.observe(el, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

function ToolbarButton({ icon: Icon, label, onClick, disabled, active, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      className={`inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
        active
          ? 'border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
          : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
      }`}
    >
      <Icon className="w-3.5 h-3.5" /> {label}
    </button>
  );
}

function SnippetListItem({ group, selected, onSelect }) {
  const { latest } = group;
  return (
    <button
      type="button"
      onClick={() => onSelect(latest.view_id)}
      className={`w-full text-left px-3 py-2 border-b border-gray-100 dark:border-gray-800 border-l-2 ${
        selected
          ? 'bg-indigo-50 dark:bg-indigo-950 border-l-indigo-500'
          : 'border-l-transparent hover:bg-gray-50 dark:hover:bg-gray-800'
      }`}
    >
      <div className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate">{group.filename}</div>
      <div className="text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
        {latest.language && <span className="uppercase">{latest.language}</span>}
        {latest.version != null && <span>v{latest.version}</span>}
        {group.items.length > 1 && <span>· {group.items.length}</span>}
      </div>
    </button>
  );
}

export default function CodePanel() {
  const {
    conversationRunIds, projects, setInput, t, textareaRef,
  } = useChatPage();
  const dark = useResolvedDark();

  const [rows, setRows] = useState([]);
  const [envelopes, setEnvelopes] = useState({});
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [selectedViewId, setSelectedViewId] = useState(null);
  const [draft, setDraft] = useState('');
  const [saveNote, setSaveNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState(null);
  const [runError, setRunError] = useState('');
  const [mountWorkspace, setMountWorkspace] = useState(false);

  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [pickedVersions, setPickedVersions] = useState([]);
  const [diffText, setDiffText] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const [saveProjectOpen, setSaveProjectOpen] = useState(false);
  const [saveProjectId, setSaveProjectId] = useState('');
  const [saveProjectPath, setSaveProjectPath] = useState('');
  const [saveProjectOverwrite, setSaveProjectOverwrite] = useState(false);
  const [saveProjectStatus, setSaveProjectStatus] = useState('');
  const [saveProjectBusy, setSaveProjectBusy] = useState(false);

  // A stable string key so the list-fetch effect only re-runs when the set of
  // run ids actually changes membership, not on every unrelated re-render of
  // the (recomputed-per-message) Set the page hands down.
  const runIdsKey = useMemo(
    () => Array.from(conversationRunIds || []).sort().join(','),
    [conversationRunIds],
  );

  useEffect(() => {
    const ids = runIdsKey ? runIdsKey.split(',') : [];
    if (!ids.length) { setRows([]); return undefined; }
    let cancelled = false;
    setListLoading(true);
    setListError('');
    Promise.all(ids.map((id) => listViews({ run_id: id }).then((r) => r.data?.views || []).catch(() => [])))
      .then((lists) => {
        if (cancelled) return;
        const merged = new Map();
        for (const list of lists) {
          for (const row of list) {
            if (row.kind === 'code') merged.set(row.view_id, row);
          }
        }
        setRows(Array.from(merged.values()));
      })
      .catch((e) => { if (!cancelled) setListError(e?.response?.data?.detail || t('chat.code.listFailed')); })
      .finally(() => { if (!cancelled) setListLoading(false); });
    return () => { cancelled = true; };
  }, [runIdsKey, t]);

  // Full envelopes (filename/language/version/body) for whatever the list rows
  // don't have cached yet — the index rows above carry only kind/title.
  useEffect(() => {
    const missing = rows.map((r) => r.view_id).filter((id) => !envelopes[id]);
    if (!missing.length) return undefined;
    let cancelled = false;
    Promise.all(missing.map((id) => getView(id).then((r) => [id, r.data]).catch(() => [id, null])))
      .then((pairs) => {
        if (cancelled) return;
        setEnvelopes((prev) => {
          const next = { ...prev };
          for (const [id, env] of pairs) if (env) next[id] = env;
          return next;
        });
      });
    return () => { cancelled = true; };
  }, [rows, envelopes]);

  const groups = useMemo(() => {
    const byFilename = new Map();
    for (const row of rows) {
      const env = envelopes[row.view_id];
      const filename = env?.spec?.filename || row.title || row.view_id;
      const list = byFilename.get(filename) || [];
      list.push({
        view_id: row.view_id,
        version: env?.spec?.version,
        language: env?.spec?.language || '',
        updated_at: row.updated_at || row.created_at || '',
      });
      byFilename.set(filename, list);
    }
    const arr = Array.from(byFilename.entries()).map(([filename, items]) => {
      const sorted = [...items].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
      return { filename, items: sorted, latest: sorted[0] };
    });
    arr.sort((a, b) => (b.latest?.updated_at || '').localeCompare(a.latest?.updated_at || ''));
    return arr;
  }, [rows, envelopes]);

  useEffect(() => {
    if (selectedViewId) return;
    const first = groups[0]?.latest?.view_id;
    if (first) setSelectedViewId(first);
  }, [groups, selectedViewId]);

  const selectedEnvelope = selectedViewId ? envelopes[selectedViewId] : null;
  const selectedSpec = selectedEnvelope?.spec || {};

  useEffect(() => {
    setDraft(selectedSpec.body || '');
    setRunResult(null);
    setRunError('');
    setSaveError('');
    setSaveNote('');
    setVersionsOpen(false);
    setPickedVersions([]);
    setDiffText(null);
    setSaveProjectOpen(false);
    setSaveProjectStatus('');
    // Only the identity of the loaded envelope matters here: a fresh object
    // means either a different snippet was selected or a save/version-switch
    // replaced this one's content server-side.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedViewId, selectedEnvelope]);

  useEffect(() => {
    if (!saveProjectOpen) return;
    setSaveProjectPath(selectedSpec.filename || '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveProjectOpen]);

  const dirty = Boolean(selectedEnvelope) && draft !== (selectedSpec.body || '');
  const runnable = isRunnable(selectedSpec.language);

  const selectSnippet = (viewId) => {
    setSelectedViewId(viewId);
  };

  const handleCopy = async () => {
    try { await navigator.clipboard.writeText(draft); } catch { /* clipboard unavailable */ }
  };

  const handleDownload = () => {
    const blob = new Blob([draft], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = selectedSpec.filename || 'snippet.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleSaveVersion = async () => {
    if (!selectedViewId) return;
    setSaving(true);
    setSaveError('');
    try {
      const res = await saveCodeVersion(selectedViewId, draft, saveNote);
      setEnvelopes((prev) => ({ ...prev, [selectedViewId]: res.data }));
    } catch (e) {
      setSaveError(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    if (!selectedViewId) return;
    setRunning(true);
    setRunError('');
    setRunResult(null);
    try {
      const res = await runCode(selectedViewId, { body: draft, mountWorkspace });
      setRunResult(res.data);
    } catch (e) {
      setRunError(e?.response?.data?.detail || t('chat.code.runFailed'));
    } finally {
      setRunning(false);
    }
  };

  const sendRunToAgent = () => {
    if (!runResult) return;
    const filename = selectedSpec.filename || t('chat.code.untitled');
    const output = [runResult.stdout, runResult.stderr].filter(Boolean).join('\n').slice(0, 4000);
    setInput(t('chat.code.runReport', {
      filename, version: selectedSpec.version ?? '', code: runResult.exit_code, output,
    }));
    textareaRef?.current?.focus();
  };

  const handleDiscuss = () => {
    if (!selectedEnvelope) return;
    setInput(t('chat.code.discussPrefill', {
      filename: selectedSpec.filename || t('chat.code.untitled'),
      language: selectedSpec.language || '',
      body: draft,
    }));
    textareaRef?.current?.focus();
  };

  const handleEdit = () => {
    if (!selectedEnvelope) return;
    setInput(t('chat.code.editPrefill', {
      filename: selectedSpec.filename || t('chat.code.untitled'),
      version: selectedSpec.version ?? '',
    }));
    textareaRef?.current?.focus();
  };

  const toggleVersions = async () => {
    const opening = !versionsOpen;
    setVersionsOpen(opening);
    setSaveProjectOpen(false);
    if (opening && selectedViewId) {
      setVersionsLoading(true);
      try {
        const res = await getCodeVersions(selectedViewId);
        setVersions(res.data?.versions || []);
      } catch {
        setVersions([]);
      } finally {
        setVersionsLoading(false);
      }
    }
  };

  const togglePickVersion = (version) => {
    setPickedVersions((prev) => {
      if (prev.includes(version)) return prev.filter((v) => v !== version);
      if (prev.length >= 2) return [prev[1], version];
      return [...prev, version];
    });
  };

  useEffect(() => {
    if (pickedVersions.length !== 2 || !selectedViewId) { setDiffText(null); return undefined; }
    const [a, b] = [...pickedVersions].sort((x, y) => x - y);
    let cancelled = false;
    setDiffLoading(true);
    getCodeDiff(selectedViewId, a, b)
      .then((res) => { if (!cancelled) setDiffText(res.data?.diff || ''); })
      .catch(() => { if (!cancelled) setDiffText(''); })
      .finally(() => { if (!cancelled) setDiffLoading(false); });
    return () => { cancelled = true; };
  }, [pickedVersions, selectedViewId]);

  const handleSaveToProject = async () => {
    if (!selectedViewId || !saveProjectId || !saveProjectPath) return;
    setSaveProjectBusy(true);
    setSaveProjectStatus('');
    try {
      const res = await saveCodeToProject(selectedViewId, saveProjectId, saveProjectPath, saveProjectOverwrite);
      setSaveProjectStatus(t('chat.code.savedToProject', { path: res.data?.path || saveProjectPath }));
    } catch (e) {
      setSaveProjectStatus(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSaveProjectBusy(false);
    }
  };

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden">
      {/* Snippet list */}
      <div className="w-48 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 overflow-y-auto">
        {listLoading && groups.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-gray-500 p-3">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingList')}
          </div>
        ) : listError ? (
          <div className="p-3 text-xs text-red-600">{listError}</div>
        ) : groups.length === 0 ? (
          <div className="p-3 text-xs text-gray-500 italic">{t('chat.code.empty')}</div>
        ) : (
          groups.map((group) => (
            <SnippetListItem
              key={group.filename}
              group={group}
              selected={group.items.some((it) => it.view_id === selectedViewId)}
              onSelect={selectSnippet}
            />
          ))
        )}
      </div>

      {/* Editor + actions */}
      <div className="flex-1 min-w-0 flex flex-col">
        {!selectedViewId ? (
          <div className="flex-1 flex items-center justify-center text-xs text-gray-400 p-4 text-center">
            {t('chat.code.selectASnippet')}
          </div>
        ) : (
          <>
            <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate max-w-[140px]">
                {selectedSpec.filename || t('chat.code.untitled')}
              </span>
              {selectedSpec.language && (
                <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                  {selectedSpec.language}
                </span>
              )}
              {selectedSpec.version != null && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-300">
                  {t('chat.code.versionShort', { version: selectedSpec.version })}
                </span>
              )}
              {dirty && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-950 text-amber-600 dark:text-amber-300">
                  {t('chat.code.unsaved')}
                </span>
              )}
            </div>

            <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800 flex items-center gap-1.5 flex-wrap">
              <ToolbarButton icon={Copy} label={t('chat.code.copy')} onClick={handleCopy} />
              <ToolbarButton icon={Download} label={t('chat.code.download')} onClick={handleDownload} />
              <ToolbarButton
                icon={running ? Loader2 : Play}
                label={t('chat.code.run')}
                onClick={handleRun}
                disabled={!runnable || running}
                title={runnable ? t('chat.code.run') : t('chat.code.notRunnable')}
              />
              {runnable && (
                <label className="flex items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400">
                  <input
                    type="checkbox"
                    checked={mountWorkspace}
                    onChange={(e) => setMountWorkspace(e.target.checked)}
                  />
                  {t('chat.code.mountWorkspace')}
                </label>
              )}
              <ToolbarButton
                icon={saving ? Loader2 : Save}
                label={t('chat.code.saveVersion')}
                onClick={handleSaveVersion}
                disabled={!dirty || saving}
              />
              <div className="relative">
                <ToolbarButton
                  icon={FolderGit2}
                  label={t('chat.code.saveToProject')}
                  onClick={() => { setSaveProjectOpen((v) => !v); setVersionsOpen(false); }}
                  active={saveProjectOpen}
                />
                {saveProjectOpen && (
                  <div className="absolute left-0 mt-1 w-64 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20 p-3 space-y-2">
                    <label className="block text-[11px] text-gray-500 dark:text-gray-400">
                      {t('chat.code.project')}
                      <select
                        value={saveProjectId}
                        onChange={(e) => setSaveProjectId(e.target.value)}
                        className="mt-0.5 w-full text-xs border border-gray-200 dark:border-gray-700 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
                      >
                        <option value="">{t('chat.code.selectProject')}</option>
                        {(projects || []).map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-[11px] text-gray-500 dark:text-gray-400">
                      {t('chat.code.path')}
                      <input
                        value={saveProjectPath}
                        onChange={(e) => setSaveProjectPath(e.target.value)}
                        className="mt-0.5 w-full text-xs border border-gray-200 dark:border-gray-700 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
                      />
                    </label>
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300">
                      <input
                        type="checkbox"
                        checked={saveProjectOverwrite}
                        onChange={(e) => setSaveProjectOverwrite(e.target.checked)}
                      />
                      {t('chat.code.overwrite')}
                    </label>
                    <button
                      type="button"
                      onClick={handleSaveToProject}
                      disabled={!saveProjectId || !saveProjectPath || saveProjectBusy}
                      className="w-full text-xs px-2 py-1 rounded-md bg-indigo-600 text-white disabled:opacity-40 hover:bg-indigo-700"
                    >
                      {saveProjectBusy ? t('chat.code.saving') : t('chat.code.save')}
                    </button>
                    {saveProjectStatus && (
                      <p className="text-[11px] text-gray-500 dark:text-gray-400">{saveProjectStatus}</p>
                    )}
                  </div>
                )}
              </div>
              <ToolbarButton icon={MessageSquare} label={t('chat.code.discuss')} onClick={handleDiscuss} />
              <ToolbarButton icon={Edit3} label={t('chat.code.edit')} onClick={handleEdit} />
              <div className="relative">
                <ToolbarButton icon={History} label={t('chat.code.versions')} onClick={toggleVersions} active={versionsOpen} />
                {versionsOpen && (
                  <div className="absolute right-0 mt-1 w-72 max-h-80 overflow-y-auto bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20 p-2 space-y-1">
                    {versionsLoading ? (
                      <div className="flex items-center gap-2 text-xs text-gray-500 p-2">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingVersions')}
                      </div>
                    ) : versions.length === 0 ? (
                      <div className="text-xs text-gray-500 p-2">{t('chat.code.noVersions')}</div>
                    ) : (
                      <>
                        <p className="text-[10px] text-gray-400 px-1">{t('chat.code.pickTwoToCompare')}</p>
                        {versions.map((v) => (
                          <label
                            key={v.version}
                            className="flex items-center gap-2 text-xs px-1.5 py-1 rounded hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              checked={pickedVersions.includes(v.version)}
                              onChange={() => togglePickVersion(v.version)}
                            />
                            <span className="font-medium text-gray-700 dark:text-gray-200">
                              {t('chat.code.versionShort', { version: v.version })}
                            </span>
                            <span className="text-[10px] text-gray-400">{v.author}</span>
                            {v.note && <span className="text-[10px] text-gray-400 truncate">{v.note}</span>}
                          </label>
                        ))}
                        {diffLoading && (
                          <div className="flex items-center gap-2 text-xs text-gray-500 p-2">
                            <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingDiff')}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>

            {saveError && (
              <div className="px-3 py-1.5 text-[11px] text-red-600 flex items-center gap-1">
                <AlertCircle className="w-3.5 h-3.5" /> {saveError}
              </div>
            )}

            {diffText != null && (
              <div className="border-b border-gray-100 dark:border-gray-800 max-h-48 overflow-y-auto">
                <DiffView diff={diffText} />
              </div>
            )}

            <div className="flex-1 min-h-0">
              <CodeEditor
                value={draft}
                onChange={setDraft}
                language={selectedSpec.language}
                dark={dark}
                className="h-full"
              />
            </div>

            {(running || runResult || runError) && (
              <div className="border-t border-gray-100 dark:border-gray-800 p-3 space-y-2 max-h-64 overflow-y-auto">
                {running && (
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.running')}
                  </div>
                )}
                {runError && (
                  <div className="text-xs text-red-600 flex items-center gap-1">
                    <AlertCircle className="w-3.5 h-3.5" /> {runError}
                  </div>
                )}
                {runResult && (
                  <>
                    <div className="flex items-center gap-2 flex-wrap text-[11px]">
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono ${
                        runResult.exit_code === 0
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300'
                          : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'
                      }`}
                      >
                        {runResult.exit_code === 0 ? <Check className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                        {t('chat.code.exitCode', { code: runResult.exit_code })}
                      </span>
                      <span className="text-gray-400">{t('chat.code.duration', { ms: runResult.duration_ms ?? 0 })}</span>
                      {runResult.sandbox && (
                        <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                          {runResult.sandbox}
                        </span>
                      )}
                    </div>
                    {runResult.stdout && (
                      <div>
                        <div className="text-[10px] text-gray-400 mb-0.5">{t('chat.code.stdout')}</div>
                        <pre className="text-[11px] font-mono whitespace-pre-wrap bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded p-2 max-h-32 overflow-y-auto">{runResult.stdout}</pre>
                      </div>
                    )}
                    {runResult.stderr && (
                      <div>
                        <div className="text-[10px] text-gray-400 mb-0.5">{t('chat.code.stderr')}</div>
                        <pre className="text-[11px] font-mono whitespace-pre-wrap bg-red-50 dark:bg-red-950 border border-red-100 dark:border-red-900 rounded p-2 max-h-32 overflow-y-auto text-red-700 dark:text-red-300">{runResult.stderr}</pre>
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={sendRunToAgent}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                    >
                      <MessageSquare className="w-3.5 h-3.5" /> {t('chat.code.sendToAgent')}
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

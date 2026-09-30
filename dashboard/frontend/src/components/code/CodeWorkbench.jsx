/**
 * A code snippet at work: the editor with everything done to a snippet, in
 * one place for the chat's Code panel (components/chat/CodePanel.jsx) and a
 * code view's own page (pages/ViewDetail.jsx).
 *
 * The snippet comes in as an envelope, a code view's or the same shape made
 * from a reply's fenced block (`reply: true`); the host owns the list, the
 * selection and the envelope cache, this owns what happens to the selected
 * one: the draft, Run and its result, versions and their diff, Save to
 * project, and (for a view) the recorded run history. What needs the host is
 * handed in: `onVersionSaved` with what the backend returned, `saveAsView`
 * for a reply's block, `chat` when there is a composer to prefill.
 */
import { useEffect, useState } from 'react';
import {
  AlertCircle, BookmarkPlus, Check, Copy, Download, Edit3, FolderGit2, History, Info, ListChecks, Loader2,
  MessageSquare, Play, Save,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  getCodeDiff, getCodeRuns, getCodeVersions, getSnippetDiff, getSnippetVersions, runCode, runSnippet,
  saveCodeBody, saveCodeToProject, saveCodeVersion, saveSnippetToProject, saveSnippetVersion,
} from '../../api/code';
import { DiffView } from '../chat/panels';
import CodeEditor from '../chat/CodeEditor';
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

// `tone="run"`: the one button that does something, green among the grey.
function ToolbarButton({ icon: Icon, label, onClick, disabled, active, title, tone }) {
  const look = tone === 'run'
    ? 'border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700 dark:border-emerald-500 dark:bg-emerald-600'
    : active
      ? 'border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
      : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      className={`inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${look}`}
    >
      <Icon className="w-3.5 h-3.5" /> {label}
    </button>
  );
}

// How a mounted workspace is reached from a snippet, per runnable language:
// $WORK is set by both the docker and the local provider (sandbox/).
const MOUNT_EXAMPLE = {
  python: 'import os, pathlib\nroot = pathlib.Path(os.environ["WORK"])\nprint((root / "README.md").read_text())',
  javascript: 'const fs = require("fs");\nconst path = require("path");\nconst root = process.env.WORK;\nconsole.log(fs.readFileSync(path.join(root, "README.md"), "utf8"));',
  bash: 'cat "$WORK/README.md"\nls "$WORK"',
};
MOUNT_EXAMPLE.node = MOUNT_EXAMPLE.javascript;
MOUNT_EXAMPLE.js = MOUNT_EXAMPLE.javascript;
MOUNT_EXAMPLE.sh = MOUNT_EXAMPLE.bash;
MOUNT_EXAMPLE.shell = MOUNT_EXAMPLE.bash;

const popover = 'absolute mt-1 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20';

const formatWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

/**
 * @param {object}  props
 * @param {object}  props.envelope       the snippet: a code view's envelope, or a reply block's (`reply: true`).
 * @param {boolean} [props.isReply]
 * @param {string}  [props.replyKey]     the key a reply block's versions are kept under.
 * @param {string}  [props.workspace]    where a reply block runs and versions; a view has its own.
 * @param {Array}   [props.projects]     for Save to project.
 * @param {function} [props.onVersionSaved] (data) after Save version: the view envelope, or a reply's versions.
 * @param {function} [props.onDraftChange] told the editor's text as it changes (the host's "Save as view" saves it).
 * @param {{onClick: function, busy: boolean, error: string}} [props.saveAsView] the reply block's "Save as view".
 * @param {{setInput: function}} [props.chat] a composer to prefill: Discuss, Edit and the run report show when given.
 * @param {boolean} [props.runHistory]   a Runs button with the view's recorded runs.
 * @param {string}  [props.className]
 */
export default function CodeWorkbench({
  envelope, isReply = false, replyKey = null, workspace, projects, onVersionSaved = () => {},
  onDraftChange = null, saveAsView = null, chat = null, runHistory = false, className = '',
}) {
  const { t } = useI18n();
  const dark = useResolvedDark();
  const spec = envelope?.spec || {};
  const viewId = envelope?.view_id || null;

  const [draft, setDraft] = useState('');
  const [saveNote, setSaveNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [savingInPlace, setSavingInPlace] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState(null);
  const [runError, setRunError] = useState('');
  const [mountWorkspace, setMountWorkspace] = useState(false);
  const [mountHelpOpen, setMountHelpOpen] = useState(false);

  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [pickedVersions, setPickedVersions] = useState([]);
  const [diffText, setDiffText] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const [runsOpen, setRunsOpen] = useState(false);
  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(false);

  const [saveProjectOpen, setSaveProjectOpen] = useState(false);
  const [saveProjectId, setSaveProjectId] = useState('');
  const [saveProjectPath, setSaveProjectPath] = useState('');
  const [saveProjectOverwrite, setSaveProjectOverwrite] = useState(false);
  const [saveProjectStatus, setSaveProjectStatus] = useState('');
  const [saveProjectBusy, setSaveProjectBusy] = useState(false);

  useEffect(() => {
    setDraft(spec.body || '');
    setRunResult(null);
    setRunError('');
    setSaveError('');
    setSaveNote('');
    setVersionsOpen(false);
    setPickedVersions([]);
    setDiffText(null);
    setRunsOpen(false);
    setSaveProjectOpen(false);
    setSaveProjectStatus('');
    setMountHelpOpen(false);
    // Only the identity of the loaded envelope matters here: a fresh object
    // means either a different snippet was selected or a save/version-switch
    // replaced this one's content server-side.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewId, envelope]);

  useEffect(() => {
    if (!saveProjectOpen) return;
    setSaveProjectPath(spec.filename || '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveProjectOpen]);

  useEffect(() => { if (onDraftChange) onDraftChange(draft); }, [draft, onDraftChange]);

  const dirty = Boolean(envelope) && draft !== (spec.body || '');
  const runnable = isRunnable(spec.language);
  const closeAll = () => { setVersionsOpen(false); setRunsOpen(false); setSaveProjectOpen(false); };

  const handleCopy = async () => {
    try { await navigator.clipboard.writeText(draft); } catch { /* clipboard unavailable */ }
  };

  const handleDownload = () => {
    const blob = new Blob([draft], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = spec.filename || 'snippet.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleSaveVersion = async () => {
    if (!viewId) return;
    setSaving(true);
    setSaveError('');
    try {
      if (isReply) {
        const res = await saveSnippetVersion({ key: replyKey, body: draft, note: saveNote, workspace, base: spec.base ?? spec.body });
        onVersionSaved(res.data?.versions || []);
      } else {
        const res = await saveCodeVersion(viewId, draft, saveNote);
        onVersionSaved(res.data);
      }
    } catch (e) {
      setSaveError(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  // The plain Save of a view: the current version gets the editor's text,
  // its number stays. A reply's block has no current version to overwrite.
  const handleSaveInPlace = async () => {
    if (!viewId || isReply) return;
    setSavingInPlace(true);
    setSaveError('');
    try {
      const res = await saveCodeBody(viewId, draft);
      onVersionSaved(res.data);
    } catch (e) {
      setSaveError(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSavingInPlace(false);
    }
  };

  const loadRuns = async () => {
    if (!viewId || isReply) return;
    setRunsLoading(true);
    try {
      const res = await getCodeRuns(viewId);
      setRuns(res.data?.runs || []);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  };

  const handleRun = async () => {
    if (!viewId) return;
    setRunning(true);
    setRunError('');
    setRunResult(null);
    try {
      const res = isReply
        ? await runSnippet({ language: spec.language, body: draft, mountWorkspace, workspace })
        : await runCode(viewId, { body: draft, mountWorkspace });
      setRunResult(res.data);
      if (runHistory && runsOpen) loadRuns();
    } catch (e) {
      setRunError(e?.response?.data?.detail || t('chat.code.runFailed'));
    } finally {
      setRunning(false);
    }
  };

  const toggleRuns = async () => {
    const opening = !runsOpen;
    closeAll();
    setRunsOpen(opening);
    if (opening) loadRuns();
  };

  // A recorded run shown where a fresh one would be, marked as history.
  const showRun = (run) => {
    setRunError('');
    setRunResult({ ...run, sandbox: run.sandbox || '', recorded: true });
    setRunsOpen(false);
  };

  const sendRunToAgent = () => {
    if (!runResult || !chat) return;
    const filename = spec.filename || t('chat.code.untitled');
    const output = [runResult.stdout, runResult.stderr].filter(Boolean).join('\n').slice(0, 4000);
    chat.setInput(t('chat.code.runReport', {
      filename, version: runResult.version ?? spec.version ?? '', code: runResult.exit_code, output,
    }));
  };

  const handleDiscuss = () => {
    if (!envelope || !chat) return;
    chat.setInput(t('chat.code.discussPrefill', {
      filename: spec.filename || t('chat.code.untitled'),
      language: spec.language || '',
      body: draft,
    }));
  };

  const handleEdit = () => {
    if (!envelope || !chat) return;
    chat.setInput(t('chat.code.editPrefill', {
      filename: spec.filename || t('chat.code.untitled'),
      version: spec.version ?? '',
    }));
  };

  const toggleVersions = async () => {
    const opening = !versionsOpen;
    closeAll();
    setVersionsOpen(opening);
    if (opening && viewId) {
      setVersionsLoading(true);
      try {
        const res = isReply
          ? await getSnippetVersions(replyKey, workspace)
          : await getCodeVersions(viewId);
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
    if (pickedVersions.length !== 2 || !viewId) { setDiffText(null); return undefined; }
    const [a, b] = [...pickedVersions].sort((x, y) => x - y);
    let cancelled = false;
    setDiffLoading(true);
    (isReply ? getSnippetDiff(replyKey, a, b, workspace) : getCodeDiff(viewId, a, b))
      .then((res) => { if (!cancelled) setDiffText(res.data?.diff || ''); })
      .catch(() => { if (!cancelled) setDiffText(''); })
      .finally(() => { if (!cancelled) setDiffLoading(false); });
    return () => { cancelled = true; };
    // replyKey and workspace follow the envelope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedVersions, viewId]);

  const handleSaveToProject = async () => {
    if (!viewId || !saveProjectId || !saveProjectPath) return;
    setSaveProjectBusy(true);
    setSaveProjectStatus('');
    try {
      const res = isReply
        ? await saveSnippetToProject({ projectId: saveProjectId, path: saveProjectPath, body: draft, overwrite: saveProjectOverwrite })
        : await saveCodeToProject(viewId, saveProjectId, saveProjectPath, saveProjectOverwrite);
      setSaveProjectStatus(t('chat.code.savedToProject', { path: res.data?.path || saveProjectPath }));
    } catch (e) {
      setSaveProjectStatus(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSaveProjectBusy(false);
    }
  };

  if (!envelope) return null;
  const errorText = saveError || saveAsView?.error || '';

  return (
    <div className={`flex flex-col min-h-0 ${className}`} data-testid="code-workbench">
      <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800 flex items-center gap-2 flex-wrap">
        <span className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate max-w-[140px]">
          {spec.filename || t('chat.code.untitled')}
        </span>
        {spec.language && (
          <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
            {spec.language}
          </span>
        )}
        {spec.version != null && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-300">
            {t('chat.code.versionShort', { version: spec.version })}
          </span>
        )}
        {isReply && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400" title={t('chat.code.fromRepliesHint')}>
            {t('chat.code.replyUnsaved')}
          </span>
        )}
        {dirty && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-950 text-amber-600 dark:text-amber-300">
            {t('chat.code.unsaved')}
          </span>
        )}
        {/* Everything done with the snippet, at the right of the name: read,
            look back, talk about it, ask for changes, run it. */}
        <div className="ml-auto flex items-center gap-1.5 flex-wrap">
          <ToolbarButton icon={Copy} label={t('chat.code.copy')} onClick={handleCopy} />
          <ToolbarButton icon={Download} label={t('chat.code.download')} onClick={handleDownload} />
          <div className="relative">
            <ToolbarButton icon={History} label={t('chat.code.versions')} onClick={toggleVersions} active={versionsOpen} />
            {versionsOpen && (
              <div className={`${popover} right-0 w-72 max-h-80 overflow-y-auto p-2 space-y-1`}>
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
          {runHistory && !isReply && (
            <div className="relative">
              <ToolbarButton icon={ListChecks} label={t('chat.code.runs')} title={t('chat.code.runsHint')} onClick={toggleRuns} active={runsOpen} />
              {runsOpen && (
                <div className={`${popover} right-0 w-80 max-h-80 overflow-y-auto p-2 space-y-1`} data-testid="run-history">
                  {runsLoading ? (
                    <div className="flex items-center gap-2 text-xs text-gray-500 p-2">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingRuns')}
                    </div>
                  ) : runs.length === 0 ? (
                    <div className="text-xs text-gray-500 p-2">{t('chat.code.noRuns')}</div>
                  ) : runs.map((run, i) => (
                    <button
                      key={`${run.at || run.created_at || ''}-${i}`}
                      type="button"
                      onClick={() => showRun(run)}
                      className="w-full flex items-center gap-2 text-xs px-1.5 py-1 rounded hover:bg-gray-50 dark:hover:bg-gray-800 text-left"
                    >
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono ${
                        run.exit_code === 0
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300'
                          : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'}`}
                      >
                        {run.exit_code === 0 ? <Check className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                        {t('chat.code.exitCode', { code: run.exit_code })}
                      </span>
                      {run.version != null && (
                        <span className="text-gray-500 dark:text-gray-400">{t('chat.code.versionShort', { version: run.version })}</span>
                      )}
                      <span className="text-gray-400">{t('chat.code.duration', { ms: run.duration_ms ?? 0 })}</span>
                      <span className="ml-auto text-[10px] text-gray-400 truncate">{formatWhen(run.at || run.created_at)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {chat && <ToolbarButton icon={MessageSquare} label={t('chat.code.discuss')} onClick={handleDiscuss} />}
          {chat && <ToolbarButton icon={Edit3} label={t('chat.code.edit')} onClick={handleEdit} />}
          <ToolbarButton
            icon={running ? Loader2 : Play}
            label={t('chat.code.run')}
            tone="run"
            onClick={handleRun}
            disabled={!runnable || running}
            title={runnable ? t('chat.code.runHint') : t('chat.code.notRunnable')}
          />
        </div>
      </div>

      {/* Where it is kept: as a view, as a version, as a file in a project. */}
      <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800 flex items-center gap-1.5 flex-wrap">
        {isReply && saveAsView && (
          <ToolbarButton
            icon={saveAsView.busy ? Loader2 : BookmarkPlus}
            label={t('chat.code.saveAsView')}
            title={t('chat.code.saveAsViewHint')}
            onClick={saveAsView.onClick}
            disabled={saveAsView.busy}
            active
          />
        )}
        {!isReply && (
          <ToolbarButton
            icon={savingInPlace ? Loader2 : Save}
            label={t('chat.code.saveInPlace')}
            title={t('chat.code.saveInPlaceHint', { version: spec.version ?? 1 })}
            onClick={handleSaveInPlace}
            disabled={!dirty || savingInPlace || saving}
          />
        )}
        <ToolbarButton
          icon={saving ? Loader2 : Save}
          label={t('chat.code.saveVersion')}
          title={t('chat.code.saveVersionHint')}
          onClick={handleSaveVersion}
          disabled={!dirty || saving || savingInPlace}
        />
        <div className="relative">
          <ToolbarButton
            icon={FolderGit2}
            label={t('chat.code.saveToProject')}
            onClick={() => { const next = !saveProjectOpen; closeAll(); setSaveProjectOpen(next); }}
            active={saveProjectOpen}
          />
          {saveProjectOpen && (
            <div className={`${popover} left-0 w-64 p-3 space-y-2`}>
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
        {/* The mount switch, after the saves: it belongs to Run, but the row above is full. */}
        {runnable && (
          <span className="flex items-center gap-1">
            <label className="flex items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400" title={t('chat.code.mountWorkspaceHint')}>
              <input
                type="checkbox"
                checked={mountWorkspace}
                onChange={(e) => setMountWorkspace(e.target.checked)}
              />
              {t('chat.code.mountWorkspace')}
            </label>
            {/* How the snippet reaches the files once mounted. */}
            <span className="relative inline-flex items-center">
              <button
                type="button"
                onClick={() => setMountHelpOpen((v) => !v)}
                aria-label={t('chat.code.mountFilesHelp')}
                aria-expanded={mountHelpOpen}
                data-testid="mount-help"
                className={`inline-flex items-center p-0.5 rounded text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-300 ${mountHelpOpen ? 'text-indigo-600 dark:text-indigo-300' : ''}`}
              >
                <Info className="w-3.5 h-3.5" />
              </button>
              {mountHelpOpen && (
                <div className={`${popover} left-0 w-80 p-3 text-[11px] leading-relaxed text-gray-600 dark:text-gray-300 space-y-1.5`}>
                  <p>{t('chat.code.mountFilesIntro')}</p>
                  <p>{t('chat.code.mountFilesDocker')}</p>
                  <p>{t('chat.code.mountFilesLocal')}</p>
                  <pre className="font-mono text-[10px] bg-gray-50 dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded p-2 whitespace-pre overflow-x-auto">{MOUNT_EXAMPLE[spec.language] || MOUNT_EXAMPLE.python}</pre>
                  <p>{t('chat.code.mountFilesRule')}</p>
                </div>
              )}
            </span>
          </span>
        )}
      </div>

      {errorText && (
        <div className="px-3 py-1.5 text-[11px] text-red-600 flex items-center gap-1">
          <AlertCircle className="w-3.5 h-3.5" /> {errorText}
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
          language={spec.language}
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
                {runResult.recorded && (
                  <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400" data-testid="recorded-run">
                    {t('chat.code.recordedRun', { version: runResult.version ?? '', time: formatWhen(runResult.at || runResult.created_at) })}
                  </span>
                )}
              </div>
              {/* The sandbox could not run it at all (no docker daemon, no
                  provider): the reason, not just an exit code of -1. */}
              {runResult.error && (
                <div className="text-xs text-red-600 dark:text-red-400 flex items-start gap-1" data-testid="run-error">
                  <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> <span>{runResult.error}</span>
                </div>
              )}
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
              {chat && (
                <button
                  type="button"
                  onClick={sendRunToAgent}
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  <MessageSquare className="w-3.5 h-3.5" /> {t('chat.code.sendToAgent')}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

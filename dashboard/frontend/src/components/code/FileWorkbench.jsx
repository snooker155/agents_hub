/**
 * A workspace file in the code editor: the "Editor" and "Run" of a file in
 * the chat's Artifacts panel (components/chat/panels.jsx) and on the
 * Artifacts page. Reads the file, hands it to CodeWorkbench as a file (Save
 * writes it back, Run runs it from its folder: views/file_run.py), and keeps
 * the saved text as what the editor compares against.
 */
import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { getWorkspaceFileContent } from '../../api/workspaces';
import { codeLanguageFor } from '../../lib/codeLanguage';
import CodeWorkbench from './CodeWorkbench';

/**
 * @param {object}  props
 * @param {string}  props.workspace
 * @param {string}  props.path       workspace-relative
 * @param {number}  [props.autoRun]  a nonce: run once the file is read
 * @param {{setInput: function}} [props.chat] a composer to prefill (Discuss, Edit, the run report)
 * @param {function} [props.onSaved] (path) after Save
 * @param {string}  [props.className]
 */
export default function FileWorkbench({ workspace, path, autoRun = null, chat = null, onSaved = null, className = '' }) {
  const { t } = useI18n();
  // Keyed by the file: a new one starts loading, with nothing shown.
  const key = `${workspace}:${path}`;
  const [state, setState] = useState({ key: null, body: null, error: '' });
  useEffect(() => {
    let cancelled = false;
    getWorkspaceFileContent(workspace, path)
      .then((r) => { if (!cancelled) setState({ key, body: r.data?.content ?? '', error: '' }); })
      .catch((e) => {
        if (!cancelled) setState({ key, body: null, error: e?.response?.data?.detail || t('chat.artifactContentFailed') });
      });
    return () => { cancelled = true; };
  }, [key, workspace, path, t]);

  const loaded = state.key === key ? state : { body: null, error: '' };
  const name = String(path || '').split('/').pop();
  // A fresh object only when the saved text changes: the workbench resets its
  // draft on a new envelope.
  const envelope = useMemo(() => (loaded.body == null ? null : {
    view_id: `file:${path}`, kind: 'code', file: true,
    spec: { filename: name, language: codeLanguageFor(path), body: loaded.body },
  }), [loaded.body, path, name]);
  const file = useMemo(() => ({ workspace, path }), [workspace, path]);

  if (loaded.error) return <div className="p-3 text-xs text-red-600" data-testid="file-workbench-error">{loaded.error}</div>;
  if (!envelope) {
    return (
      <div className="flex items-center gap-2 p-3 text-xs text-gray-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.artifactLoading')}
      </div>
    );
  }
  return (
    <CodeWorkbench
      envelope={envelope}
      file={file}
      workspace={workspace}
      autoRun={autoRun}
      chat={chat}
      onVersionSaved={(data) => {
        setState({ key, body: data?.content ?? '', error: '' });
        if (onSaved) onSaved(path);
      }}
      className={className}
    />
  );
}

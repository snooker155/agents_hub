import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, Copy, KeyRound, Lock, Plus, RefreshCw, Trash2, Unlock, X } from 'lucide-react';
import {
  forgetHubSyncKey, getHubSync, getHubSyncConflict, listHubSyncConflicts, notifySyncChanged, relativeTime,
  resolveHubSyncConflict, runHubSync, saveHubSync, setHubSyncKey,
} from '../../api/hubSync';
import { getWorkspaces } from '../../api/workspaces';
import { SectionCard, inputCls } from '../settingsUi';
import { MULTI, isAdmin, useAuth } from '../auth';
import { useI18n } from '../../i18n';

// A detail is a string, or an object with the server's own `message` when it
// carries more than text (the key route's key_exists answer).
const detailOf = (e, fallback) => {
  const detail = e?.response?.data?.detail;
  if (detail && typeof detail === 'object') return detail.message || fallback;
  return detail || e?.message || fallback;
};

// The key route answers a generate for an account that already has a remote
// key with 409 and { message, code: 'key_exists', replace_unused }. Other 409s
// keep a plain string, so the object shape alone does not identify this one.
const keyExistsDetail = (e) => {
  const detail = e?.response?.data?.detail;
  if (e?.response?.status !== 409 || !detail || typeof detail !== 'object') return null;
  return detail.code === 'key_exists' ? detail : null;
};

/** The dialog that sets the end-to-end key: a fresh passphrase or the one from another device. */
export function KeyDialog({ onClose, onDone }) {
  const { t } = useI18n();
  const [mode, setMode] = useState(null); // null | 'generate' | 'have'
  const [passphrase, setPassphrase] = useState('');
  const [generated, setGenerated] = useState('');
  const [stored, setStored] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [canStartOver, setCanStartOver] = useState(false);

  // The server publishes a generated key before the passphrase reaches this
  // dialog, and the passphrase is kept nowhere. Closing before it is stored
  // (or while the request is still out) would leave the account with a key
  // nobody can open, so every way out waits for the "stored" tick.
  const guarded = mode === 'generate' && (busy || (Boolean(generated) && !stored));

  const finish = () => { setGenerated(''); onDone(); };
  const close = () => {
    if (guarded) return;
    if (generated) finish();
    else onClose();
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  // The same rule for leaving the page. A reload or a closed tab gets the
  // browser's own prompt. In-app links get a confirm: the app runs on a
  // BrowserRouter, where react-router's useBlocker is not available, and a
  // link is how the shell navigates (a link behind the overlay is still
  // reachable with Tab and Enter). The capture phase runs before the
  // router's own click handler, so a refusal stops the navigation.
  const leaveConfirm = t('hubSync.keyDialog.leaveConfirm');
  useEffect(() => {
    if (!guarded) return undefined;
    const onUnload = (e) => { e.preventDefault(); e.returnValue = ''; };
    const onClick = (e) => {
      const link = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (!link || link.target === '_blank' || e.defaultPrevented) return;
      if (window.confirm(leaveConfirm)) return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener('beforeunload', onUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [guarded, leaveConfirm]);

  const submit = async (body) => {
    setBusy(true); setError(''); setCanStartOver(false);
    try {
      const { data } = await setHubSyncKey(body);
      if (body.generate) setGenerated(data?.generated_passphrase || '');
      else onDone();
    } catch (e) {
      const status = e?.response?.status;
      const exists = keyExistsDetail(e);
      // Only the linked hub knows whether any record is sealed with the old
      // key, so starting over is offered only on its replace_unused word.
      setCanStartOver(Boolean(body.generate) && exists?.replace_unused === true);
      setError(status === 403 ? t('hubSync.keyDialog.wrong')
        : exists ? (exists.message || t('hubSync.keyDialog.keyExists'))
          : status === 409 ? detailOf(e, t('hubSync.keyDialog.noSecret'))
            : detailOf(e, t('hubSync.keyDialog.failed')));
    } finally {
      setBusy(false);
    }
  };

  // Replacing deletes the old key on the linked hub for good, so the person
  // confirms knowing it protects nothing yet and its passphrase stops working.
  const startOver = () => {
    if (!window.confirm(t('hubSync.keyDialog.startOverConfirm'))) return;
    submit({ generate: true, replace_unused: true });
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(generated); setCopied(true); } catch { /* the box can still be selected by hand */ }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" data-testid="sync-key-dialog"
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900 flex items-center gap-2"><KeyRound className="w-4 h-4" />{t('hubSync.keyDialog.title')}</h3>
          <button type="button" onClick={close} disabled={guarded} data-testid="sync-key-close"
            aria-label={t('hubSync.keyDialog.cancel')} title={guarded && generated ? t('hubSync.keyDialog.storeFirst') : undefined}
            className="p-1 rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"><X className="w-4 h-4" /></button>
        </div>
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{t('hubSync.encryption.warning')}</p>

        {!mode && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button type="button" onClick={() => { setMode('generate'); submit({ generate: true }); }} data-testid="sync-key-generate"
              className="text-left rounded-lg border border-gray-200 p-3 hover:bg-gray-50">
              <div className="text-sm font-medium text-gray-900">{t('hubSync.keyDialog.generate')}</div>
              <div className="text-[11px] text-gray-500 mt-1">{t('hubSync.keyDialog.generateHint')}</div>
            </button>
            <button type="button" onClick={() => setMode('have')} data-testid="sync-key-have"
              className="text-left rounded-lg border border-gray-200 p-3 hover:bg-gray-50">
              <div className="text-sm font-medium text-gray-900">{t('hubSync.keyDialog.have')}</div>
              <div className="text-[11px] text-gray-500 mt-1">{t('hubSync.keyDialog.haveHint')}</div>
            </button>
          </div>
        )}

        {mode === 'have' && (
          <div className="space-y-2">
            <label className="block text-xs font-medium text-gray-500">{t('hubSync.keyDialog.passphrase')}</label>
            <input type="password" className={inputCls} value={passphrase} onChange={(e) => setPassphrase(e.target.value)} data-testid="sync-key-input" autoComplete="off" />
            <button type="button" disabled={busy || !passphrase.trim()} onClick={() => submit({ passphrase })} data-testid="sync-key-use"
              className="px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
              {t('hubSync.keyDialog.use')}
            </button>
          </div>
        )}

        {mode === 'generate' && generated && (
          <div className="space-y-3">
            <div className="text-xs font-medium text-gray-500">{t('hubSync.keyDialog.yourPassphrase')}</div>
            <div className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 font-mono text-base sm:text-lg break-all select-all" data-testid="sync-passphrase">{generated}</div>
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={copy} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50">
                <Copy className="w-4 h-4" />{copied ? t('hubSync.keyDialog.copied') : t('hubSync.keyDialog.copy')}
              </button>
              <span className="text-[11px] text-gray-500">{t('hubSync.keyDialog.shownOnce')}</span>
            </div>
            <label className="flex items-start gap-2 text-sm text-gray-800">
              <input type="checkbox" className="mt-0.5" checked={stored} onChange={(e) => setStored(e.target.checked)} data-testid="sync-passphrase-stored" />
              <span>{t('hubSync.keyDialog.stored')}</span>
            </label>
            <button type="button" disabled={!stored} onClick={finish} data-testid="sync-key-done"
              className="px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
              {t('hubSync.keyDialog.done')}
            </button>
          </div>
        )}

        {busy && mode === 'generate' && !generated && <RefreshCw className="w-4 h-4 animate-spin text-gray-400" />}
        {error && (
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>
            {mode === 'generate' && !generated && canStartOver && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 space-y-2">
                <p className="text-[11px] text-amber-800">{t('hubSync.keyDialog.startOverHint')}</p>
                <button type="button" disabled={busy} onClick={startOver} data-testid="sync-key-start-over"
                  className="px-3 py-1.5 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t('hubSync.keyDialog.startOver')}
                </button>
              </div>
            )}
            {mode === 'generate' && !generated && (
              <button type="button" onClick={() => { setMode(null); setError(''); setCanStartOver(false); }} className="text-xs text-indigo-600 hover:underline">{t('hubSync.keyDialog.cancel')}</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ConflictRow({ conflict, canEdit, onResolved }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (doc !== null) return;
    try {
      const { data } = await getHubSyncConflict(conflict.conflict_id);
      setDoc(data?.lost_doc ?? {});
    } catch (e) {
      setError(detailOf(e, t('hubSync.conflicts.loadFailed')));
    }
  };
  const dismiss = async () => {
    try {
      await resolveHubSyncConflict(conflict.conflict_id);
      onResolved();
    } catch (e) {
      setError(detailOf(e, t('hubSync.conflicts.dismissFailed')));
    }
  };

  return (
    <li className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm" data-testid="sync-conflict">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-gray-800">{conflict.kind}</span>
        <span className="font-mono text-xs text-gray-500 break-all">{conflict.record_id}</span>
        <span className="text-xs text-gray-500">{t('hubSync.conflicts.kept', { side: t(`hubSync.conflicts.${conflict.kept === 'remote' ? 'remote' : 'local'}`) })}</span>
        <span className="ml-auto flex items-center gap-3">
          <button type="button" onClick={toggle} className="text-xs text-indigo-600 hover:underline">
            {open ? t('hubSync.conflicts.hide') : t('hubSync.conflicts.show')}
          </button>
          {canEdit && <button type="button" onClick={dismiss} className="text-xs text-gray-600 hover:underline">{t('hubSync.conflicts.dismiss')}</button>}
        </span>
      </div>
      {open && doc !== null && (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-white border border-gray-200 p-2 text-[11px] text-gray-700" data-testid="sync-conflict-doc">{JSON.stringify(doc, null, 2)}</pre>
      )}
      {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </li>
  );
}

/**
 * Settings, hub link tab: two-way sync of chosen workspaces with the linked hub,
 * optionally end-to-end encrypted (hubsync/, routes/sync.py).
 */
export default function HubLinkSyncCard() {
  const { t, language } = useI18n();
  const auth = useAuth();
  const canEdit = auth?.mode !== MULTI || isAdmin(auth);

  const [status, setStatus] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [enabled, setEnabled] = useState(false);
  // The mapping row whose Encrypt box asked for a key first; it is ticked
  // once the key is set.
  const [pendingRow, setPendingRow] = useState(null);
  // Every workspace sealed, default and the others, also later ones (the
  // start `ah setup` offers); the per row boxes wait while it is on.
  const [encryptAll, setEncryptAll] = useState(false);
  const [conflicts, setConflicts] = useState([]);
  const [keyDialog, setKeyDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getHubSync();
      setStatus(data);
      setEnabled(!!data.enabled);
      setMappings((data.mappings || []).map((m) => ({ workspace: m.workspace, space: m.space, encrypted: !!m.encrypted })));
      setEncryptAll(!!data.encrypt_all);
      if (data.conflicts > 0) {
        const res = await listHubSyncConflicts();
        setConflicts(res.data?.conflicts || []);
      } else {
        setConflicts([]);
      }
      setError('');
    } catch (e) {
      setError(detailOf(e, t('hubSync.card.loadFailed')));
    }
  }, [t]);
  // Read once on mount (the actions reload after themselves); `load` changes
  // with the language only, which must not refetch and drop unsaved rows.
  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps -- the loader is shared with the actions; it sets state after its await
  useEffect(() => { load(); }, []);
  useEffect(() => {
    getWorkspaces().then((r) => setWorkspaces((r.data || []).map((w) => w.name))).catch(() => setWorkspaces([]));
  }, []);

  const setRow = (i, patch) => setMappings((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => {
    const used = new Set(mappings.map((m) => m.workspace));
    const next = workspaces.find((w) => !used.has(w)) || '';
    setMappings((rows) => [...rows, { workspace: next, space: next, encrypted: false }]);
  };
  const valid = mappings.filter((m) => m.workspace && m.space.trim());

  const encrypted = encryptAll || mappings.some((m) => m.encrypted);

  const toggleEncryptAll = (on) => {
    if (on && !status?.key_set) { setPendingRow('all'); setKeyDialog(true); return; }
    setEncryptAll(on);
  };

  const toggleEncrypted = (i, on) => {
    if (on && !status?.key_set) { setPendingRow(i); setKeyDialog(true); return; }
    setRow(i, { encrypted: on });
  };

  const save = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const { data } = await saveHubSync({
        enabled, encrypt_all: encryptAll,
        mappings: valid.map((m) => ({ workspace: m.workspace, space: m.space.trim(), encrypted: encryptAll || !!m.encrypted })),
      });
      setStatus(data);
      setNotice(t('hubSync.card.saved'));
      notifySyncChanged();
      await load();
    } catch (e) {
      setError(detailOf(e, t('hubSync.card.saveFailed')));
    } finally {
      setBusy(false);
    }
  };

  const syncNow = async () => {
    setRunning(true); setError(''); setNotice('');
    try {
      await runHubSync();
      notifySyncChanged();
      await load();
    } catch (e) {
      setError(detailOf(e, t('hubSync.run.failed')));
    } finally {
      setRunning(false);
    }
  };

  const forgetKey = async () => {
    if (!window.confirm(t('hubSync.encryption.forgetConfirm'))) return;
    try {
      await forgetHubSyncKey();
      notifySyncChanged();
      await load();
    } catch (e) {
      setError(detailOf(e, t('hubSync.encryption.forgetFailed')));
    }
  };

  const keyDone = async () => {
    setKeyDialog(false);
    const row = pendingRow;
    setPendingRow(null);
    notifySyncChanged();
    // The reload brings back the saved mappings; the row that asked for the
    // key is ticked on top of them, to be saved with the rest.
    const ticked = typeof row === 'number' ? mappings.map((m, j) => (j === row ? { ...m, encrypted: true } : m)) : mappings;
    setMappings(ticked);
    if (row === 'all') setEncryptAll(true);
    await load();
    setMappings(ticked);
    if (row === 'all') setEncryptAll(true);
  };

  if (!status) {
    return error ? <p className="text-sm text-red-600">{error}</p> : null;
  }
  if (!status.linked) {
    return (
      <SectionCard title={t('hubSync.card.title')}>
        <div data-testid="sync-card-unlinked" className="space-y-3">
          <p className="text-sm text-gray-600">{t('hubSync.card.intro')}</p>
          <p className="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 flex items-start gap-2">
            <Lock className="w-4 h-4 mt-0.5 shrink-0 text-gray-400" />{t('hubSync.card.notLinked')}
          </p>
        </div>
      </SectionCard>
    );
  }
  const lr = status.last_result || {};
  const errors = lr.errors || [];
  const cannotEnable = enabled && valid.length === 0;
  const ro = !canEdit;

  return (
    <SectionCard
      title={t('hubSync.card.title')}
      actions={enabled ? (
        <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-50 text-green-700">
          {encrypted ? <Lock className="w-3 h-3" /> : <RefreshCw className="w-3 h-3" />}
          {encrypted ? t('hubSync.indicator.encrypted') : t('hubSync.indicator.plain')}
        </span>
      ) : null}
    >
      <div data-testid="sync-card" className="space-y-5">
        <p className="text-sm text-gray-600">{t('hubSync.card.intro')}</p>
        <p className="text-[11px] text-gray-400">{t('hubSync.card.replacesMirror')}</p>
        {ro && <p className="text-xs text-gray-500">{t('hubSync.card.readOnly')}</p>}

        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('hubSync.card.mappings')}</div>
          <p className="text-[11px] text-gray-400">{t('hubSync.card.mappingsHint')}</p>
          {mappings.map((m, i) => (
            <div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_auto_auto] gap-2 items-end" data-testid="sync-mapping">
              <div>
                {i === 0 && <label className="block text-xs font-medium text-gray-500 mb-1">{t('hubSync.card.workspace')}</label>}
                <select className={inputCls} value={m.workspace} disabled={ro} data-testid="sync-mapping-workspace"
                  onChange={(e) => setRow(i, { workspace: e.target.value, ...(m.space === m.workspace || !m.space ? { space: e.target.value } : {}) })}>
                  {!workspaces.includes(m.workspace) && <option value={m.workspace}>{m.workspace}</option>}
                  {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
                </select>
              </div>
              <div>
                {i === 0 && <label className="block text-xs font-medium text-gray-500 mb-1">{t('hubSync.card.space')}</label>}
                <input className={inputCls} value={m.space} disabled={ro} data-testid="sync-mapping-space" onChange={(e) => setRow(i, { space: e.target.value })} />
              </div>
              <label className="flex items-center gap-1.5 text-sm text-gray-700 sm:h-[38px] whitespace-nowrap" title={t('hubSync.encryption.hint')}>
                <input type="checkbox" checked={encryptAll || !!m.encrypted} disabled={ro || encryptAll} data-testid="sync-mapping-encrypted"
                  onChange={(e) => toggleEncrypted(i, e.target.checked)} />
                {encryptAll || m.encrypted ? <Lock className="w-3.5 h-3.5 text-emerald-600" /> : null}
                {t('hubSync.card.encrypt')}
              </label>
              {!ro && (
                <button type="button" onClick={() => setMappings((rows) => rows.filter((_, j) => j !== i))} aria-label={t('hubSync.card.remove')}
                  className="p-2 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 justify-self-start"><Trash2 className="w-4 h-4" /></button>
              )}
            </div>
          ))}
          {!ro && (
            <button type="button" onClick={addRow} data-testid="sync-add-mapping" className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50">
              <Plus className="w-4 h-4" />{t('hubSync.card.add')}
            </button>
          )}
        </div>

        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('hubSync.encryption.title')}</div>
          <p className="text-[11px] text-gray-400">{t('hubSync.encryption.hint')}</p>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-0.5" checked={encryptAll} disabled={ro} data-testid="sync-encrypt-all"
              onChange={(e) => toggleEncryptAll(e.target.checked)} />
            <span>
              <span className="text-gray-800 font-medium">{t('hubSync.encryption.all')}</span>
              <span className="block text-[11px] text-gray-400">
                {encryptAll ? t('hubSync.encryption.allHint') : t('hubSync.encryption.perWorkspace')}
              </span>
            </span>
          </label>
          {encrypted && <p className="text-[11px] text-amber-700">{t('hubSync.encryption.warning')}</p>}
          {!status.key_set && !ro && (
            <button type="button" onClick={() => setKeyDialog(true)} data-testid="sync-set-key"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50">
              <KeyRound className="w-4 h-4" />{t('hubSync.encryption.setKey')}
            </button>
          )}
          {status.key_set && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600" data-testid="sync-key-state">
              <span>{t('hubSync.encryption.keyId')}: <span className="font-mono">{status.key_id}</span></span>
              <span className={`inline-flex items-center gap-1 ${status.key_unlocked ? 'text-emerald-700' : 'text-red-600'}`}>
                {status.key_unlocked ? <Unlock className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
                {status.key_unlocked ? t('hubSync.encryption.unlocked') : t('hubSync.encryption.locked')}
              </span>
              {!ro && <button type="button" onClick={forgetKey} className="text-red-600 hover:underline">{t('hubSync.encryption.forget')}</button>}
              {!ro && !status.key_unlocked && <button type="button" onClick={() => setKeyDialog(true)} className="text-indigo-600 hover:underline">{t('hubSync.encryption.setKey')}</button>}
            </div>
          )}
        </div>

        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={enabled} disabled={ro} onChange={(e) => setEnabled(e.target.checked)} data-testid="sync-enabled" />
          <span>
            <span className="text-gray-800 font-medium">{t('hubSync.card.enabled')}</span>
            <span className="block text-[11px] text-gray-400">{cannotEnable ? t('hubSync.card.needMapping') : t('hubSync.card.enabledHint')}</span>
          </span>
        </label>

        {error && <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>}
        {notice && <p className="flex items-center gap-1.5 text-sm text-green-700"><CheckCircle className="w-4 h-4 shrink-0" />{notice}</p>}

        {!ro && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={save} disabled={busy || cannotEnable} data-testid="sync-save"
              className="px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
              {t('hubSync.card.save')}
            </button>
            <button type="button" onClick={syncNow} disabled={running || !status.enabled} data-testid="sync-run"
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50">
              <RefreshCw className={`w-4 h-4 ${running ? 'animate-spin' : ''}`} />{running ? t('hubSync.run.running') : t('hubSync.run.now')}
            </button>
          </div>
        )}

        <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-sm space-y-1" data-testid="sync-status">
          <div className="text-gray-700">
            {t('hubSync.status.lastPass')}: {status.last_pass_at ? relativeTime(status.last_pass_at, language) : t('hubSync.status.never')}
          </div>
          {status.last_pass_at && (
            <div className="text-xs text-gray-500">{t('hubSync.status.counts', { pulled: lr.pulled ?? 0, applied: lr.applied ?? 0, pushed: lr.pushed ?? 0 })}</div>
          )}
          {status.last_error && <p className="text-xs text-amber-700">{t('hubSync.status.lastError')}: {status.last_error}</p>}
          {!status.last_error && status.has_error && <p className="text-xs text-amber-700" data-testid="sync-error-hidden">{t('hubSync.status.errorHidden')}</p>}
          {errors.length > 0 && (
            <ul className="text-xs text-amber-700 list-disc pl-4">{errors.slice(0, 5).map((m, i) => <li key={i}>{String(m)}</li>)}</ul>
          )}
        </div>

        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            {conflicts.length ? t('hubSync.conflicts.count', { count: conflicts.length }) : t('hubSync.conflicts.title')}
          </div>
          {conflicts.length === 0
            ? <p className="text-xs text-gray-400">{t('hubSync.conflicts.none')}</p>
            : <ul className="space-y-2">{conflicts.map((c) => <ConflictRow key={c.conflict_id} conflict={c} canEdit={!ro} onResolved={load} />)}</ul>}
        </div>
      </div>
      {keyDialog && <KeyDialog onClose={() => { setKeyDialog(false); setPendingRow(null); }} onDone={keyDone} />}
    </SectionCard>
  );
}

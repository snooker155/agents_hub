import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, CheckCircle, Loader, Lock } from 'lucide-react';
import { getHubSync, notifySyncChanged, setHubSyncWorkspace } from '../../api/hubSync';
import { KeyDialog } from '../settings/HubLinkSyncCard';
import { SectionCard, inputCls } from '../settingsUi';
import { MULTI, isAdmin, useAuth } from '../auth';
import { useI18n } from '../../i18n';

/**
 * This workspace's part of the two-way sync with the linked hub (hubsync/):
 * whether it syncs, into which space there, and whether its records are
 * sealed before they leave. The same row the Settings page lists under
 * Remote hub; saving here changes this row only.
 */
export default function WorkspaceHubSync({ workspace }) {
  const { t } = useI18n();
  const auth = useAuth();
  const canEdit = auth?.mode !== MULTI || isAdmin(auth);

  const [status, setStatus] = useState(null);
  const [synced, setSynced] = useState(false);
  const [space, setSpace] = useState(workspace);
  const [encrypted, setEncrypted] = useState(false);
  const [keyDialog, setKeyDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const apply = useCallback((data) => {
    setStatus(data);
    const row = (data?.mappings || []).find((m) => m.workspace === workspace);
    setSynced(!!row);
    setSpace(row?.space || workspace);
    setEncrypted(!!row?.encrypted || !!data?.encrypt_all);
  }, [workspace]);

  const load = useCallback(() => getHubSync()
    .then(({ data }) => { apply(data); setError(''); })
    .catch((e) => setError(e?.response?.data?.detail || e?.message || t('hubSync.card.loadFailed'))), [apply, t]);

  // Read once per workspace; `t` changes with the language only.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [workspace]);

  const toggleEncrypted = (on) => {
    if (on && !status?.key_set) { setKeyDialog(true); return; }
    setEncrypted(on);
  };

  const save = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const { data } = await setHubSyncWorkspace(workspace, {
        synced, space: space.trim() || workspace, encrypted: encrypted || !!status?.encrypt_all,
      });
      apply(data);
      setNotice(t('hubSync.card.saved'));
      notifySyncChanged();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setError((typeof detail === 'string' && detail) || detail?.message || e?.message || t('hubSync.card.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const settingsLink = (
    <Link to="/settings/hubLink" className="text-indigo-600 hover:underline">{t('hubSync.workspace.openSettings')}</Link>
  );

  if (!status) {
    return (
      <SectionCard title={t('hubSync.workspace.title')}>
        {error
          ? <p className="text-sm text-red-600">{error}</p>
          : <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}</p>}
      </SectionCard>
    );
  }

  if (!status.linked) {
    return (
      <SectionCard title={t('hubSync.workspace.title')}>
        <div className="space-y-3" data-testid="ws-sync-unlinked">
          <p className="text-sm text-gray-600">{t('hubSync.workspace.intro')}</p>
          <p className="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
            {t('hubSync.workspace.notLinked')} {settingsLink}
          </p>
        </div>
      </SectionCard>
    );
  }

  const ro = !canEdit;
  return (
    <SectionCard title={t('hubSync.workspace.title')}>
      <div className="space-y-4" data-testid="ws-sync">
        <p className="text-sm text-gray-600">{t('hubSync.workspace.intro')}</p>
        {ro && <p className="text-xs text-gray-500">{t('hubSync.card.readOnly')}</p>}

        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={synced} disabled={ro} data-testid="ws-sync-on"
            onChange={(e) => setSynced(e.target.checked)} />
          <span className="text-gray-800 font-medium">{t('hubSync.workspace.synced', { remote: status.remote_name || status.remote || '' })}</span>
        </label>

        {synced && (
          <>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">{t('hubSync.card.space')}</label>
              <input className={inputCls} value={space} disabled={ro} data-testid="ws-sync-space" onChange={(e) => setSpace(e.target.value)} />
            </div>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5" checked={encrypted || !!status.encrypt_all}
                disabled={ro || !!status.encrypt_all} data-testid="ws-sync-encrypted"
                onChange={(e) => toggleEncrypted(e.target.checked)} />
              <span>
                <span className="text-gray-800 font-medium inline-flex items-center gap-1.5">
                  {encrypted && <Lock className="w-3.5 h-3.5 text-emerald-600" />}{t('hubSync.encryption.title')}
                </span>
                <span className="block text-[11px] text-gray-400">
                  {status.encrypt_all ? <>{t('hubSync.workspace.allOn')} {settingsLink}</> : t('hubSync.encryption.hint')}
                </span>
              </span>
            </label>
            {encrypted && <p className="text-[11px] text-amber-700">{t('hubSync.encryption.warning')}</p>}
            {encrypted && status.key_set && !status.key_unlocked && (
              <p className="text-[11px] text-red-600">{t('hubSync.workspace.keyLocked')} {settingsLink}</p>
            )}
          </>
        )}

        {!status.enabled && <p className="text-xs text-gray-500">{t('hubSync.workspace.syncOff')} {settingsLink}</p>}

        {error && <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>}
        {notice && <p className="flex items-center gap-1.5 text-sm text-green-700"><CheckCircle className="w-4 h-4 shrink-0" />{notice}</p>}

        {!ro && (
          <button type="button" onClick={save} disabled={busy} data-testid="ws-sync-save"
            className="px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50">
            {t('hubSync.card.save')}
          </button>
        )}
      </div>
      {keyDialog && (
        <KeyDialog
          onClose={() => setKeyDialog(false)}
          onDone={async () => {
            setKeyDialog(false);
            notifySyncChanged();
            const { data } = await getHubSync();
            setStatus(data);
            setEncrypted(true);
          }}
        />
      )}
    </SectionCard>
  );
}

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, CheckCircle, Link2, RefreshCw, Trash2, Unplug } from 'lucide-react';
import { getHubLink, saveHubLink, syncHubLink, unlinkHub } from '../../api/hubLink';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import PageLoader from '../PageLoader';

const MIRROR_KINDS = ['runs', 'chats', 'memory'];

const fmt = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-0.5" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="text-gray-800">{label}</span>
        {hint && <span className="block text-[11px] text-gray-400">{hint}</span>}
      </span>
    </label>
  );
}

/**
 * Settings → System → Remote hub (docs/hub-link.md): attach this hub to
 * another one. Its models and agents become usable here, what happens here
 * is mirrored there, and this hub can serve it as a node.
 */
export default function HubLinkSection() {
  const { t } = useI18n();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [form, setForm] = useState({
    url: '', api_key: '', name: '', use_models: true, use_agents: true,
    mirror: { runs: true, chats: true, memory: false }, target_workspace: '',
    node: { enabled: false, token: '', share_models: true, sandbox: true },
  });

  const load = useCallback(async () => {
    try {
      const { data } = await getHubLink();
      setStatus(data);
      if (data.link) {
        const l = data.link;
        setForm((f) => ({
          ...f, url: l.url || '', api_key: '', name: l.name || '',
          use_models: !!l.use_models, use_agents: !!l.use_agents,
          mirror: { ...f.mirror, ...(l.mirror || {}) },
          target_workspace: l.target_workspace || '',
          node: { ...f.node, ...(l.node || {}), token: '' },
        }));
      }
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || '');
    } finally {
      setLoading(false);
    }
  }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader is shared with the actions; it sets state after its await
  useEffect(() => { load(); }, [load]);

  const setF = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setMirror = (k, v) => setForm((f) => ({ ...f, mirror: { ...f.mirror, [k]: v } }));
  const setNode = (k, v) => setForm((f) => ({ ...f, node: { ...f.node, [k]: v } }));

  const save = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const payload = { ...form, node: { ...form.node } };
      if (!payload.api_key) delete payload.api_key;
      if (!payload.node.token) delete payload.node.token;
      const { data } = await saveHubLink(payload);
      setStatus(data);
      setNotice(t('settings.hubLink.saved'));
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('settings.hubLink.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const sync = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const { data } = await syncHubLink();
      setNotice(t('settings.hubLink.synced', { sent: data?.drained?.sent ?? 0, failed: data?.drained?.failed ?? 0 }));
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || '');
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    if (!window.confirm(t('settings.hubLink.confirmUnlink'))) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await unlinkHub();
      setForm((f) => ({ ...f, url: '', api_key: '', node: { ...f.node, enabled: false, token: '' } }));
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || '');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <PageLoader size="sm" />;
  const link = status?.link;
  const linked = !!status?.linked;
  const outbox = status?.outbox || {};
  const nodeState = status?.runtime?.node || {};

  return (
    <>
      <SectionCard
        title={t('settings.hubLink.title')}
        actions={linked ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-50 text-green-700"><CheckCircle className="w-3 h-3" />{t('settings.hubLink.linked')}</span>
        ) : (
          <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">{t('settings.hubLink.notLinked')}</span>
        )}
      >
        <p className="text-sm text-gray-600">{t('settings.hubLink.intro')}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('settings.hubLink.url')}</label>
            <input className={inputCls} value={form.url} onChange={(e) => setF('url', e.target.value)} placeholder="https://hub.example.com" data-testid="hublink-url" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('settings.hubLink.apiKey')}</label>
            <input className={inputCls} type="password" value={form.api_key} onChange={(e) => setF('api_key', e.target.value)}
              placeholder={link?.api_key_set ? link.api_key : 'ahk_…'} data-testid="hublink-key" />
            <p className="text-[11px] text-gray-400 mt-1">{t('settings.hubLink.apiKeyHint')}</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('settings.hubLink.name')}</label>
            <input className={inputCls} value={form.name} onChange={(e) => setF('name', e.target.value)} placeholder={t('settings.hubLink.namePlaceholder')} />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('settings.hubLink.targetWorkspace')}</label>
            <input className={inputCls} value={form.target_workspace} onChange={(e) => setF('target_workspace', e.target.value)} placeholder={t('settings.hubLink.targetWorkspacePlaceholder')} />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.hubLink.useTitle')}</div>
            <Toggle checked={form.use_models} onChange={(v) => setF('use_models', v)} label={t('settings.hubLink.useModels')} hint={t('settings.hubLink.useModelsHint')} />
            <Toggle checked={form.use_agents} onChange={(v) => setF('use_agents', v)} label={t('settings.hubLink.useAgents')} hint={t('settings.hubLink.useAgentsHint')} />
          </div>
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.hubLink.mirrorTitle')}</div>
            {MIRROR_KINDS.map((k) => (
              <Toggle key={k} checked={form.mirror[k]} onChange={(v) => setMirror(k, v)} label={t(`settings.hubLink.mirror.${k}`)} hint={t(`settings.hubLink.mirror.${k}Hint`)} />
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('settings.hubLink.nodeTitle')}</div>
          <p className="text-[11px] text-gray-400">{t('settings.hubLink.nodeIntro')}</p>
          <Toggle checked={form.node.enabled} onChange={(v) => setNode('enabled', v)} label={t('settings.hubLink.nodeEnabled')} />
          {form.node.enabled && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pl-6">
              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-gray-500 mb-1">{t('settings.hubLink.nodeToken')}</label>
                <input className={inputCls} type="password" value={form.node.token} onChange={(e) => setNode('token', e.target.value)}
                  placeholder={link?.node?.token_set ? link.node.token : 'ahnode_…'} data-testid="hublink-node-token" />
                <p className="text-[11px] text-gray-400 mt-1">{t('settings.hubLink.nodeTokenHint')}</p>
              </div>
              <Toggle checked={form.node.share_models} onChange={(v) => setNode('share_models', v)} label={t('settings.hubLink.nodeShareModels')} hint={t('settings.hubLink.nodeShareModelsHint')} />
              <Toggle checked={form.node.sandbox} onChange={(v) => setNode('sandbox', v)} label={t('settings.hubLink.nodeSandbox')} hint={t('settings.hubLink.nodeSandboxHint')} />
            </div>
          )}
        </div>

        {error && <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4" />{error}</p>}
        {notice && <p className="flex items-center gap-1.5 text-sm text-green-700"><CheckCircle className="w-4 h-4" />{notice}</p>}

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={save} disabled={busy || !form.url.trim()}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50" data-testid="hublink-save">
            {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />}
            {linked ? t('settings.hubLink.update') : t('settings.hubLink.link')}
          </button>
          {linked && (
            <>
              <button type="button" onClick={sync} disabled={busy} className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                <RefreshCw className="w-4 h-4" />{t('settings.hubLink.sync')}
              </button>
              <button type="button" onClick={unlink} disabled={busy} className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50">
                <Unplug className="w-4 h-4" />{t('settings.hubLink.unlink')}
              </button>
            </>
          )}
        </div>
      </SectionCard>

      {linked && (
        <SectionCard title={t('settings.hubLink.statusTitle')}>
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('settings.hubLink.status.outbox')}</dt>
              <dd className="text-lg font-semibold text-gray-800">{outbox.pending ?? 0}</dd>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('settings.hubLink.status.lastSync')}</dt>
              <dd className="text-sm font-semibold text-gray-800">{fmt(link?.last_sync_at)}</dd>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('settings.hubLink.status.node')}</dt>
              <dd className="text-sm font-semibold text-gray-800">
                {!link?.node?.enabled ? t('settings.hubLink.status.nodeOff')
                  : nodeState.connected ? t('settings.hubLink.status.nodeConnected', { n: nodeState.answered || 0 })
                    : t('settings.hubLink.status.nodeDisconnected')}
              </dd>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('settings.hubLink.status.target')}</dt>
              <dd className="text-sm font-semibold text-gray-800 truncate">{link?.target_workspace || 'default'}</dd>
            </div>
          </dl>
          {(link?.last_error || outbox.last_error || nodeState.last_error) && (
            <p className="text-xs text-amber-700 flex items-center gap-1"><AlertCircle className="w-3.5 h-3.5" />{link?.last_error || outbox.last_error || nodeState.last_error}</p>
          )}
          <p className="text-xs text-gray-500">
            {t('settings.hubLink.status.where')}{' '}
            <Link to="/models" className="text-indigo-600 hover:underline">hub-remote</Link>{' · '}
            <Link to="/mcp" className="text-indigo-600 hover:underline">{t('settings.hubLink.status.mcp')}</Link>{' · '}
            <Link to="/nodes" className="text-indigo-600 hover:underline">{t('settings.hubLink.status.nodes')}</Link>
          </p>
        </SectionCard>
      )}
    </>
  );
}

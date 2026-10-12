import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Cpu, KeyRound, Laptop, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { createNode, deleteNode, listNodes, rotateNodeToken } from '../api/hubLink';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { CopyButton } from '../components/ConnectionSetup';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

const inputCls = 'w-full border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none';
const smallBtn = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed';

const fmt = (iso) => {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
};

/**
 * The nodes attached to this hub (docs/hub-link.md, "Nodes"): other hubs,
 * usually one person's machine, that dial in and offer their local models
 * and their sandbox. A node is made here and gets a token, shown once; the
 * other machine pastes it into its own Settings → Remote hub.
 */
export default function Nodes() {
  const { t } = useI18n();
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [workspaces, setWorkspaces] = useState('');
  const [saving, setSaving] = useState(false);
  const [issued, setIssued] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await listNodes();
      setNodes(data.nodes || []);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || t('nodes.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader is shared with the actions; it sets state after its await
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const add = async () => {
    setSaving(true);
    setError('');
    try {
      const list = workspaces.split(',').map((w) => w.trim()).filter(Boolean);
      const { data } = await createNode({ name: name.trim(), workspaces: list.length ? list : null });
      setIssued(data);
      setAdding(false);
      setName('');
      setWorkspaces('');
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || t('nodes.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (n) => {
    if (!window.confirm(t('nodes.confirmDelete', { name: n.name }))) return;
    try { await deleteNode(n.id); await load(); }
    catch (e) { setError(e?.response?.data?.detail || t('nodes.actionFailed')); }
  };

  const rotate = async (n) => {
    if (!window.confirm(t('nodes.confirmRotate', { name: n.name }))) return;
    try {
      const { data } = await rotateNodeToken(n.id);
      setIssued(data);
    } catch (e) { setError(e?.response?.data?.detail || t('nodes.actionFailed')); }
  };

  if (loading) return <PageLoader />;

  return (
    <PageContainer>
      <PageHeader
        icon={Laptop}
        title={t('nodes.title')}
        description={t('nodes.description')}
        actions={(
          <button onClick={() => { setAdding(true); setIssued(null); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold bg-indigo-600 text-white hover:bg-indigo-700">
            <Plus className="w-4 h-4" />{t('nodes.add')}
          </button>
        )}
      />

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 shrink-0" />{error}
        </div>
      )}

      {issued && (
        <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4" data-testid="node-token">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-indigo-900">{t('nodes.tokenTitle', { name: issued.node?.name })}</div>
              <p className="text-xs text-indigo-800 mt-0.5">{t('nodes.tokenOnce')}</p>
            </div>
            <button onClick={() => setIssued(null)} className="text-indigo-400 hover:text-indigo-700" aria-label={t('nodes.close')}><X className="w-4 h-4" /></button>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <code className="flex-1 text-xs bg-white border border-indigo-200 rounded-lg px-3 py-2 break-all">{issued.token}</code>
            <CopyButton value={issued.token} />
          </div>
          <p className="mt-3 text-xs text-indigo-900">{t('nodes.tokenHint')}</p>
          <pre className="mt-1 text-[11px] bg-white border border-indigo-200 rounded-lg px-3 py-2 overflow-x-auto">{`ah hub link ${window.location.origin} --key <personal key> --node-token ${issued.token}`}</pre>
        </div>
      )}

      {adding && (
        <div className="mb-4 rounded-xl border border-gray-200 bg-white p-4 space-y-3">
          <div className="text-sm font-semibold">{t('nodes.form.title')}</div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('nodes.form.name')}</label>
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('nodes.form.namePlaceholder')} />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">{t('nodes.form.workspaces')}</label>
            <input className={inputCls} value={workspaces} onChange={(e) => setWorkspaces(e.target.value)} placeholder="team-a, team-b" />
            <p className="text-[11px] text-gray-400 mt-1">{t('nodes.form.workspacesHint')}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={add} disabled={saving || !name.trim()} className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">{t('nodes.form.create')}</button>
            <button onClick={() => setAdding(false)} className="px-3 py-1.5 rounded-lg text-sm text-gray-600 hover:bg-gray-100">{t('nodes.form.cancel')}</button>
          </div>
        </div>
      )}

      {nodes.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-500">
          {t('nodes.empty')}
        </div>
      ) : (
        <div className="space-y-3">
          {nodes.map((n) => (
            <div key={n.id} className="rounded-xl border border-gray-200 bg-white p-4" data-testid="node-row">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{n.name}</span>
                    {n.online ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />{t('nodes.state.online')}</span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">{t('nodes.state.offline')}</span>
                    )}
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    <code>{n.id}</code>
                    {n.host && <> · {n.host}</>}
                    {n.version && <> · v{n.version}</>}
                    {' · '}{t('nodes.lastSeen', { when: fmt(n.last_seen) })}
                  </div>
                </div>
                <div className="flex gap-1.5">
                  <button onClick={() => rotate(n)} className={smallBtn} title={t('nodes.actions.rotate')}><KeyRound className="w-3.5 h-3.5" />{t('nodes.actions.rotate')}</button>
                  <button onClick={() => remove(n)} className={`${smallBtn} text-red-600`} title={t('nodes.actions.delete')}><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>
              <dl className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <dt className="text-[11px] uppercase tracking-wide text-gray-400 flex items-center gap-1"><Cpu className="w-3 h-3" />{t('nodes.models')}</dt>
                  <dd className="mt-1 text-gray-800">
                    {(n.models || []).length === 0 ? t('nodes.noModels') : (
                      <>
                        {t('nodes.modelCount', { count: n.models.length })}
                        {n.backend_id && <> · <Link to="/models" className="text-indigo-600 hover:underline">{n.backend_id}</Link></>}
                        <div className="mt-1 text-gray-500 truncate">{n.models.map((m) => m.id).join(', ')}</div>
                      </>
                    )}
                  </dd>
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('nodes.sandbox')}</dt>
                  <dd className="mt-1 text-gray-800">
                    {n.capabilities?.sandbox ? (
                      <>
                        {t('nodes.sandboxOn', { provider: n.capabilities.sandbox_provider || '?' })}
                        <div className="mt-1 text-gray-500">{t('nodes.sandboxHint')} <code>{n.sandbox_provider}</code> · <Link to="/environments" className="text-indigo-600 hover:underline">{t('nodes.environments')}</Link></div>
                      </>
                    ) : t('nodes.sandboxOff')}
                  </dd>
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <dt className="text-[11px] uppercase tracking-wide text-gray-400">{t('nodes.workspaces')}</dt>
                  <dd className="mt-1 text-gray-800">{(n.workspaces || []).length ? n.workspaces.join(', ') : t('nodes.anyWorkspace')}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      )}
      <p className="mt-4 text-xs text-gray-400 flex items-center gap-1"><RefreshCw className="w-3 h-3" />{t('nodes.refreshHint')}</p>
    </PageContainer>
  );
}

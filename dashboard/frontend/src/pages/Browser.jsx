import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Globe, Plus, Bot, User, Send, Loader2, RefreshCw } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { useToast, errorDetail } from '../components/toast';
import { useI18n } from '../i18n';
import { getAgents, getWorkspaces } from '../api';
import {
  getBrowserStatus, listBrowserSessions, createBrowserSession, closeBrowserSession,
  handoffBrowserSession, setBrowserControl,
} from '../api/browser';
import { BrowserToolbar, BrowserViewport, useBrowserSession } from '../components/browser';

// The session list is a directory, not a live view: a slow refresh is enough
// to pick up an agent opening a page or a session expiring.
const LIST_REFRESH_MS = 5000;

function OwnerBadge({ owner }) {
  const { t } = useI18n();
  const agent = owner === 'agent';
  const Icon = agent ? Bot : User;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${
      agent ? 'border-indigo-200 bg-indigo-50 text-indigo-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'
    }`}>
      <Icon className="h-3 w-3" />
      {agent ? t('browser.ownerAgent') : t('browser.ownerUser')}
    </span>
  );
}

function HandoffDialog({ session, workspace, onClose, onDone }) {
  const { t } = useI18n();
  const toast = useToast();
  const [agents, setAgents] = useState([]);
  const [agentId, setAgentId] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getAgents(workspace)
      .then(({ data }) => {
        const list = Array.isArray(data) ? data : (data?.items || []);
        setAgents(list);
        if (list.length) setAgentId((cur) => cur || list[0].id);
      })
      .catch(() => setAgents([]));
  }, [workspace]);

  const submit = async (e) => {
    e.preventDefault();
    if (!agentId) return;
    setBusy(true);
    try {
      const { data } = await handoffBrowserSession(session.session_id, { agentId, message, workspace });
      toast.success(t('browser.handoffDone', { agent: agentId }));
      onDone(data);
    } catch (err) {
      toast.error(t('browser.handoffFailed'), errorDetail(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true"
      aria-label={t('browser.handoffTitle')}>
      <form onSubmit={submit} className="w-full max-w-lg space-y-3 rounded-xl bg-white p-5 shadow-xl">
        <h2 className="text-lg font-semibold text-gray-900">{t('browser.handoffTitle')}</h2>
        <p className="text-sm text-gray-500">{t('browser.handoffHint')}</p>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">{t('browser.agent')}</span>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}
            className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm">
            {agents.length === 0 && <option value="">{t('browser.noAgents')}</option>}
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-gray-700">{t('browser.message')}</span>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={4}
            placeholder={t('browser.messagePlaceholder')}
            className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm" />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="rounded-md border border-gray-200 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
            {t('browser.cancel')}
          </button>
          <button type="submit" disabled={busy || !agentId}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {t('browser.handoffSubmit')}
          </button>
        </div>
      </form>
    </div>
  );
}

function OpenSession({ session, workspace, onClosed, onHandedOff }) {
  const { t } = useI18n();
  const toast = useToast();
  const isUser = session.owner === 'user';
  const [controlling, setControlling] = useState(isUser);
  const [handoff, setHandoff] = useState(false);
  const { frame, loading, send, navigate, inputError, status } = useBrowserSession(session.session_id);
  const control = isUser || controlling;
  // An agent's session: the hold is registered with the service so the
  // agent waits while the person drives, and dropped when they leave.
  const toggleControl = () => {
    const next = !controlling;
    setControlling(next);
    setBrowserControl(session.session_id, next).catch(() => {});
  };
  useEffect(() => () => {
    if (!isUser && controlling) setBrowserControl(session.session_id, false).catch(() => {});
  }, [isUser, controlling, session.session_id]);

  const close = async () => {
    try {
      await closeBrowserSession(session.session_id);
    } catch (err) {
      toast.error(t('browser.closeFailed'), errorDetail(err));
    }
    onClosed();
  };

  if (status === 404) {
    return <p className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">{t('browser.sessionClosed')}</p>;
  }

  return (
    <div className="space-y-2">
      <BrowserToolbar
        url={frame?.url || session.url || ''}
        title={frame?.title || session.title || ''}
        sessionId={session.session_id}
        onBack={control ? () => send({ kind: 'back' }) : undefined}
        onForward={control ? () => send({ kind: 'forward' }) : undefined}
        onReload={control ? () => send({ kind: 'reload' }) : undefined}
        onNavigate={control ? navigate : undefined}
        controlling={controlling}
        onToggleControl={isUser ? undefined : toggleControl}
        onClose={close}
      >
        <button type="button" onClick={() => setHandoff(true)}
          className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700">
          <Bot className="h-3.5 w-3.5" />
          {t('browser.handToAgent')}
        </button>
      </BrowserToolbar>
      <BrowserViewport frame={frame} loading={loading} controllable={control} onInput={send} />
      {inputError && <p className="text-xs text-red-600">{inputError}</p>}
      <p className="text-xs text-gray-400">
        {control ? t('browser.controlHint') : t('browser.watchHint')}
        {!isUser && controlling ? ` ${t('browser.agentWaits')}` : ''}
      </p>
      {handoff && (
        <HandoffDialog
          session={session}
          workspace={workspace}
          onClose={() => setHandoff(false)}
          onDone={(data) => { setHandoff(false); setControlling(false); onHandedOff(data); }}
        />
      )}
    </div>
  );
}

export default function Browser() {
  const { t } = useI18n();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace() || {};
  // ?url=...&workspace=...: a page to open at once (the project page's "open
  // in the agent's browser" link for a deployed app, docs/project-deployments.md).
  const [searchParams, setSearchParams] = useSearchParams();
  const autoOpened = useRef(false);
  const [workspace, setWorkspace] = useState(searchParams.get('workspace') || selectedWorkspace || 'default');
  const [workspaces, setWorkspaces] = useState([]);
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [handedOff, setHandedOff] = useState(null);

  useEffect(() => {
    getBrowserStatus().then(({ data }) => setStatus(data)).catch(() => setStatus({ configured: false }));
    getWorkspaces().then(({ data }) => setWorkspaces(Array.isArray(data) ? data : [])).catch(() => {});
  }, []);

  // A promise chain, not an async function: the React Compiler lint treats an
  // async function called from an effect as a synchronous setState.
  const refresh = useCallback(() => listBrowserSessions(workspace)
    .then(({ data }) => setSessions(data?.sessions || []))
    .catch(() => setSessions([])), [workspace]);

  useEffect(() => {
    if (!status?.configured) return undefined;
    refresh();
    const timer = setInterval(refresh, LIST_REFRESH_MS);
    return () => clearInterval(timer);
  }, [status, refresh]);

  const open = useMemo(() => sessions.find((s) => s.session_id === openId) || null, [sessions, openId]);

  const create = async (url = '') => {
    setCreating(true);
    try {
      const { data } = await createBrowserSession(workspace, url);
      setOpenId(data.session_id);
      setSessions((list) => [{ ...data, owner: 'user', workspace, label: '' },
        ...list.filter((s) => s.session_id !== data.session_id)]);
      refresh();
    } catch (err) {
      toast.error(t('browser.createFailed'), errorDetail(err));
    } finally {
      setCreating(false);
    }
  };

  useEffect(() => {
    const url = searchParams.get('url');
    if (!url || autoOpened.current || !status?.configured) return;
    autoOpened.current = true;
    create(url);
    setSearchParams({}, { replace: true });
  }, [status, searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  const workspaceNames = workspaces.map((w) => w.name).filter(Boolean);
  if (!workspaceNames.includes(workspace)) workspaceNames.unshift(workspace);

  const actions = status?.configured ? (
    <>
      <select value={workspace} onChange={(e) => { setWorkspace(e.target.value); setOpenId(null); }}
        aria-label={t('browser.workspace')}
        className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-sm text-gray-700">
        {workspaceNames.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
      <button type="button" onClick={() => create('')} disabled={creating}
        className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
        {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
        {t('browser.newSession')}
      </button>
    </>
  ) : null;

  return (
    <PageContainer>
      <PageHeader icon={Globe} title={t('browser.title')} description={t('browser.description')} actions={actions} />

      {status && !status.configured && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          {t('browser.notConfigured')}{' '}
          <Link to="/settings/browser" className="font-medium text-indigo-600 underline">{t('browser.openSettings')}</Link>
          {' · '}
          <Link to="/docs/browser" className="font-medium text-indigo-600 underline">{t('browser.readDocs')}</Link>
        </div>
      )}

      {status?.configured && (
        <div className="flex flex-col gap-4 lg:flex-row">
          <aside className="space-y-2 lg:w-72 lg:shrink-0">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-700">{t('browser.sessions')}</h2>
              <button type="button" onClick={refresh} className="text-gray-400 hover:text-gray-700"
                title={t('browser.refresh')} aria-label={t('browser.refresh')}>
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            </div>
            {sessions.length === 0 && <p className="text-xs text-gray-400">{t('browser.noSessions')}</p>}
            <ul className="space-y-1.5">
              {sessions.map((s) => (
                <li key={s.session_id}>
                  <button type="button" onClick={() => setOpenId(s.session_id)}
                    className={`w-full rounded-lg border px-3 py-2 text-left ${
                      s.session_id === openId ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-white hover:border-indigo-200'
                    }`}>
                    <div className="flex items-center gap-2">
                      <OwnerBadge owner={s.owner} />
                      {s.label && <span className="truncate text-xs text-gray-600">{s.label}</span>}
                    </div>
                    <div className="mt-1 truncate text-sm text-gray-800">{s.title || s.url || t('browser.blankPage')}</div>
                    <div className="truncate font-mono text-[10px] text-gray-400">{s.url}</div>
                    {s.run_id && (
                      <div className="font-mono text-[10px] text-gray-400">{t('browser.run', { id: String(s.run_id).slice(0, 8) })}</div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <section className="min-w-0 flex-1 space-y-3">
            {handedOff?.task_id && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                {t('browser.handedOff')}{' '}
                <Link to={`/tasks/${handedOff.task_id}`} className="font-medium text-indigo-600 underline">
                  {t('browser.openTask')}
                </Link>
              </div>
            )}
            {open ? (
              <OpenSession
                key={open.session_id}
                session={open}
                workspace={workspace}
                onClosed={() => { setOpenId(null); refresh(); }}
                onHandedOff={(data) => { setHandedOff(data); refresh(); }}
              />
            ) : (
              <div className="space-y-2">
                <BrowserToolbar onNavigate={(url) => create(url)} disabled={creating} />
                <BrowserViewport frame={null} emptyText={t('browser.startHint')} />
              </div>
            )}
          </section>
        </div>
      )}
    </PageContainer>
  );
}

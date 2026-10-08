/**
 * Distribution page: the hub's agents where people already work
 * (dashboard/backend/routes/distribution.py).
 *
 * Four cards: MCP for any client (Claude Code, Cursor, VS Code...), the Obsidian plugin, the Slack
 * app and the Teams app. The two chat apps share one installs list, where an
 * organisation that put the bot in its own workspace waits for approval and
 * is then given an agent.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Check, Copy, Download, ExternalLink, Plus, RefreshCw, Send } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import PageLoader from '../components/PageLoader';
import { SectionCard, inputCls } from '../components/settingsUi';
import BalancedColumns from '../components/dashboard/BalancedColumns';
import InstallsTable from '../components/distribution/InstallsTable';
import { useAgentsOf } from '../components/distribution/useAgentsOf';
import { MCP_CLIENTS, mcpInstallLink, mcpSnippet } from '../components/distribution/mcpClients';
import { errorDetail, useToast } from '../components/toast';
import { useI18n } from '../i18n';
import { getWorkspaces } from '../api';
import {
  addInstall, createSlackInstallLink, downloadObsidianPlugin, downloadTeamsPackage, getDistribution,
  getSlackManifest, saveBlob,
} from '../api/distribution';

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnSecondary = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';
const notice = 'rounded-lg px-3 py-2 text-sm border';
const LABEL = 'block text-xs font-medium text-gray-600 mb-1';
// BalancedColumns measures a card from its children and stretches the last one in a column.
const CARD = 'relative flex-1';

// A failed download arrives as a Blob, so the server's detail has to be read out of it.
async function failure(err) {
  const data = err?.response?.data;
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const detail = JSON.parse(await data.text())?.detail;
      if (typeof detail === 'string' && detail) return detail;
    } catch { /* not JSON: fall through */ }
  }
  return errorDetail(err) || '';
}

function CopyButton({ text, label }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => {});
  };
  return (
    <button type="button" onClick={copy} className={btnSecondary} aria-label={label || t('distribution.copy')}>
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? t('distribution.copied') : t('distribution.copy')}
    </button>
  );
}

function CopyRow({ label, value, testId }) {
  return (
    <div>
      <span className={LABEL}>{label}</span>
      <div className="flex flex-wrap items-center gap-2">
        <code data-testid={testId} className="flex-1 min-w-0 break-all text-xs bg-gray-100 text-gray-800 rounded px-2 py-1.5">{value}</code>
        <CopyButton text={value} label={`${label}: copy`} />
      </div>
    </div>
  );
}

function Snippet({ label, text, testId, children }) {
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        <span className="text-xs font-medium text-gray-600">{label}</span>
        <div className="flex gap-2">{children}<CopyButton text={text} label={`${label}: copy`} /></div>
      </div>
      <pre data-testid={testId} className="text-xs bg-gray-100 text-gray-800 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">{text}</pre>
    </div>
  );
}

const MCP_TOOL_GROUPS = [
  ['agents', ['list_workspaces', 'list_agents', 'ask_agent']],
  ['files', ['list_files', 'read_file', 'upload_file']],
  ['knowledge', ['list_knowledge', 'search_knowledge']],
  ['workflows', ['list_workflows', 'run_workflow']],
  ['runs', ['list_runs', 'get_run', 'stop_run']],
];

function McpCard({ info, workspaces }) {
  const { t } = useI18n();
  const [key, setKey] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [client, setClient] = useState(MCP_CLIENTS[0]);
  const mode = info.auth_mode;
  const url = info.mcp?.url || '';
  const headerName = info.mcp?.workspace_header || 'X-Agents-Hub-Workspace';
  const serverName = info.mcp?.server_name || 'agents-hub';

  const headers = useMemo(() => {
    const h = {};
    if (mode !== 'single') h.Authorization = `Bearer ${key.trim() || 'YOUR_API_KEY'}`;
    if (workspace) h[headerName] = workspace;
    return h;
  }, [mode, key, workspace, headerName]);

  const target = { url, headers, serverName };
  const snippet = mcpSnippet(client, target);
  const installLink = mcpInstallLink(client, target);

  return (
    <SectionCard className={CARD} title={t('distribution.mcp.title')}>
      <p className="text-sm text-gray-600">{t('distribution.mcp.intro')}</p>
      <CopyRow label={t('distribution.mcp.url')} value={url} testId="mcp-url" />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="dist-key" className={LABEL}>{t('distribution.mcp.apiKey')}</label>
          <input id="dist-key" type="text" autoComplete="off" className={inputCls} value={key}
            disabled={mode === 'single'} placeholder={t('distribution.mcp.apiKeyPlaceholder')}
            onChange={(e) => setKey(e.target.value)} />
          <p className="text-xs text-gray-500 mt-1">{t('distribution.mcp.apiKeyHint')}</p>
          <p className="text-xs text-gray-500 mt-1">
            {mode === 'single' && t('distribution.mcp.authSingle')}
            {mode === 'token' && t('distribution.mcp.authToken')}
            {mode === 'multi' && (
              <>{t('distribution.mcp.authMulti')}{' '}
                <Link to="/account" className="text-indigo-600 hover:underline">{t('distribution.mcp.createKey')}</Link></>
            )}
          </p>
        </div>
        <div>
          <label htmlFor="dist-mcp-ws" className={LABEL}>{t('distribution.mcp.workspace')}</label>
          <select id="dist-mcp-ws" className={inputCls} value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
            <option value="">{t('distribution.mcp.workspaceNone')}</option>
            {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
        </div>
      </div>
      <div>
        <span className={LABEL}>{t('distribution.mcp.client')}</span>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('distribution.mcp.client')}>
          {MCP_CLIENTS.map((c) => (
            <button key={c} type="button" aria-pressed={client === c} onClick={() => setClient(c)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium border ${client === c
                ? 'bg-indigo-600 border-indigo-600 text-white'
                : 'border-gray-300 text-gray-700 hover:bg-gray-50'}`}>
              {t(`distribution.mcp.clients.${c}.name`)}
            </button>
          ))}
        </div>
      </div>
      <Snippet label={t(`distribution.mcp.clients.${client}.where`)} text={snippet} testId="mcp-snippet">
        {installLink && (
          <a href={installLink} className={btnSecondary}>
            <ExternalLink className="w-3.5 h-3.5" />
            {t('distribution.mcp.addTo', { client: t(`distribution.mcp.clients.${client}.name`) })}
          </a>
        )}
      </Snippet>
      <p className="text-xs text-gray-500">
        {t('distribution.mcp.cliHint')}{' '}
        <code className="bg-gray-100 rounded px-1 py-0.5">ah mcp connect {client} --write</code>
      </p>
      <div>
        <span className={LABEL}>{t('distribution.mcp.tools')}</span>
        <dl className="space-y-2">
          {MCP_TOOL_GROUPS.map(([group, names]) => (
            <div key={group}>
              <dt className="text-sm font-medium text-gray-800">{t(`distribution.mcp.toolGroups.${group}.title`)}</dt>
              <dd className="text-sm text-gray-600">
                {t(`distribution.mcp.toolGroups.${group}.text`)}{' '}
                <span className="inline-flex flex-wrap gap-1 align-middle">
                  {names.map((n) => <code key={n} className="text-xs bg-gray-100 text-gray-700 rounded px-1 py-0.5">{n}</code>)}
                </span>
              </dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-gray-500 mt-2">{t('distribution.mcp.toolsNote')}</p>
      </div>
    </SectionCard>
  );
}

function ObsidianCard({ info, onError }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const ob = info.obsidian || {};
  const download = async () => {
    setBusy(true);
    try {
      const { data } = await downloadObsidianPlugin();
      saveBlob(data, 'agents-hub-obsidian.zip');
    } catch (err) {
      onError(await failure(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <SectionCard className={CARD} title={t('distribution.obsidian.title')}>
      {!ob.available ? (
        <p className="text-sm text-gray-500">{t('distribution.obsidian.unavailable')}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={download} disabled={busy} className={btnPrimary}>
              <Download className="w-3.5 h-3.5" /> {t('distribution.obsidian.download')}
            </button>
            {ob.version && <span className="text-xs text-gray-500">{t('distribution.obsidian.version', { version: ob.version })}</span>}
          </div>
          <ol className="text-sm text-gray-700 space-y-1 list-decimal pl-5">
            <li>{t('distribution.obsidian.step1')}</li>
            <li>{t('distribution.obsidian.step2')}</li>
            <li>{t('distribution.obsidian.step3')}</li>
          </ol>
          <CopyRow label={t('distribution.obsidian.hubUrl')} value={info.public_url || ''} testId="obsidian-url" />
        </>
      )}
    </SectionCard>
  );
}

function Status({ children, tone }) {
  const tones = { good: 'bg-emerald-50 text-emerald-700', warn: 'bg-amber-50 text-amber-700', off: 'bg-gray-100 text-gray-500' };
  return <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${tones[tone]}`}>{children}</span>;
}

function SlackCard({ info, workspaces, reload, onError }) {
  const { t } = useI18n();
  const slack = info.slack || {};
  const own = slack.workspace && slack.workspace !== 'default' ? slack.workspace : '';
  const [workspace, setWorkspace] = useState(own || '');
  const [agentId, setAgentId] = useState('');
  const [busy, setBusy] = useState(false);
  const [createUrl, setCreateUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const agents = useAgentsOf(own || workspace);
  const ready = slack.available && slack.mode === 'events' && slack.oauth_ready;

  useEffect(() => {
    if (!slack.available) return;
    getSlackManifest().then(({ data }) => setCreateUrl(data?.create_app_url || '')).catch(() => {});
  }, [slack.available]);

  const copyManifest = async () => {
    try {
      const { data } = await getSlackManifest();
      await navigator.clipboard?.writeText(JSON.stringify(data.manifest, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      onError(errorDetail(err));
    }
  };

  const add = async () => {
    setBusy(true);
    try {
      const { data } = await createSlackInstallLink({ workspace: own || workspace, agent_id: agentId });
      window.location.assign(data.url);
    } catch (err) {
      onError(errorDetail(err));
      setBusy(false);
    }
  };

  if (!slack.available) {
    return <SectionCard className={CARD} title={t('distribution.slack.title')}><p className="text-sm text-gray-500">{t('distribution.notAvailable')}</p></SectionCard>;
  }
  return (
    <SectionCard className={CARD} title={t('distribution.slack.title')}>
      <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <Status tone={slack.configured ? 'good' : 'warn'}>
          {slack.configured ? t('distribution.slack.configured') : t('distribution.slack.notConfigured')}
        </Status>
        <Status tone={slack.enabled ? 'good' : 'off'}>
          {slack.enabled ? t('distribution.slack.enabled') : t('distribution.slack.disabled')}
        </Status>
        <span className="text-xs text-gray-500">{t('distribution.slack.mode', { mode: slack.mode })}</span>
      </div>
      {!ready && (
        <div className={`${notice} bg-amber-50 border-amber-200 text-amber-800`}>
          {t('distribution.slack.needsSetup')}{' '}
          <Link to="/connectors?tab=slack" className="underline">{t('distribution.slack.openConnectors')}</Link>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <a href={createUrl || undefined} target="_blank" rel="noreferrer" className={btnSecondary}>
          <ExternalLink className="w-3.5 h-3.5" /> {t('distribution.slack.createApp')}
        </a>
        <button type="button" onClick={copyManifest} className={btnSecondary}>
          <Copy className="w-3.5 h-3.5" /> {copied ? t('distribution.slack.manifestCopied') : t('distribution.slack.copyManifest')}
        </button>
      </div>
      <div className="space-y-2 border-t border-gray-100 pt-3">
        <h3 className="text-sm font-semibold text-gray-800">{t('distribution.slack.addTitle')}</h3>
        <div className="grid gap-2 sm:grid-cols-2">
          {!own && (
            <select className={inputCls} value={workspace} aria-label={t('distribution.slack.workspace')}
              onChange={(e) => { setWorkspace(e.target.value); setAgentId(''); }}>
              <option value="">{t('distribution.slack.workspace')}</option>
              {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
            </select>
          )}
          <select className={inputCls} value={agentId} aria-label={t('distribution.slack.agent')}
            onChange={(e) => setAgentId(e.target.value)}>
            <option value="">{t('distribution.slack.agent')}</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
          </select>
        </div>
        <button type="button" onClick={add} disabled={busy || !ready || !agentId || !(own || workspace)} className={btnPrimary}>
          <Send className="w-3.5 h-3.5" /> {t('distribution.slack.add')}
        </button>
      </div>
      {slack.distribution === 'public' && slack.public_install_url ? (
        <div>
          <CopyRow label={t('distribution.slack.publicUrl')} value={slack.public_install_url} testId="slack-public-url" />
          <p className="text-xs text-gray-500 mt-1">{t('distribution.slack.publicUrlHint')}</p>
        </div>
      ) : (
        <p className="text-xs text-gray-500">{t('distribution.slack.privateNote')}</p>
      )}
      {slack.redirect_url && <CopyRow label={t('distribution.slack.redirectUrl')} value={slack.redirect_url} />}
      {slack.events_url && <CopyRow label={t('distribution.slack.eventsUrl')} value={slack.events_url} />}
      <InstallsTable channel="slack" installs={slack.installs} channelWorkspace={slack.workspace}
        workspaces={workspaces} onChanged={reload} />
    </SectionCard>
  );
}

function TeamsCard({ info, workspaces, reload }) {
  const { t } = useI18n();
  const toast = useToast();
  const teams = info.teams || {};
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState(false);
  const [tenant, setTenant] = useState('');
  const [adding, setAdding] = useState(false);

  const download = async () => {
    setBusy(true);
    setError('');
    setMissing(false);
    try {
      const { data } = await downloadTeamsPackage();
      saveBlob(data, 'agents-hub-teams.zip');
    } catch (err) {
      setError((await failure(err)) || t('distribution.actionFailed'));
      setMissing(err?.response?.status === 409);
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    setAdding(true);
    setError('');
    try {
      await addInstall('teams', { org_id: tenant.trim() });
      setTenant('');
      toast.success(t('distribution.installs.added'));
      reload();
    } catch (err) {
      setError(errorDetail(err) || t('distribution.actionFailed'));
    } finally {
      setAdding(false);
    }
  };

  if (!teams.available) {
    return <SectionCard className={CARD} title={t('distribution.teams.title')}><p className="text-sm text-gray-500">{t('distribution.notAvailable')}</p></SectionCard>;
  }
  return (
    <SectionCard className={CARD} title={t('distribution.teams.title')}>
      <div className="flex flex-wrap items-center gap-2">
        <Status tone={teams.configured ? 'good' : 'warn'}>
          {teams.configured ? t('distribution.slack.configured') : t('distribution.slack.notConfigured')}
        </Status>
        <Status tone={teams.enabled ? 'good' : 'off'}>
          {teams.enabled ? t('distribution.slack.enabled') : t('distribution.slack.disabled')}
        </Status>
      </div>
      {teams.messaging_endpoint && (
        <CopyRow label={t('distribution.teams.endpoint')} value={teams.messaging_endpoint} testId="teams-endpoint" />
      )}
      <div>
        <button type="button" onClick={download} disabled={busy} className={btnPrimary}>
          <Download className="w-3.5 h-3.5" /> {t('distribution.teams.download')}
        </button>
      </div>
      {error && (
        <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">
          {error}{' '}
          {missing && <Link to="/connectors?tab=teams" className="underline">{t('distribution.slack.openConnectors')}</Link>}
        </div>
      )}
      <p className="text-sm text-gray-600">{t('distribution.teams.steps')}</p>
      <div className="space-y-2 border-t border-gray-100 pt-3">
        <h3 className="text-sm font-semibold text-gray-800">{t('distribution.teams.addOrg')}</h3>
        <div className="flex flex-wrap gap-2">
          <input className={`${inputCls} flex-1 min-w-[12rem]`} value={tenant} aria-label={t('distribution.teams.tenantId')}
            placeholder={t('distribution.teams.tenantPlaceholder')} onChange={(e) => setTenant(e.target.value)} />
          <button type="button" onClick={add} disabled={adding || !tenant.trim()} className={btnSecondary}>
            <Plus className="w-3.5 h-3.5" /> {t('distribution.teams.add')}
          </button>
        </div>
      </div>
      <InstallsTable channel="teams" installs={teams.installs} channelWorkspace={teams.workspace}
        workspaces={workspaces} onChanged={reload} />
    </SectionCard>
  );
}

export default function Distribution() {
  const { t } = useI18n();
  const toast = useToast();
  const [params] = useSearchParams();
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [workspaces, setWorkspaces] = useState([]);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getDistribution();
      setInfo(data);
      setError('');
    } catch (err) {
      toast.error(t('distribution.loadFailed'), errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getWorkspaces()
      .then(({ data }) => setWorkspaces((Array.isArray(data) ? data : []).map((w) => w.name || w).filter(Boolean)))
      .catch(() => {});
  }, []);

  const installed = params.get('installed') === 'slack';
  const org = params.get('org');

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Send}
        title={t('distribution.title')}
        description={t('distribution.description')}
        actions={(
          <button type="button" onClick={load} className={btnSecondary}>
            <RefreshCw className="w-4 h-4" /> {t('distribution.refresh')}
          </button>
        )}
      />
      {installed && (
        <div role="status" className={`${notice} bg-green-50 border-green-200 text-green-700`}>
          {org ? t('distribution.installedSlack', { org }) : t('distribution.installedSlackPlain')}
        </div>
      )}
      {error && <div role="alert" className={`${notice} bg-red-50 border-red-200 text-red-700`}>{error}</div>}
      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200"><PageLoader /></div>
      ) : info && (
        <>
          {(info.warnings || []).map((w) => (
            <div key={w} className={`${notice} bg-amber-50 border-amber-200 text-amber-800 flex items-start gap-2`}>
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> <span>{t(`distribution.warnings.${w}`)}</span>
            </div>
          ))}
          {/* The cards differ a lot in height, so a plain grid left a hole
              under the short ones: two columns balanced by content instead. */}
          <BalancedColumns
            items={[
              { key: 'mcp', node: <McpCard info={info} workspaces={workspaces} /> },
              { key: 'obsidian', node: <ObsidianCard info={info} onError={setError} /> },
              { key: 'slack', node: <SlackCard info={info} workspaces={workspaces} reload={load} onError={setError} /> },
              { key: 'teams', node: <TeamsCard info={info} workspaces={workspaces} reload={load} /> },
            ]}
          />
        </>
      )}
    </PageContainer>
  );
}

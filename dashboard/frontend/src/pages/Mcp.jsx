import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react';

import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  testMcpServer,
  updateMcpServer,
} from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { CAPABILITY_LABELS, CAPABILITY_ORDER } from '../lib/capabilities';
import { useI18n } from '../i18n';
import { useWorkspace } from '../components/workspace';

/**
 * MCP servers: tool collections somebody else runs, attached per workspace.
 *
 * The third page in the Connect group, and the same direction as Connectors:
 * this hub reaches out. What makes it a page of its own rather than another tab
 * there is the question it has to ask. A connector is an integration this
 * product wrote and already knows the shape of; an MCP server hands over tools
 * nobody here has seen, so attaching one means declaring what the collection
 * can do. That declaration is what the capability guard enforces, so the form
 * puts it next to the connection details rather than behind an advanced
 * section: it is not an option, it is half of what attaching means.
 */

const EMPTY = {
  id: '',
  name: '',
  description: '',
  transport: 'stdio',
  command: '',
  args: '',
  url: '',
  headers: '',
  env: '',
  enabled: true,
  capabilities: { ingests_untrusted: false, reads_private: false, can_exfiltrate: false },
  approvalMode: 'none',
  approvalList: '',
  tool_allowlist: '',
};

const fmtWhen = (iso) => (iso
  ? new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
  : null);

// Key/value maps are edited as NAME=value lines. A table of two inputs per row
// is more chrome than this deserves, and a pasted block of environment lines is
// what people actually have in hand.
const pairsToText = (pairs) => Object.entries(pairs || {})
  .map(([k, v]) => `${k}=${v}`).join('\n');

const textToPairs = (text) => Object.fromEntries(
  String(text || '').split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const at = line.indexOf('=');
      return at === -1 ? [line, ''] : [line.slice(0, at).trim(), line.slice(at + 1).trim()];
    })
    .filter(([k]) => k),
);

const linesToList = (text) => String(text || '').split('\n')
  .map((line) => line.trim()).filter(Boolean);

function fromServer(server) {
  const approval = server.approval;
  return {
    ...EMPTY,
    ...server,
    args: (server.args || []).join('\n'),
    headers: pairsToText(server.headers),
    env: pairsToText(server.env),
    capabilities: { ...EMPTY.capabilities, ...(server.capabilities || {}) },
    approvalMode: Array.isArray(approval) ? 'list' : (approval || 'none'),
    approvalList: Array.isArray(approval) ? approval.join('\n') : '',
    tool_allowlist: (server.tool_allowlist || []).join('\n'),
  };
}

function toPayload(form) {
  const http = form.transport !== 'stdio';
  return {
    id: form.id.trim().toLowerCase(),
    name: form.name.trim() || form.id.trim(),
    description: form.description,
    transport: form.transport,
    command: http ? '' : form.command.trim(),
    args: http ? [] : linesToList(form.args),
    url: http ? form.url.trim() : '',
    headers: http ? textToPairs(form.headers) : {},
    env: http ? {} : textToPairs(form.env),
    enabled: form.enabled,
    capabilities: form.capabilities,
    approval: form.approvalMode === 'list' ? linesToList(form.approvalList) : form.approvalMode,
    tool_allowlist: linesToList(form.tool_allowlist),
  };
}

const LABEL = 'block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1';
const INPUT = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500';
const HINT = 'text-[11px] text-gray-400 mt-1 mb-3';

function Field({ label, hint, children }) {
  return (
    <div className="mb-1">
      <label className={LABEL}>{label}</label>
      {children}
      {hint ? <p className={HINT}>{hint}</p> : <div className="mb-3" />}
    </div>
  );
}

function Modal({ title, children, onClose, wide = false }) {
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm">
      <div className={`bg-white rounded-xl w-full p-6 shadow-2xl max-h-[90vh] overflow-y-auto ${wide ? 'max-w-3xl' : 'max-w-xl'}`}>
        <div className="flex items-start justify-between mb-4">
          <h3 className="text-lg font-bold text-gray-800">{title}</h3>
          <button onClick={onClose} aria-label="close" className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** The three ticks the capability guard reads, with the agent editor's wording. */
function CapabilityPicker({ value, onChange }) {
  const { t } = useI18n();
  return (
    <fieldset className="border border-indigo-100 bg-indigo-50/30 rounded-lg p-3 mb-4">
      <legend className="flex items-center gap-1 px-1 text-[10px] text-indigo-700 uppercase tracking-wider font-semibold">
        <ShieldCheck className="w-3 h-3" />
        {t('mcp.capabilitiesLabel')}
      </legend>
      {CAPABILITY_ORDER.map((cap) => (
        <label key={cap} className="flex items-center gap-2 text-sm text-gray-700 py-0.5">
          <input
            type="checkbox"
            checked={!!value[cap]}
            onChange={(e) => onChange({ ...value, [cap]: e.target.checked })}
          />
          {CAPABILITY_LABELS[cap]}
        </label>
      ))}
      <p className="text-[11px] text-gray-500 mt-2">{t('mcp.capabilitiesHint')}</p>
    </fieldset>
  );
}

function ServerForm({ workspace, server, transports, onClose, onSaved }) {
  const { t } = useI18n();
  const editing = !!server;
  const [form, setForm] = useState(() => (server ? fromServer(server) : EMPTY));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const payload = toPayload(form);
      if (editing) {
        delete payload.id;
        await updateMcpServer(server.id, payload, workspace);
      } else {
        await createMcpServer(payload, workspace);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const stdio = form.transport === 'stdio';
  const websocket = form.transport === 'websocket';

  return (
    <Modal
      wide
      onClose={onClose}
      title={editing ? t('mcp.editServer', { name: server.name || server.id }) : t('mcp.newServer')}
    >
      <form onSubmit={submit}>
        <div className="grid sm:grid-cols-2 sm:gap-4">
          <Field label={t('mcp.idLabel')} hint={editing ? null : t('mcp.idHint')}>
            <input
              value={form.id}
              onChange={(e) => set({ id: e.target.value })}
              placeholder="tickets"
              required
              disabled={editing}
              className={`${INPUT} disabled:bg-gray-50 disabled:text-gray-400`}
            />
          </Field>
          <Field label={t('mcp.nameLabel')}>
            <input
              value={form.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="Tickets"
              className={INPUT}
            />
          </Field>
        </div>

        <Field label={t('mcp.transportLabel')}>
          <select
            value={form.transport}
            onChange={(e) => set({ transport: e.target.value })}
            className={INPUT}
          >
            {(transports || ['stdio']).map((value) => (
              <option key={value} value={value}>{t(`mcp.transports.${value}`)}</option>
            ))}
          </select>
        </Field>

        {/* The transport decides which half of the form is even meaningful, so
            it switches rather than greying fields out: a URL box on a child
            process is a question nobody can answer. */}
        {stdio ? (
          <>
            <Field label={t('mcp.commandLabel')}>
              <input
                value={form.command}
                onChange={(e) => set({ command: e.target.value })}
                placeholder="npx"
                className={INPUT}
              />
            </Field>
            <Field label={t('mcp.argsLabel')} hint={t('mcp.argsHint')}>
              <textarea
                value={form.args}
                onChange={(e) => set({ args: e.target.value })}
                rows={3}
                placeholder={'-y\n@modelcontextprotocol/server-filesystem\n/srv/shared'}
                className={`${INPUT} font-mono text-xs`}
              />
            </Field>
            <Field label={t('mcp.envLabel')} hint={`${t('mcp.pairsHint')} ${t('mcp.scrubbedHint')}`}>
              <textarea
                value={form.env}
                onChange={(e) => set({ env: e.target.value })}
                rows={3}
                placeholder="SERVICE_TOKEN=..."
                className={`${INPUT} font-mono text-xs`}
              />
            </Field>
          </>
        ) : (
          <>
            <Field
              label={t('mcp.urlLabel')}
              hint={websocket ? t('mcp.urlWebsocketHint') : t('mcp.urlHttpHint')}
            >
              <input
                value={form.url}
                onChange={(e) => set({ url: e.target.value })}
                placeholder={websocket ? 'wss://tickets.internal/mcp' : 'https://tickets.internal/mcp'}
                className={INPUT}
              />
            </Field>
            <Field
              label={t('mcp.headersLabel')}
              hint={websocket ? t('mcp.headersWebsocketHint') : `${t('mcp.pairsHint')} ${t('mcp.secretsHint')}`}
            >
              <textarea
                value={form.headers}
                onChange={(e) => set({ headers: e.target.value })}
                rows={3}
                placeholder="Authorization=Bearer ..."
                className={`${INPUT} font-mono text-xs`}
              />
            </Field>
          </>
        )}

        <CapabilityPicker
          value={form.capabilities}
          onChange={(capabilities) => set({ capabilities })}
        />

        <div className="grid sm:grid-cols-2 sm:gap-4">
          <Field label={t('mcp.approvalLabel')} hint={t('mcp.approvalHint')}>
            <select
              value={form.approvalMode}
              onChange={(e) => set({ approvalMode: e.target.value })}
              className={INPUT}
            >
              <option value="none">{t('mcp.approval.none')}</option>
              <option value="all">{t('mcp.approval.all')}</option>
              <option value="list">{t('mcp.approval.list')}</option>
            </select>
          </Field>
          <Field label={t('mcp.allowlistLabel')} hint={t('mcp.allowlistHint')}>
            <textarea
              value={form.tool_allowlist}
              onChange={(e) => set({ tool_allowlist: e.target.value })}
              rows={2}
              className={`${INPUT} font-mono text-xs`}
            />
          </Field>
        </div>

        {form.approvalMode === 'list' && (
          <Field label={t('mcp.approvalListLabel')} hint={t('mcp.approvalListHint')}>
            <textarea
              value={form.approvalList}
              onChange={(e) => set({ approvalList: e.target.value })}
              rows={2}
              className={`${INPUT} font-mono text-xs`}
            />
          </Field>
        )}

        <label className="flex items-center gap-2 text-sm text-gray-700 mb-4">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => set({ enabled: e.target.checked })}
          />
          {t('mcp.enabledLabel')}
        </label>

        {error && <p className="text-sm text-red-600 mb-3">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-500">
            {t('mcp.cancel')}
          </button>
          <button
            type="submit"
            disabled={saving || !form.id.trim()}
            className="px-5 py-2 text-sm font-bold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {editing ? t('mcp.save') : t('mcp.create')}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** What a Test actually found: every tool, and which the allowlist keeps. */
function TestResult({ server, result, onClose }) {
  const { t } = useI18n();
  return (
    <Modal wide onClose={onClose} title={t('mcp.testTitle', { name: server.name || server.id })}>
      {!result.ok ? (
        <p className="text-sm text-red-600">
          {t('mcp.testFailed')} <span className="text-gray-500">{result.error}</span>
        </p>
      ) : result.tools.length === 0 ? (
        <p className="text-sm text-gray-500">{t('mcp.testEmpty')}</p>
      ) : (
        <ul className="space-y-2">
          {result.tools.map((tool) => (
            <li key={tool.id} className="border border-gray-200 rounded-lg px-3 py-2">
              <code className="text-xs font-semibold text-gray-800">{tool.id}</code>
              <span className={`ml-2 text-[10px] uppercase tracking-wider font-bold ${tool.allowed ? 'text-emerald-700' : 'text-gray-400'}`}>
                {tool.allowed ? t('mcp.allowed') : t('mcp.filtered')}
              </span>
              {tool.description && (
                <p className="text-xs text-gray-500 mt-1">{tool.description}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-gray-400 mt-4">
        {t('mcp.usageBody', { id: server.id })}
      </p>
      <div className="flex justify-end mt-4">
        <button onClick={onClose} className="px-4 py-2 text-sm text-gray-500">{t('mcp.close')}</button>
      </div>
    </Modal>
  );
}

function ServerRow({ server, busy, onEdit, onTest, onDelete }) {
  const { t } = useI18n();
  const seen = fmtWhen(server.last_seen);
  const granted = CAPABILITY_ORDER.filter((cap) => server.capabilities?.[cap]);
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 py-3">
      <div className="flex items-center gap-4">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${
            !server.enabled ? 'bg-gray-300'
              : server.last_error ? 'bg-red-500'
                : seen ? 'bg-emerald-500' : 'bg-amber-400'
          }`}
        />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-gray-800 truncate">
            {server.name || server.id}
          </span>
          <span className="block text-[11px] text-gray-400 truncate">
            <code>{server.id}</code>
            {' · '}
            {server.transport}
            {' · '}
            {/* "not loaded" is not zero: nothing has connected yet, and saying
                zero would read as a working server with no tools. */}
            {server.tool_count === null
              ? t('mcp.toolsNotLoaded')
              : t('mcp.toolCount', { count: server.tool_count })}
            {seen ? ` · ${t('mcp.lastSeen', { when: seen })}` : ` · ${t('mcp.neverSeen')}`}
          </span>
        </span>

        <span className="hidden md:flex items-center gap-1">
          {granted.length === 0 ? (
            <span className="text-[10px] text-gray-400">{t('mcp.grantsNothing')}</span>
          ) : granted.map((cap) => (
            <span
              key={cap}
              className="text-[10px] uppercase tracking-wider font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full"
            >
              {CAPABILITY_LABELS[cap]}
            </span>
          ))}
        </span>

        {!server.enabled && (
          <span className="text-[10px] uppercase tracking-wider font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">
            {t('mcp.disabled')}
          </span>
        )}

        <button
          onClick={onTest}
          disabled={busy}
          className="px-3 py-1.5 text-xs font-semibold text-indigo-700 bg-indigo-50 rounded-lg hover:bg-indigo-100 disabled:opacity-50"
        >
          {busy ? t('mcp.testing') : t('mcp.test')}
        </button>
        <button onClick={onEdit} className="px-3 py-1.5 text-xs text-gray-600 hover:text-gray-900">
          {t('mcp.edit')}
        </button>
        <button onClick={onDelete} aria-label={t('mcp.delete')} className="text-gray-400 hover:text-red-600">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      {server.last_error && (
        <p className="flex items-start gap-1 text-xs text-red-600 mt-2">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          {server.last_error}
        </p>
      )}
    </div>
  );
}

export default function Mcp() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [servers, setServers] = useState([]);
  const [transports, setTransports] = useState(['stdio']);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null); // a server, or 'new'
  const [testing, setTesting] = useState('');
  const [result, setResult] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await listMcpServers(selectedWorkspace);
      setServers(data.servers || []);
      setTransports(data.transports || ['stdio']);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [selectedWorkspace]);

  useEffect(() => { load(); }, [load]);

  const runTest = async (server) => {
    setTesting(server.id);
    try {
      const { data } = await testMcpServer(server.id, selectedWorkspace);
      setResult({ server, ...data, tools: data.tools || [] });
    } catch (err) {
      setResult({ server, ok: false, error: err.response?.data?.detail || err.message, tools: [] });
    } finally {
      setTesting('');
      load();
    }
  };

  const remove = async (server) => {
    // A confirm() rather than a dialog: what is lost is a configuration, and
    // the agents that referenced it keep working with fewer tools.
    if (!window.confirm(t('mcp.deleteConfirm', { name: server.name || server.id }))) return;
    try {
      await deleteMcpServer(server.id, selectedWorkspace);
      load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Plug}
        title={t('mcp.title')}
        description={t('mcp.description')}
        actions={<>
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('mcp.refresh')}
          </button>
          <button
            onClick={() => setEditing('new')}
            className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" />
            {t('mcp.add')}
          </button>
        </>}
      />

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-400">{t('mcp.loading')}</p>
      ) : servers.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-xl p-8 text-center">
          <Plug className="w-8 h-8 text-indigo-400 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-gray-800 mb-1">{t('mcp.emptyTitle')}</h3>
          <p className="text-sm text-gray-500 max-w-xl mx-auto">{t('mcp.emptyBody')}</p>
          <p className="text-xs text-gray-400 mt-4">{t('mcp.emptyExample')}</p>
          <button
            onClick={() => setEditing('new')}
            className="mt-5 px-5 py-2 text-sm font-bold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            {t('mcp.add')}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {servers.map((server) => (
            <ServerRow
              key={server.id}
              server={server}
              busy={testing === server.id}
              onEdit={() => setEditing(server)}
              onTest={() => runTest(server)}
              onDelete={() => remove(server)}
            />
          ))}
        </div>
      )}

      {editing && (
        <ServerForm
          workspace={selectedWorkspace}
          server={editing === 'new' ? null : editing}
          transports={transports}
          onClose={() => setEditing(null)}
          onSaved={load}
        />
      )}

      {result && (
        <TestResult server={result.server} result={result} onClose={() => setResult(null)} />
      )}
    </PageContainer>
  );
}

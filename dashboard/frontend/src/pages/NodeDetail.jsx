import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/WorkspaceContext';
import {
  getNodeById,
  getNodeLogs,
  getNodeConnections,
  getNodeRuns,
  exposeNode,
  unexposeNode,
  startNode,
  stopNode,
  deleteNode,
} from '../api';
import {
  Server,
  ArrowLeft,
  Activity,
  Globe,
  GlobeLock,
  Copy,
  Check,
  FileText,
  Play,
  RotateCw,
  Square,
  Trash2,
  Loader,
  Clock,
  Wifi,
  WifiOff,
  AlertTriangle,
  RefreshCw,
  ExternalLink,
  Box,
} from 'lucide-react';

// ── Status helpers ─────────────────────────────────────────────────────────

const STATUS = {
  running:   { dot: 'bg-green-500 animate-pulse',  badge: 'bg-green-100 text-green-800',   label: 'Running' },
  starting:  { dot: 'bg-yellow-400 animate-pulse', badge: 'bg-yellow-100 text-yellow-800', label: 'Starting' },
  stopping:  { dot: 'bg-orange-400 animate-pulse', badge: 'bg-orange-100 text-orange-800', label: 'Stopping' },
  stopped:   { dot: 'bg-gray-400',                 badge: 'bg-gray-100 text-gray-600',     label: 'Stopped' },
  failed:    { dot: 'bg-red-500',                  badge: 'bg-red-100 text-red-700',       label: 'Failed' },
  completed: { dot: 'bg-blue-400',                 badge: 'bg-blue-100 text-blue-700',     label: 'Completed' },
};

function StatusBadge({ status }) {
  const s = STATUS[status] || STATUS.stopped;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${s.badge}`}>
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${s.dot}`} />
      {s.label}
    </span>
  );
}

function uptime(startedAt, finishedAt) {
  if (!startedAt) return '—';
  const end = finishedAt ? new Date(finishedAt) : new Date();
  const secs = Math.max(0, Math.floor((end - new Date(startedAt)) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// ── Copy button ────────────────────────────────────────────────────────────

function CopyButton({ text, className = '' }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={handleCopy}
      title="Copy to clipboard"
      className={`p-1.5 rounded transition-colors ${copied ? 'text-green-600 bg-green-50' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-100'} ${className}`}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

// ── Expose panel ───────────────────────────────────────────────────────────

function ExposePanel({ node, onNodeUpdated }) {
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState('');
  const isExposed = !!node.is_exposed;
  const isService = (node.node_type || 'worker') === 'service';

  const servicePort = node.http_host_port || node.http_port;
  const serviceUrl = node.http_url || (servicePort ? `http://localhost:${servicePort}` : null);
  const runUrl = serviceUrl ? `${serviceUrl}/run` : null;
  const gatewayUrl = node.expose_token ? `http://localhost:8000/api/external/${node.expose_token}/run` : null;

  const handleToggle = async () => {
    setToggling(true);
    setError('');
    try {
      if (isExposed) {
        await unexposeNode(node.node_id);
        onNodeUpdated({ ...node, is_exposed: false, expose_token: null, exposed_at: null, external_url: null });
      } else {
        const r = await exposeNode(node.node_id);
        onNodeUpdated(r.data);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to update exposure');
    } finally {
      setToggling(false);
    }
  };

  // ── HTTP Service node: always visible, no toggle, token is optional auth ───
  if (isService) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div className="flex items-center gap-3">
          <Globe className="w-5 h-5 text-violet-600" />
          <div>
            <h2 className="text-sm font-semibold text-gray-800">HTTP Service</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              This node runs its own HTTP server and accepts requests directly.
              {node.expose_token
                ? ' Bearer token authentication is active.'
                : ' Generate a token to enable bearer token authentication.'}
            </p>
          </div>
        </div>

        {error && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
        )}

        {runUrl && (
          <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-500 mb-1.5">Service URL</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs text-indigo-800 break-all">{runUrl}</code>
              <CopyButton text={runUrl} />
            </div>
          </div>
        )}

        <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
          <div className="flex items-center justify-between mb-1.5">
            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Access Token</p>
            <button
              onClick={handleToggle}
              disabled={toggling}
              className="text-[10px] font-semibold px-2 py-0.5 rounded border transition-colors disabled:opacity-50
                border-gray-300 text-gray-600 hover:bg-gray-100"
            >
              {toggling ? '…' : node.expose_token ? 'Revoke' : 'Generate'}
            </button>
          </div>
          {node.expose_token ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs text-gray-700 break-all">{node.expose_token}</code>
              <CopyButton text={node.expose_token} />
            </div>
          ) : (
            <p className="text-xs text-gray-400 italic">No token — endpoint is open</p>
          )}
        </div>

        {runUrl && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-amber-600 mb-1.5">Example Request</p>
            <pre className="text-xs text-amber-800 whitespace-pre-wrap break-all leading-5">{node.expose_token
              ? `curl -X POST "${runUrl}" \\\n  -H "Authorization: Bearer ${node.expose_token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"prompt": "Hello, what can you do?"}'`
              : `curl -X POST "${runUrl}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"prompt": "Hello, what can you do?"}'`
            }</pre>
          </div>
        )}

        {serviceUrl && (
          <div className="bg-violet-50 border border-violet-200 rounded-lg p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-violet-600 mb-1.5">Health Check</p>
            <pre className="text-xs text-violet-800 whitespace-pre-wrap break-all leading-5">{`curl "${serviceUrl}/health"`}</pre>
          </div>
        )}
      </div>
    );
  }

  // ── Task Worker node: expose toggle + gateway URL ─────────────────────────
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {isExposed
            ? <Globe className="w-5 h-5 text-indigo-600" />
            : <GlobeLock className="w-5 h-5 text-gray-400" />
          }
          <div>
            <h2 className="text-sm font-semibold text-gray-800">External Access</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {isExposed
                ? 'This node is reachable via an external URL with a secret token.'
                : 'Enable to allow external services to submit tasks to this node.'}
            </p>
          </div>
        </div>

        <button
          onClick={handleToggle}
          disabled={toggling}
          className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
            isExposed ? 'bg-indigo-600' : 'bg-gray-200'
          } ${toggling ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          <span
            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
              isExposed ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {error && (
        <div className="mt-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
      )}

      {isExposed && gatewayUrl && (
        <div className="mt-4 space-y-3">
          <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-500 mb-1.5">External URL</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs text-indigo-800 break-all">{gatewayUrl}</code>
              <CopyButton text={gatewayUrl} />
            </div>
          </div>

          {node.expose_token && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">Access Token</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs text-gray-700 break-all">{node.expose_token}</code>
                <CopyButton text={node.expose_token} />
              </div>
            </div>
          )}

          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-amber-600 mb-1.5">Example Request</p>
            <pre className="text-xs text-amber-800 whitespace-pre-wrap break-all leading-5">{`curl -X POST "${gatewayUrl}" \\
  -H "Content-Type: application/json" \\
  -d '{"prompt": "Your task description here"}'`}</pre>
          </div>

          {node.exposed_at && (
            <p className="text-[10px] text-gray-400">
              Exposed since {fmtDate(node.exposed_at)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Connection history ─────────────────────────────────────────────────────

const CONN_STATUS_COLOR = {
  202: 'bg-green-100 text-green-700',
  200: 'bg-green-100 text-green-700',
  400: 'bg-yellow-100 text-yellow-700',
  403: 'bg-orange-100 text-orange-700',
  404: 'bg-gray-100 text-gray-600',
  500: 'bg-red-100 text-red-700',
  503: 'bg-red-100 text-red-700',
};

const RUN_STATUS_HTTP = { completed: 200, failed: 500, running: 202, stopped: 0 };
const RUN_STATUS_HTTP_COLOR = {
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-yellow-100 text-yellow-800',
  stopped: 'bg-gray-100 text-gray-600',
};

function elapsedMs(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return null;
  return Math.max(0, new Date(finishedAt) - new Date(startedAt));
}

function ConnectionHistory({ node }) {
  const nodeId = node.node_id;
  const isService = (node.node_type || 'worker') === 'service';
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const { liveUpdates } = useWorkspace();
  const navigate = useNavigate();

  const fetchConnections = useCallback(async () => {
    try {
      if (isService) {
        const r = await getNodeRuns(nodeId);
        setConnections(r.data);
      } else {
        const r = await getNodeConnections(nodeId);
        setConnections(r.data);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [nodeId, isService]);

  useEffect(() => {
    fetchConnections();
    if (!liveUpdates) return;
    const id = setInterval(fetchConnections, 5000);
    return () => clearInterval(id);
  }, [fetchConnections, liveUpdates]);

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100 bg-gray-50">
        <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          <Wifi className="w-4 h-4 text-gray-500" />
          Connection History
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">{connections.length} total</span>
          <button onClick={fetchConnections} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-10">
          <Loader className="w-5 h-5 animate-spin text-indigo-400" />
        </div>
      ) : connections.length === 0 ? (
        <div className="py-12 text-center">
          <WifiOff className="w-8 h-8 text-gray-300 mx-auto mb-2" />
          <p className="text-sm text-gray-400">No connections yet.</p>
          <p className="text-xs text-gray-400 mt-0.5">
            {isService ? 'Make your first POST /run request to this service.' : 'Expose the node and make your first external request.'}
          </p>
        </div>
      ) : isService ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Time</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Run ID</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Prompt</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Output</th>
                <th className="text-right px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">ms</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {connections.map((r) => (
                <tr key={r.run_id || r.started_at} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(r.started_at)}</td>
                  <td className="px-4 py-2.5 font-mono">
                    {r.run_id ? (
                      <button
                        onClick={() => navigate(`/messages/${r.run_id}`)}
                        className="text-indigo-600 hover:text-indigo-900 hover:underline"
                      >
                        {r.run_id.slice(0, 8)}…
                      </button>
                    ) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-gray-700 max-w-xs truncate" title={r.title}>{r.title || '—'}</td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 rounded-full font-semibold text-[10px] ${RUN_STATUS_HTTP_COLOR[r.status] || 'bg-gray-100 text-gray-600'}`}>
                      {RUN_STATUS_HTTP[r.status] ?? r.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-gray-500 max-w-xs truncate" title={r.output || r.error}>
                    {r.status === 'failed'
                      ? <span className="text-red-600">{r.error || 'error'}</span>
                      : (r.output ? r.output.slice(0, 80) : '—')}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-500 whitespace-nowrap">
                    {elapsedMs(r.started_at, r.finished_at) ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Time</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Client IP</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Prompt</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Detail</th>
                <th className="text-right px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">ms</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {connections.map((c) => {
                const colorClass = CONN_STATUS_COLOR[c.response_status] || 'bg-gray-100 text-gray-600';
                return (
                  <tr key={c.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(c.timestamp)}</td>
                    <td className="px-4 py-2.5 text-gray-600 whitespace-nowrap">{c.client_ip}</td>
                    <td className="px-4 py-2.5 text-gray-700 max-w-xs truncate" title={c.prompt_preview}>{c.prompt_preview || '—'}</td>
                    <td className="px-4 py-2.5">
                      <span className={`px-2 py-0.5 rounded-full font-semibold text-[10px] ${colorClass}`}>
                        {c.response_status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 max-w-xs truncate" title={c.response_detail}>{c.response_detail || '—'}</td>
                    <td className="px-4 py-2.5 text-right text-gray-500 whitespace-nowrap">{c.elapsed_ms != null ? c.elapsed_ms : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Runs panel ─────────────────────────────────────────────────────────────

const RUN_STATUS = {
  running:   'bg-yellow-100 text-yellow-800',
  completed: 'bg-green-100 text-green-800',
  failed:    'bg-red-100 text-red-700',
  stopped:   'bg-gray-100 text-gray-600',
  stop:      'bg-orange-100 text-orange-700',
};

function RunsPanel({ node }) {
  const isService = (node.node_type || 'worker') === 'service';
  const { liveUpdates } = useWorkspace();
  const navigate = useNavigate();
  const isActive = ['running', 'starting'].includes(node.status);

  // Service nodes: fetch full run history from backend
  const [runs, setRuns] = useState([]);
  const [loadingRuns, setLoadingRuns] = useState(isService);

  const fetchRuns = useCallback(async () => {
    try {
      const r = await getNodeRuns(node.node_id);
      setRuns(r.data);
    } catch {
      // silently ignore
    } finally {
      setLoadingRuns(false);
    }
  }, [node.node_id]);

  useEffect(() => {
    if (!isService) return;
    fetchRuns();
    if (!liveUpdates || !isActive) return;
    const id = setInterval(fetchRuns, 5000);
    return () => clearInterval(id);
  }, [fetchRuns, isService, isActive, liveUpdates]);

  // Worker nodes: use in-progress sessions from enriched node data
  const workerRuns = Array.isArray(node.running_sessions) ? node.running_sessions : [];
  const displayRuns = isService ? runs : workerRuns;
  const emptyMsg = isService ? 'No runs yet for this service node.' : 'No sessions currently running on this node.';

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100 bg-gray-50">
        <h2 className="text-sm font-semibold text-gray-800">
          {isService ? 'Runs' : 'Running Sessions'}
        </h2>
        <div className="flex items-center gap-2">
          {isService && (
            <button onClick={fetchRuns} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}
          <span className="text-xs text-gray-500">{displayRuns.length}</span>
        </div>
      </div>

      {loadingRuns ? (
        <div className="flex justify-center py-8">
          <Loader className="w-5 h-5 animate-spin text-indigo-400" />
        </div>
      ) : displayRuns.length === 0 ? (
        <div className="px-5 py-8 text-sm text-gray-400">{emptyMsg}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Run ID</th>
                {isService ? (
                  <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Prompt</th>
                ) : (
                  <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Task ID</th>
                )}
                <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Status</th>
                <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Started</th>
                {isService && (
                  <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Finished</th>
                )}
                {isService && (
                  <th className="text-left px-5 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">Output</th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {displayRuns.map((r) => (
                <tr key={r.run_id || r.started_at} className="hover:bg-gray-50">
                  <td className="px-5 py-2.5 font-mono">
                    {r.run_id ? (
                      <button
                        onClick={() => navigate(`/messages/${r.run_id}`)}
                        className="text-indigo-600 hover:text-indigo-900 hover:underline"
                      >
                        {r.run_id.slice(0, 8)}…
                      </button>
                    ) : '—'}
                  </td>
                  {isService ? (
                    <td className="px-5 py-2.5 text-gray-600 max-w-[180px] truncate" title={r.title}>{r.title || '—'}</td>
                  ) : (
                    <td className="px-5 py-2.5 text-gray-600">{r.task_id ? `${r.task_id.slice(0, 8)}…` : '—'}</td>
                  )}
                  <td className="px-5 py-2.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${RUN_STATUS[r.status] || 'bg-gray-100 text-gray-600'}`}>
                      {r.status || 'running'}
                    </span>
                  </td>
                  <td className="px-5 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(r.started_at)}</td>
                  {isService && (
                    <td className="px-5 py-2.5 text-gray-500 whitespace-nowrap">{fmtDate(r.finished_at)}</td>
                  )}
                  {isService && (
                    <td className="px-5 py-2.5 text-gray-600 max-w-[200px] truncate" title={r.output || r.error}>
                      {r.status === 'failed'
                        ? <span className="text-red-600">{r.error || 'error'}</span>
                        : (r.output ? r.output.slice(0, 80) : '—')}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Logs panel ─────────────────────────────────────────────────────────────

function LogsPanel({ node }) {
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef(null);
  const isActive = ['running', 'starting', 'stopping'].includes(node.status);
  const { liveUpdates } = useWorkspace();

  const fetchLogs = useCallback(async () => {
    try {
      const r = await getNodeLogs(node.node_id);
      setLogs(r.data.logs || '(empty)');
    } catch {
      setLogs('Failed to load logs.');
    } finally {
      setLoading(false);
    }
  }, [node.node_id]);

  useEffect(() => {
    fetchLogs();
    if (!liveUpdates || !isActive) return;
    const id = setInterval(fetchLogs, 3000);
    return () => clearInterval(id);
  }, [fetchLogs, isActive, liveUpdates]);

  useEffect(() => { bottomRef.current?.scrollIntoView(); }, [logs]);

  return (
    <div className="bg-gray-950 rounded-xl border border-gray-800 overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <FileText className="w-4 h-4 text-gray-400" />
          Activity Log
        </h2>
        <div className="flex items-center gap-2">
          {isActive && (
            <span className="flex items-center gap-1 text-[10px] text-green-500">
              <Activity className="w-3 h-3 animate-pulse" />
              Live
            </span>
          )}
          <button onClick={fetchLogs} className="p-1 rounded text-gray-500 hover:text-gray-300 hover:bg-gray-800">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      <div className="p-5 max-h-96 overflow-auto">
        {loading ? (
          <div className="flex justify-center py-10">
            <Loader className="w-5 h-5 animate-spin text-indigo-400" />
          </div>
        ) : (
          <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{logs}</pre>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function NodeDetail() {
  const { nodeId } = useParams();
  const navigate = useNavigate();
  const { liveUpdates } = useWorkspace();
  const [node, setNode] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const fetchNode = useCallback(async () => {
    try {
      const r = await getNodeById(nodeId);
      setNode(r.data);
    } catch (e) {
      setError(e.response?.data?.detail || 'Node not found');
    } finally {
      setLoading(false);
    }
  }, [nodeId]);

  useEffect(() => {
    fetchNode();
    if (!liveUpdates) return;
    const id = setInterval(fetchNode, 5000);
    return () => clearInterval(id);
  }, [fetchNode, liveUpdates]);

  const handleStop = async () => {
    setBusy('stopping');
    try { await stopNode(nodeId); await fetchNode(); }
    catch (e) { setError(e.response?.data?.detail || 'Failed to stop'); }
    finally { setBusy(''); }
  };

  const handleDelete = async () => {
    if (!window.confirm('Remove this node record?')) return;
    setBusy('deleting');
    try {
      await deleteNode(nodeId);
      navigate('/nodes');
    } catch (e) {
      setError(e.response?.data?.detail || 'Cannot delete — stop the node first');
      setBusy('');
    }
  };

  const handleStart = async () => {
    setBusy('starting');
    setError('');
    try {
      const r = await startNode({
        agent_id: node.agent_id,
        workspace: node.workspace || null,
        label: node.label || null,
      });
      navigate(`/nodes/${r.data.node_id}`);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start node');
      setBusy('');
    }
  };

  const handleRestart = async () => {
    setBusy('restarting');
    setError('');
    try {
      if (node.status === 'running' || node.status === 'starting') {
        await stopNode(nodeId);
      }
      const r = await startNode({
        agent_id: node.agent_id,
        workspace: node.workspace || null,
        label: node.label || null,
      });
      // For local-process restarts, remove the previous node record once stoppable.
      if ((node.execution_mode || 'local') !== 'docker') {
        for (let i = 0; i < 12; i++) {
          try {
            await deleteNode(nodeId);
            break;
          } catch (_) {
            await new Promise((res) => setTimeout(res, 500));
          }
        }
      }
      navigate(`/nodes/${r.data.node_id}`);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to restart node');
      setBusy('');
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-32">
        <Loader className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  if (error && !node) {
    return (
      <div className="text-center py-32">
        <AlertTriangle className="w-8 h-8 text-red-400 mx-auto mb-3" />
        <p className="text-gray-600 text-sm">{error}</p>
        <button onClick={() => navigate('/nodes')} className="mt-4 text-sm text-indigo-600 hover:underline">
          Back to Nodes
        </button>
      </div>
    );
  }

  const isActive = node.status === 'running' || node.status === 'starting';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/nodes')}
            className="p-2 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Server className="w-5 h-5 text-indigo-600" />
              {node.agent_name || node.agent_id}
              {node.is_default && (
                <span className="text-[10px] font-bold uppercase bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded">
                  default
                </span>
              )}
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">{node.node_id}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Stopped/failed/completed: Start + Delete */}
          {!isActive && (
            <>
              <button
                onClick={handleStart}
                disabled={!!busy}
                className="flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-40 transition-colors"
              >
                {busy === 'starting' ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                Start
              </button>
              <button
                onClick={handleDelete}
                disabled={!!busy}
                className="flex items-center gap-1.5 px-3 py-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 disabled:opacity-40 transition-colors"
              >
                {busy === 'deleting' ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                Delete
              </button>
            </>
          )}
          {/* Running/starting: Restart + Stop */}
          {isActive && (
            <>
              <button
                onClick={handleRestart}
                disabled={!!busy}
                className="flex items-center gap-1.5 px-3 py-2 text-sm text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100 disabled:opacity-40 transition-colors"
              >
                {busy === 'restarting' ? <Loader className="w-4 h-4 animate-spin" /> : <RotateCw className="w-4 h-4" />}
                Restart
              </button>
              <button
                onClick={handleStop}
                disabled={!!busy}
                className="flex items-center gap-1.5 px-3 py-2 text-sm text-orange-600 bg-orange-50 border border-orange-200 rounded-lg hover:bg-orange-100 disabled:opacity-40 transition-colors"
              >
                {busy === 'stopping' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                Stop
              </button>
            </>
          )}
        </div>
      </div>

      {/* Info cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Status</p>
          <StatusBadge status={node.status} />
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Uptime</p>
          <p className="text-sm font-semibold text-gray-700">
            {uptime(node.started_at, node.finished_at)}
          </p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Workspace</p>
          <p className="text-sm text-gray-700 truncate">{node.workspace || '—'}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Node Type</p>
          {(node.node_type || 'worker') === 'service' ? (
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-violet-700">
              HTTP Service
            </span>
          ) : (
            <span className="text-sm font-semibold text-gray-600">Task Worker</span>
          )}
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Agent Mode</p>
          {node.execution_mode === 'docker' ? (
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-blue-700">
              <Box className="w-4 h-4" />
              Container
            </span>
          ) : (
            <span className="text-sm font-semibold text-gray-600">Local Process</span>
          )}
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">
            {node.execution_mode === 'docker' ? 'Container' : 'PID'}
          </p>
          {node.execution_mode === 'docker' ? (
            <div className="flex items-center gap-1.5">
              <p className="text-sm text-blue-800 truncate">{node.container_name || '—'}</p>
              {node.container_name && <CopyButton text={node.container_name} />}
            </div>
          ) : (
            <p className="text-sm text-gray-700">{node.pid || '—'}</p>
          )}
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Started</p>
          <p className="text-sm text-gray-700">{fmtDate(node.started_at)}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Runs</p>
          <p className="text-sm font-semibold text-indigo-700">{node.running_sessions_count ?? 0}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1">Finished</p>
          <p className="text-sm text-gray-700">{fmtDate(node.finished_at)}</p>
        </div>
      </div>

      <RunsPanel node={node} />

      {/* Expose panel */}
      <ExposePanel node={node} onNodeUpdated={setNode} />

      {/* Connection history */}
      <ConnectionHistory node={node} />

      {/* Activity log */}
      <LogsPanel node={node} />
    </div>
  );
}

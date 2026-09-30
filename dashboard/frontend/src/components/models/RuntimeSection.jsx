import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, Loader, Trash2, AlertCircle, Download, Search, Power, PowerOff } from 'lucide-react';
import {
  getRuntimeStatus, getRuntimeHfFiles, downloadRuntimeModel,
  loadRuntimeModel, unloadRuntimeModel, deleteRuntimeModel,
} from '../../api/localModels';
import { humanBytes } from './jobs';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';
const STATUS_POLL_MS = 10000;

function MemoryBar({ label, used, total }) {
  const { t } = useI18n();
  if (!total) return null;
  const pct = Math.max(0, Math.min(100, (100 * used) / total));
  return (
    <div className="text-xs">
      <div className="flex items-center justify-between text-gray-500 mb-1">
        <span>{label}</span>
        <span className="tabular-nums">{t('localModels.runtime.memoryOfTotal', { used: humanBytes(used), total: humanBytes(total) })}</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/**
 * The hub's own model runtime: a llama.cpp-style server over local GGUF /
 * safetensors files, run separately (compose profile `models`, or
 * `python deploy/models/app.py`). Three states worth telling apart:
 * not configured at all, configured but unreachable, and reachable.
 */
export default function RuntimeSection({ refreshKey, onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyFile, setBusyFile] = useState('');
  const [loadForm, setLoadForm] = useState({}); // file -> { context_length, gpu_layers }
  const [openLoadFor, setOpenLoadFor] = useState('');

  const [repo, setRepo] = useState('');
  const [revision, setRevision] = useState('');
  const [hfFiles, setHfFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState('');
  const [listing, setListing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const pollRef = useRef(null);

  const load = useCallback(async (silent) => {
    if (!silent) setLoading(true);
    try {
      const { data } = await getRuntimeStatus();
      setState(data || { configured: false });
    } catch (e) {
      setState({ configured: true, ok: false, error: errorDetail(e) });
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load, refreshKey]);

  // Poll every 10s while this section stays mounted (i.e. while the Local
  // tab is open), independent of the shared job list's own faster polling.
  useEffect(() => {
    pollRef.current = setInterval(() => load(true), STATUS_POLL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [load]);

  const handleListFiles = async () => {
    const r = repo.trim();
    if (!r) return;
    setListing(true);
    setHfFiles([]);
    setSelectedFile('');
    try {
      const { data } = await getRuntimeHfFiles(r, revision.trim() || undefined);
      const files = (data?.files || []).filter((f) => f.file?.toLowerCase().endsWith('.gguf'));
      setHfFiles(files);
      if (files.length === 0) toast.error(t('localModels.runtime.noGgufFiles'));
      else setSelectedFile(files[0].file);
    } catch (e) {
      toast.error(t('localModels.runtime.listFilesFailed'), errorDetail(e));
    } finally {
      setListing(false);
    }
  };

  const handleDownload = async () => {
    const r = repo.trim();
    if (!r || !selectedFile) return;
    setDownloading(true);
    try {
      await downloadRuntimeModel(r, selectedFile, revision.trim() || undefined);
      toast.success(t('localModels.runtime.downloadStarted', { file: selectedFile }));
      onJobStarted?.();
    } catch (e) {
      toast.error(t('localModels.runtime.downloadFailed'), errorDetail(e));
    } finally {
      setDownloading(false);
    }
  };

  const handleLoad = async (file) => {
    const f = loadForm[file] || {};
    const contextLength = Number(f.context_length ?? 4096) || 4096;
    const gpuLayers = f.gpu_layers === '' || f.gpu_layers == null ? -1 : Number(f.gpu_layers);
    setBusyFile(file);
    try {
      await loadRuntimeModel(file, contextLength, gpuLayers);
      toast.success(t('localModels.runtime.loaded', { file }));
      setOpenLoadFor('');
      load(true);
    } catch (e) {
      toast.error(t('localModels.runtime.loadFailed'), errorDetail(e));
    } finally {
      setBusyFile('');
    }
  };

  const handleUnload = async (file) => {
    setBusyFile(file);
    try {
      await unloadRuntimeModel(file);
      toast.success(t('localModels.runtime.unloaded', { file }));
      load(true);
    } catch (e) {
      toast.error(t('localModels.runtime.unloadFailed'), errorDetail(e));
    } finally {
      setBusyFile('');
    }
  };

  const handleDelete = async (file) => {
    if (!window.confirm(t('localModels.runtime.confirmDelete', { file }))) return;
    setBusyFile(file);
    try {
      await deleteRuntimeModel(file);
      toast.success(t('localModels.runtime.deleted', { file }));
      load(true);
    } catch (e) {
      toast.error(t('localModels.runtime.deleteFailed'), errorDetail(e));
    } finally {
      setBusyFile('');
    }
  };

  const configured = !!state?.configured;
  const ok = state?.ok !== false;
  const models = state?.models || [];
  const memory = state?.memory || {};
  const ramUsed = memory.ram_total_bytes != null && memory.ram_available_bytes != null
    ? memory.ram_total_bytes - memory.ram_available_bytes
    : null;

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="text-sm font-semibold text-gray-800">{t('localModels.runtime.title')}</span>
          {configured && state?.url && <span className="text-xs text-gray-400 font-mono truncate">{state.url}</span>}
        </div>
        {configured && (
          <button
            type="button"
            onClick={() => load(false)}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors shrink-0"
          >
            {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {t('common.refresh')}
          </button>
        )}
      </div>

      <div className="p-3">
        {loading && !state ? (
          <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('localModels.loading')}</p>
        ) : !configured ? (
          <div className="text-sm text-gray-600 space-y-1.5">
            <p>{t('localModels.runtime.notConfigured')}</p>
            <p className="text-xs text-gray-500">
              {t('localModels.runtime.notConfiguredHint')} <Link to="/docs" className="text-indigo-600 hover:underline">{t('localModels.runtime.docsLink')}</Link>.
            </p>
          </div>
        ) : !ok ? (
          <p className="flex items-center gap-1.5 text-sm text-red-700"><AlertCircle className="w-4 h-4 shrink-0" /> {state?.error || t('localModels.runtime.unreachable')}</p>
        ) : (
          <>
            {(memory.ram_total_bytes || memory.gpu) && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3 pb-3 border-b border-gray-50">
                {ramUsed != null && <MemoryBar label={t('localModels.runtime.ram')} used={ramUsed} total={memory.ram_total_bytes} />}
                {memory.gpu && (
                  <MemoryBar
                    label={memory.gpu.name ? t('localModels.runtime.gpuNamed', { name: memory.gpu.name }) : t('localModels.runtime.gpu')}
                    used={memory.gpu.used_bytes}
                    total={memory.gpu.total_bytes}
                  />
                )}
              </div>
            )}

            {models.length === 0 ? (
              <p className="text-sm text-gray-400 px-1 py-2 mb-3">{t('localModels.runtime.noModels')}</p>
            ) : (
              <div className="overflow-x-auto border border-gray-100 rounded-lg mb-3">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.name')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.format')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.size')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.context')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.runtime.status')}</th>
                      <th className="px-2 py-1.5 font-medium w-64"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((m) => {
                      const f = loadForm[m.file] || { context_length: 4096, gpu_layers: -1 };
                      const busy = busyFile === m.file;
                      return (
                        <tr key={m.file} className="border-t border-gray-50 hover:bg-gray-50 align-top">
                          <td className="px-2 py-1.5 font-mono text-gray-700">{m.name || m.file}</td>
                          <td className="px-2 py-1.5 text-gray-500">{m.format || <span className="text-gray-300">{t('common.none')}</span>}</td>
                          <td className="px-2 py-1.5 text-gray-500 tabular-nums">{humanBytes(m.size_bytes) || <span className="text-gray-300">{t('common.none')}</span>}</td>
                          <td className="px-2 py-1.5 text-gray-500 tabular-nums">{m.loaded ? (m.context_length ?? <span className="text-gray-300">{t('common.none')}</span>) : <span className="text-gray-300">{t('common.none')}</span>}</td>
                          <td className="px-2 py-1.5">
                            {m.loaded ? (
                              <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium text-green-700 bg-green-50 border-green-200">{t('localModels.runtime.loadedBadge')}</span>
                            ) : (
                              <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium text-gray-500 bg-gray-50 border-gray-200">{t('localModels.runtime.notLoaded')}</span>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="flex flex-col items-end gap-1.5">
                              <div className="flex items-center justify-end gap-2">
                                <Link
                                  to={`/models/hub-local/${encodeURIComponent(m.file)}`}
                                  title={t('localModels.structure')}
                                  className="text-xs font-medium text-gray-400 hover:text-indigo-600"
                                >
                                  {t('localModels.structureShort')}
                                </Link>
                                {m.loaded ? (
                                  <button
                                    type="button"
                                    onClick={() => handleUnload(m.file)}
                                    disabled={busy}
                                    title={t('localModels.runtime.unload')}
                                    className="text-gray-400 hover:text-amber-600 disabled:opacity-50"
                                  >
                                    {busy ? <Loader className="w-4 h-4 animate-spin" /> : <PowerOff className="w-4 h-4" />}
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => setOpenLoadFor((cur) => (cur === m.file ? '' : m.file))}
                                    disabled={busy}
                                    title={t('localModels.runtime.load')}
                                    className="text-gray-400 hover:text-green-600 disabled:opacity-50"
                                  >
                                    <Power className="w-4 h-4" />
                                  </button>
                                )}
                                <button
                                  type="button"
                                  onClick={() => handleDelete(m.file)}
                                  disabled={busy}
                                  title={t('common.delete')}
                                  className="text-gray-300 hover:text-red-500 disabled:opacity-50"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </div>
                              {openLoadFor === m.file && !m.loaded && (
                                <div className="flex items-center gap-1.5">
                                  <input
                                    type="number"
                                    value={f.context_length ?? 4096}
                                    onChange={(e) => setLoadForm((s) => ({ ...s, [m.file]: { ...f, context_length: e.target.value } }))}
                                    title={t('localModels.runtime.contextLength')}
                                    className={`${inputCls} w-24 py-1`}
                                  />
                                  <input
                                    type="number"
                                    value={f.gpu_layers ?? -1}
                                    onChange={(e) => setLoadForm((s) => ({ ...s, [m.file]: { ...f, gpu_layers: e.target.value } }))}
                                    title={t('localModels.runtime.gpuLayers')}
                                    className={`${inputCls} w-20 py-1`}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => handleLoad(m.file)}
                                    disabled={busy}
                                    className="px-2 py-1 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50"
                                  >
                                    {t('localModels.runtime.load')}
                                  </button>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="border-t border-gray-100 pt-3">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{t('localModels.runtime.downloadTitle')}</p>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  type="text"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  placeholder={t('localModels.runtime.repoPlaceholder')}
                  className={`${inputCls} flex-1 min-w-48 max-w-xs`}
                />
                <input
                  type="text"
                  value={revision}
                  onChange={(e) => setRevision(e.target.value)}
                  placeholder={t('localModels.runtime.revisionPlaceholder')}
                  className={`${inputCls} w-32`}
                />
                <button
                  type="button"
                  onClick={handleListFiles}
                  disabled={listing || !repo.trim()}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium disabled:opacity-50"
                >
                  {listing ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  {t('localModels.runtime.listFiles')}
                </button>
              </div>
              {hfFiles.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap mt-2">
                  <select
                    value={selectedFile}
                    onChange={(e) => setSelectedFile(e.target.value)}
                    className={`${inputCls} min-w-64`}
                  >
                    {hfFiles.map((f) => (
                      <option key={f.file} value={f.file}>{f.file} ({humanBytes(f.size_bytes)})</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={handleDownload}
                    disabled={downloading || !selectedFile}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50"
                  >
                    {downloading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                    {t('localModels.runtime.download')}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

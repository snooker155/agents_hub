import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, Loader, Trash2, AlertCircle, Download, Search, Power, PowerOff, X } from 'lucide-react';
import {
  getRuntimeStatus, getRuntimeHfFiles, downloadRuntimeModel, downloadRuntimeSpeechModel,
  loadRuntimeModel, unloadRuntimeModel, deleteRuntimeModel,
} from '../../api/localModels';
import { humanBytes } from './jobs';
import SpeechEngines from './SpeechEngines';
import SpeechPackages from './SpeechPackages';
import ModelSearch from './ModelSearch';
import { FitBadge, HardwareLine } from './FitBadge';
import { useFitText, fmtParams, fmtContext } from './fit';
import { MODEL_PRESETS, PRESET_KINDS, isSpecialKind } from './speechPresets';
import { RuntimeButtons, RuntimeState } from './RuntimeControl';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none';
const STATUS_POLL_MS = 10000;
// While the runtime is being set up or started, its state changes by the second.
const BUSY_POLL_MS = 2000;

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
 * safetensors files, plus speech models (Whisper for transcription, Piper,
 * Kokoro, Kitten and Supertonic for speech), run separately (compose profile `models`, or
 * `python deploy/models/app.py`). Three states worth telling apart:
 * not configured at all, configured but unreachable, and reachable.
 */
// The file to offer first: the largest that fits this machine with room
// (what the search calls the best quantization), else the largest that fits
// at all; a full-precision file is never the pick. Without estimates, the
// first file.
const FULL_PRECISION = /(^|[-_.])(B?F16|F32)([-_.]|$)/i;
function suggestedFile(files) {
  const usable = files.filter((f) => !FULL_PRECISION.test(f.file.split('/').pop()));
  for (const verdict of ['fits', 'tight']) {
    const hits = usable.filter((f) => f.fit?.verdict === verdict);
    if (hits.length) return hits.reduce((a, b) => (b.size_bytes > a.size_bytes ? b : a));
  }
  return files[0];
}

export default function RuntimeSection({ refreshKey, reloadKey, onJobStarted, onStatus, jobs }) {
  const { t } = useI18n();
  const toast = useToast();
  const [state, setState] = useState(null);
  // A reload the person asked for (the Refresh button); the first load and a
  // refreshKey change are derived from `loadedRefresh` below.
  const [reloading, setReloading] = useState(false);
  const [loadedRefresh, setLoadedRefresh] = useState(null);
  const loading = reloading || loadedRefresh !== refreshKey;
  const [busyFile, setBusyFile] = useState('');
  const [loadForm, setLoadForm] = useState({}); // file -> { context_length, gpu_layers }
  const [openLoadFor, setOpenLoadFor] = useState('');

  const [repo, setRepo] = useState('');
  const [revision, setRevision] = useState('');
  const [hfFiles, setHfFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState('');
  const [hfPackages, setHfPackages] = useState([]);
  // What the listing said about the repo's model and this machine.
  const [hfModel, setHfModel] = useState(null);
  const [hfHardware, setHfHardware] = useState(null);
  // The search above already says what the estimates assume.
  const [searchShowsHardware, setSearchShowsHardware] = useState(false);
  const [selectedPackage, setSelectedPackage] = useState('');
  const [listing, setListing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  // The repo the field holds right now, read when a listing comes back: an
  // answer for a repo the field no longer names is dropped.
  const repoRef = useRef('');

  const pollRef = useRef(null);
  const fitText = useFitText();
  // The status also carries the runtime's call counts, which the usage card
  // below shows: one request for both.
  const onStatusRef = useRef(onStatus);
  useEffect(() => { onStatusRef.current = onStatus; }, [onStatus]);

  // Promise chains, not async functions: the React Compiler lint treats an
  // async function called from an effect as a synchronous setState.
  const refresh = useCallback(() => getRuntimeStatus()
    .then(
      ({ data }) => data || { configured: false },
      (e) => ({ configured: true, ok: false, error: errorDetail(e) }),
    )
    .then((next) => {
      setState(next);
      onStatusRef.current?.(next);
    }), []);

  const load = useCallback((silent) => {
    if (silent) return refresh();
    setReloading(true);
    return refresh().then(() => setReloading(false));
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    refresh().then(() => { if (!cancelled) setLoadedRefresh(refreshKey); });
    return () => { cancelled = true; };
  }, [refresh, refreshKey]);
  // A reload asked for from outside (the usage card's Refresh) runs without
  // this section's spinner.
  useEffect(() => { if (reloadKey) refresh(); }, [refresh, reloadKey]);

  // Poll every 10s while this section stays mounted (i.e. while the Local
  // tab is open), independent of the shared job list's own faster polling.
  const managedState = state?.managed?.state;
  const busyRuntime = managedState === 'preparing' || managedState === 'starting';
  useEffect(() => {
    pollRef.current = setInterval(() => load(true), busyRuntime ? BUSY_POLL_MS : STATUS_POLL_MS);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [load, busyRuntime]);

  // `wanted` is the package a preset asks for, `wantedFile` the GGUF file,
  // chosen once the list is in.
  const clearListing = () => {
    setHfFiles([]);
    setSelectedFile('');
    setHfPackages([]);
    setSelectedPackage('');
    setHfModel(null);
    setHfHardware(null);
  };

  // The files and speech packages listed belong to the repo they were listed
  // for: once the field names another repo (or none), they go, along with
  // the package filter, which lives in SpeechPackages and resets on unmount.
  const changeRepo = (value) => {
    if (value.trim() !== repoRef.current) clearListing();
    repoRef.current = value.trim();
    setRepo(value);
  };

  const listFiles = async (r, rev, wanted, wantedFile) => {
    if (!r) return;
    setListing(true);
    clearListing();
    try {
      const { data } = await getRuntimeHfFiles(r, rev || undefined);
      if (repoRef.current !== r) return;
      const files = (data?.files || []).filter((f) => f.file?.toLowerCase().endsWith('.gguf'));
      const packages = data?.packages || [];
      setHfFiles(files);
      setHfPackages(packages);
      setHfModel(data?.model && Object.keys(data.model).length ? data.model : null);
      setHfHardware(data?.hardware || null);
      if (files.length > 0) setSelectedFile((files.find((f) => f.file === wantedFile) || suggestedFile(files)).file);
      const pick = packages.find((p) => p.name === wanted) || packages.find((p) => !p.downloaded) || packages[0];
      if (pick) setSelectedPackage(pick.name);
      if (files.length === 0 && packages.length === 0) toast.error(t('localModels.runtime.noGgufFiles'));
    } catch (e) {
      toast.error(t('localModels.runtime.listFilesFailed'), errorDetail(e));
    } finally {
      setListing(false);
    }
  };

  const handleListFiles = () => listFiles(repo.trim(), revision.trim());

  // Empties the form: the repo, its revision and what was listed for it.
  const handleClear = () => {
    changeRepo('');
    setRevision('');
  };

  // A repo picked from the search: its files listed at once.
  const handlePick = (r) => {
    changeRepo(r);
    setRevision('');
    listFiles(r, '');
  };

  const handlePreset = (preset) => {
    changeRepo(preset.repo);
    setRevision('');
    listFiles(preset.repo, '', preset.package, preset.file);
  };

  const handleDownloadPackage = async (name) => {
    const r = repo.trim();
    if (!r || !name) return;
    setDownloading(true);
    try {
      await downloadRuntimeSpeechModel(r, name, revision.trim() || undefined);
      toast.success(t('localModels.runtime.downloadStarted', { file: name }));
      setHfPackages((list) => list.map((p) => (p.name === name ? { ...p, downloaded: true } : p)));
      onJobStarted?.();
    } catch (e) {
      toast.error(t('localModels.runtime.downloadFailed'), errorDetail(e));
    } finally {
      setDownloading(false);
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

  const handleLoad = async (file, speech) => {
    const f = speech ? {} : (loadForm[file] || {});
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
  const managed = state?.managed || null;
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
          {managed && <span className="text-xs text-gray-400">{t('localModels.runtime.managedBadge')}</span>}
        </div>
        {configured && (
          <div className="flex items-center gap-1.5 shrink-0">
          {managedState === 'running' && <RuntimeButtons managed={managed} onChanged={() => load(true)} />}
          <button
            type="button"
            onClick={() => load(false)}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors shrink-0"
          >
            {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {t('common.refresh')}
          </button>
          </div>
        )}
      </div>
      <p className="px-4 pt-2 text-xs text-gray-500">{t('localModels.runtime.intro')}</p>

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
        ) : managed && managedState !== 'running' ? (
          <RuntimeState managed={managed} onChanged={() => load(true)} />
        ) : !ok ? (
          <p className="flex items-center gap-1.5 text-sm text-red-700"><AlertCircle className="w-4 h-4 shrink-0" /> {state?.error || t('localModels.runtime.unreachable')}</p>
        ) : (
          <>
            {managed?.stale && (
              <div className="flex items-center gap-3 flex-wrap text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-3" data-testid="runtime-stale">
                <span>{t('localModels.runtime.stale')}</span>
              </div>
            )}
            <SpeechEngines engines={state?.engines} jobs={jobs} onJobStarted={onJobStarted} />

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
                      const speech = isSpecialKind(m.kind);
                      // Speech, image and MLX models take no context length: they load at once.
                      const quickLoad = speech || m.engine === 'mlx';
                      return (
                        <tr key={m.file} className="border-t border-gray-50 hover:bg-gray-50 align-top" data-testid={`runtime-model-${m.file}`}>
                          <td className="px-2 py-1.5">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="font-mono text-gray-700">{m.name || m.file}</span>
                              {speech && (
                                <span className="px-1.5 py-0.5 rounded border text-[10px] font-medium text-indigo-700 bg-indigo-50 border-indigo-200">
                                  {t(`localModels.speech.kinds.${m.kind}`)}
                                </span>
                              )}
                              {speech && m.voices?.length > 0 && (
                                <span className="text-[11px] text-gray-400">{t('localModels.speech.voices', { count: m.voices.length })}</span>
                              )}
                            </div>
                            {m.note && <p className="text-[11px] text-amber-700 mt-0.5 max-w-md">{m.note}</p>}
                          </td>
                          <td className="px-2 py-1.5 text-gray-500">
                            {m.format || <span className="text-gray-300">{t('common.none')}</span>}
                            {speech && m.engine && <span className="text-gray-400"> · {t(`localModels.speech.engines.${m.engine}`, { defaultValue: m.engine })}</span>}
                          </td>
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
                                {!speech && (
                                  <Link
                                    to={`/models/hub-local/${encodeURIComponent(m.file)}`}
                                    title={t('localModels.structure')}
                                    className="text-xs font-medium text-gray-400 hover:text-indigo-600"
                                  >
                                    {t('localModels.structureShort')}
                                  </Link>
                                )}
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
                                                    onClick={() => (quickLoad ? handleLoad(m.file, true) : setOpenLoadFor((cur) => (cur === m.file ? '' : m.file)))}
                                    disabled={busy || (quickLoad && m.loadable === false)}
                                    title={t('localModels.runtime.load')}
                                    data-testid={`runtime-load-${m.file}`}
                                    className="text-gray-400 hover:text-green-600 disabled:opacity-50"
                                  >
                                    {busy && quickLoad ? <Loader className="w-4 h-4 animate-spin" /> : <Power className="w-4 h-4" />}
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
              <ModelSearch onPick={handlePick} onShown={setSearchShowsHardware} disabled={listing} />
              <div className="space-y-1.5 mb-2 text-xs" data-testid="speech-presets">
                {PRESET_KINDS.map((kind) => (
                  <div key={kind} className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-gray-500 w-28 shrink-0">{t(`localModels.presets.${kind}`)}</span>
                    {MODEL_PRESETS.filter((p) => p.kind === kind
                      // A preset for an engine this machine cannot run (MLX off Apple silicon) is left out.
                      && (!p.engine || !state?.engines || p.engine in state.engines)).map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => handlePreset(preset)}
                        disabled={listing}
                        title={`${preset.repo}: ${preset.file || preset.package}`}
                        className="px-2 py-0.5 rounded-full border border-gray-200 text-gray-600 hover:bg-gray-100 disabled:opacity-50"
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <input
                  type="text"
                  value={repo}
                  onChange={(e) => changeRepo(e.target.value)}
                  placeholder={t('localModels.runtime.repoPlaceholder')}
                  className={`${inputCls} flex-1 min-w-48 max-w-xs`}
                />
                <input
                  type="text"
                  value={revision}
                  onChange={(e) => setRevision(e.target.value)}
                  placeholder={t('localModels.runtime.revisionPlaceholder')}
                  className={`${inputCls} w-56 max-w-full`}
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
                <button
                  type="button"
                  onClick={handleClear}
                  disabled={!repo && !revision}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium disabled:opacity-50"
                  data-testid="hf-clear"
                >
                  <X className="w-3.5 h-3.5" />
                  {t('common.clear')}
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
                      <option key={f.file} value={f.file}>
                        {f.file} ({humanBytes(f.size_bytes)}){f.fit ? ` · ${fitText(f.fit)}` : ''}
                      </option>
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
                  <FitBadge fit={hfFiles.find((f) => f.file === selectedFile)?.fit} testId="selected-file-fit" />
                </div>
              )}
              {hfFiles.length > 0 && (hfModel || (hfHardware && !searchShowsHardware)) && (
                <div className="mt-1.5 space-y-0.5" data-testid="hf-model-info">
                  {hfModel && (
                    <p className="text-xs text-gray-500">
                      {[hfModel.params && fmtParams(hfModel.params), hfModel.moe && t('localModels.modelSearch.moe'),
                        hfModel.license, hfModel.context_length && t('localModels.modelSearch.context', { n: fmtContext(hfModel.context_length) })]
                        .filter(Boolean).join(' · ')}
                    </p>
                  )}
                  {!searchShowsHardware && <HardwareLine hw={hfHardware} />}
                </div>
              )}
              {hfPackages.length > 0 && (
                <SpeechPackages
                  packages={hfPackages}
                  selected={selectedPackage}
                  onSelect={setSelectedPackage}
                  onDownload={handleDownloadPackage}
                  downloading={downloading}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

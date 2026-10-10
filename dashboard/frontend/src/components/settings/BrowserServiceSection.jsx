import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircle, AlertCircle, Loader, RefreshCw, Play, Square, Download, Eye, EyeOff, Wand2, Save, Terminal,
} from 'lucide-react';

import {
  getBrowserService, configureBrowserService, startBrowserService, stopBrowserService,
  installBrowserChromium, getBrowserServiceJob, getBrowserServiceLog,
} from '../../api/browser';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

/*
 * Settings → Browser: the browser service (deploy/browser/, headless Chromium
 * behind a token) configured and run from here, without a restart. The hub
 * reads the address and the token live, so saving applies at once. The
 * service itself runs either as a subprocess of the hub's own Python
 * ("local") or as a container ("container", only offered while docker
 * answers). Chromium for the local mode is installed from here too.
 * See docs/browser.md.
 */

const POLL_MS = 4000;
const JOB_POLL_MS = 1500;

function Badge({ ok, label, warn }) {
  const cls = ok ? 'bg-green-50 text-green-700 border-green-200'
    : warn ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-gray-50 text-gray-500 border-gray-200';
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {ok ? <CheckCircle className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
      {label}
    </span>
  );
}

export default function BrowserServiceSection() {
  const { t } = useI18n();
  const toast = useToast();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [mode, setMode] = useState('local');
  const [busy, setBusy] = useState('');
  const [job, setJob] = useState(null);
  const [log, setLog] = useState('');
  const [showLog, setShowLog] = useState(false);
  const dirty = useRef(false);

  // A promise chain, not an async function: the React Compiler lint treats an
  // async function called from an effect as a synchronous setState.
  const load = useCallback(() => getBrowserService()
    .then(({ data }) => {
      setStatus(data);
      setError('');
      if (!dirty.current) {
        setUrl(data.url || '');
        setMode(data.mode || 'local');
      }
    })
    .catch((e) => {
      setError(errorDetail(e) || t('settings.browser.loadFailed'));
    }), [t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  // A running job (Chromium install, image build) is polled until it ends.
  useEffect(() => {
    if (!job || job.state !== 'running') return undefined;
    const timer = setInterval(async () => {
      try {
        const { data } = await getBrowserServiceJob(job.id);
        setJob(data);
        if (data.state !== 'running') {
          if (data.state === 'done') toast.success(t(`settings.browser.jobDone.${data.kind}`));
          else toast.error(t(`settings.browser.jobFailed.${data.kind}`));
          load();
        }
      } catch {
        // keep polling; a transient error is not the job's end
      }
    }, JOB_POLL_MS);
    return () => clearInterval(timer);
  }, [job, load, t, toast]);

  useEffect(() => {
    if (!showLog) return undefined;
    const fetchLog = () => getBrowserServiceLog(300).then(({ data }) => setLog(data.text || '')).catch(() => {});
    fetchLog();
    const timer = setInterval(fetchLog, 3000);
    return () => clearInterval(timer);
  }, [showLog]);

  const act = async (name, fn, okMessage) => {
    setBusy(name);
    try {
      const { data } = await fn();
      if (data?.job) setJob(data.job);
      if (data?.status) setStatus(data.status);
      else if (data?.configured !== undefined) setStatus(data);
      if (data?.token) { setToken(data.token); setShowToken(true); }
      if (okMessage) toast.success(okMessage);
      dirty.current = false;
      await load();
    } catch (e) {
      toast.error(t('settings.browser.actionFailed'), errorDetail(e));
    } finally {
      setBusy('');
    }
  };

  const save = () => act('save', () => configureBrowserService({
    url: url.trim(), ...(token ? { token } : {}), mode,
  }), t('settings.browser.saved'));

  if (error) return <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>;
  if (!status) return <div className="text-sm text-gray-400 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('settings.browser.loading')}</div>;

  const dockerOk = !!status.docker?.available;
  const runningHere = status.local?.running || status.container?.running;
  const reachable = !!status.service?.reachable;
  const pw = status.playwright || {};
  const runningJob = job?.state === 'running' ? job : (status.jobs || [])[0] || null;

  return (
    <div className="space-y-5">
      <SectionCard
        title={t('settings.browser.title')}
        actions={(
          <div className="flex items-center gap-2">
            <Badge ok={status.configured} warn={!status.configured} label={status.configured ? t('settings.browser.configured') : t('settings.browser.notConfigured')} />
            <Badge ok={reachable} warn={status.configured && !reachable} label={reachable ? t('settings.browser.reachable') : t('settings.browser.unreachable')} />
            <button type="button" onClick={load} className="text-gray-400 hover:text-indigo-600 p-1" title={t('settings.browser.refresh')}>
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        )}
      >
        <p className="text-xs text-gray-500">{t('settings.browser.intro')}</p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.browser.url')}</label>
            <input className={inputCls} value={url} placeholder="http://127.0.0.1:3000"
              onChange={(e) => { dirty.current = true; setUrl(e.target.value); }} />
            <p className="text-xs text-gray-400 mt-1">{t('settings.browser.urlHint')}</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('settings.browser.token')}</label>
            <div className="flex items-center gap-1">
              <input className={inputCls} type={showToken ? 'text' : 'password'} value={token}
                placeholder={status.has_token ? '••••••••••••' : t('settings.browser.tokenPlaceholder')}
                onChange={(e) => { dirty.current = true; setToken(e.target.value); }} />
              <button type="button" onClick={() => setShowToken((v) => !v)} className="p-2 text-gray-400 hover:text-indigo-600" title={t('settings.browser.showToken')}>
                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
              <button type="button" disabled={!!busy} onClick={() => act('gen', () => configureBrowserService({ generate_token: true }), t('settings.browser.tokenGenerated'))}
                className="p-2 text-gray-400 hover:text-indigo-600 disabled:opacity-50" title={t('settings.browser.generate')}>
                <Wand2 className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-1">{status.has_token ? t('settings.browser.tokenSet') : t('settings.browser.tokenHint')}</p>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">{t('settings.browser.mode')}</label>
          <div className="flex flex-col sm:flex-row gap-3">
            {[
              { id: 'local', disabled: !pw.installed },
              { id: 'container', disabled: !dockerOk },
            ].map((opt) => (
              <label key={opt.id} className={`flex-1 border rounded-lg p-3 cursor-pointer ${mode === opt.id ? 'border-indigo-400 bg-indigo-50/40' : 'border-gray-200'} ${opt.disabled ? 'opacity-60 cursor-not-allowed' : ''}`}>
                <div className="flex items-center gap-2">
                  <input type="radio" name="browser-mode" value={opt.id} checked={mode === opt.id} disabled={opt.disabled}
                    onChange={() => { dirty.current = true; setMode(opt.id); }} />
                  <span className="text-sm font-medium text-gray-800">{t(`settings.browser.modes.${opt.id}`)}</span>
                </div>
                <p className="text-xs text-gray-500 mt-1">{t(`settings.browser.modeHints.${opt.id}`)}</p>
                {opt.id === 'container' && !dockerOk && (
                  <p className="text-xs text-amber-700 mt-1">{t('settings.browser.dockerDown')} {status.docker?.reason ? `(${status.docker.reason})` : ''}</p>
                )}
                {opt.id === 'local' && !pw.installed && (
                  <p className="text-xs text-amber-700 mt-1">{t('settings.browser.playwrightMissing')}</p>
                )}
              </label>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={save} disabled={!!busy}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {busy === 'save' ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} {t('settings.browser.save')}
          </button>
          <span className="text-xs text-gray-400">{t('settings.browser.appliesAtOnce')}</span>
        </div>
      </SectionCard>

      <SectionCard
        title={t('settings.browser.runTitle')}
        actions={(
          <Badge ok={!!runningHere} warn={!runningHere} label={
            status.local?.running ? t('settings.browser.runningLocal', { pid: status.local.pid })
              : status.container?.running ? t('settings.browser.runningContainer')
                : t('settings.browser.notRunning')
          } />
        )}
      >
        <p className="text-xs text-gray-500">{t('settings.browser.runIntro')}</p>
        <div className="flex flex-wrap items-center gap-2">
          {runningHere ? (
            <button type="button" disabled={!!busy} onClick={() => act('stop', stopBrowserService, t('settings.browser.stopped'))}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-50">
              {busy === 'stop' ? <Loader className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />} {t('settings.browser.stop')}
            </button>
          ) : (
            <button type="button" disabled={!!busy || !status.has_token || (status.mode === 'container' && !dockerOk) || !!runningJob}
              onClick={() => act('start', startBrowserService, t('settings.browser.started'))}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
              {busy === 'start' ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {status.mode === 'container' ? t('settings.browser.startContainer') : t('settings.browser.startLocal')}
            </button>
          )}
          {!status.has_token && <span className="text-xs text-amber-700">{t('settings.browser.needToken')}</span>}
          {status.mode === 'container' && dockerOk && !status.container?.image_built && (
            <span className="text-xs text-gray-500">{t('settings.browser.imageWillBuild')}</span>
          )}
          <button type="button" onClick={() => setShowLog((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 hover:bg-gray-50">
            <Terminal className="w-3.5 h-3.5" /> {showLog ? t('settings.browser.hideLog') : t('settings.browser.showLog')}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600">
          <span>{t('settings.browser.chromium')}:</span>
          <Badge ok={pw.chromium === true} warn={pw.chromium === false} label={
            !pw.installed ? t('settings.browser.playwrightMissingShort')
              : pw.chromium ? t('settings.browser.chromiumInstalled', { rev: pw.revision || '' })
                : pw.chromium === false ? t('settings.browser.chromiumMissing') : t('settings.browser.chromiumUnknown')
          } />
          <button type="button" disabled={!!busy || !pw.installed || !!runningJob}
            onClick={() => act('install', installBrowserChromium)}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50">
            <Download className="w-3.5 h-3.5" /> {pw.chromium ? t('settings.browser.reinstallChromium') : t('settings.browser.installChromium')}
          </button>
          <span className="text-gray-400">{t('settings.browser.python')}: <code className="bg-gray-100 rounded px-1">{status.python}</code></span>
        </div>

        {runningJob && (
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 text-xs text-gray-600 flex items-center gap-2">
              <Loader className="w-3 h-3 animate-spin" /> {t(`settings.browser.jobRunning.${runningJob.kind}`)}
            </div>
            <pre className="text-[11px] leading-4 font-mono p-3 overflow-auto bg-gray-950 text-gray-100" style={{ maxHeight: 200 }}>{runningJob.log || '…'}</pre>
          </div>
        )}
        {job && job.state !== 'running' && (
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 text-xs text-gray-600">
              {t(`settings.browser.jobDone.${job.kind}`)}{job.state === 'failed' ? ` (${t('settings.browser.failed')})` : ''}
            </div>
            <pre className="text-[11px] leading-4 font-mono p-3 overflow-auto bg-gray-950 text-gray-100" style={{ maxHeight: 200 }}>{job.log || ''}</pre>
          </div>
        )}
        {showLog && (
          <pre className="text-[11px] leading-4 font-mono p-3 overflow-auto bg-gray-950 text-gray-100 rounded-lg" style={{ height: 240 }}>{log || t('settings.browser.noLog')}</pre>
        )}
      </SectionCard>
    </div>
  );
}

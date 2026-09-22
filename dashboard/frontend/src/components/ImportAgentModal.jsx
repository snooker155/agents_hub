import { useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  Info,
  Loader,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import {
  discardAgentImport,
  getAgentImportRequirements,
  inspectAgentRepo,
  registerImportedAgent,
} from '../api';

const inputCls =
  'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-400 focus:outline-none';

/**
 * Import an agent that already lives in its own repository.
 *
 * Two deliberate properties of this dialog:
 *
 *  - The requirements are shown *before* the first check, not only as failures
 *    afterwards. An operator can read what their repo must provide while they
 *    are still deciding what to paste.
 *  - An agent that fails its checks is still importable. The button changes
 *    wording rather than disabling itself, because a half-configured agent
 *    parked in the list with its reasons attached is more useful than one that
 *    was refused and forgotten.
 *
 * onDone(result) receives the backend's register response.
 */
export default function ImportAgentModal({ workspace = '', onClose, onDone }) {
  const { t } = useI18n();
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('');

  const [requirements, setRequirements] = useState(null);
  const [showRequirements, setShowRequirements] = useState(true);
  const [showManifest, setShowManifest] = useState(false);

  const [inspection, setInspection] = useState(null);
  const [checking, setChecking] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');

  // Editable once an inspection has come back; pre-filled from the manifest.
  const [form, setForm] = useState({ id: '', name: '', description: '', url: '' });

  useEffect(() => {
    getAgentImportRequirements()
      .then(({ data }) => setRequirements(data))
      .catch(() => setRequirements(null));
  }, []);

  // A staged clone left on the server when the dialog is closed without
  // importing would linger until the sweeper runs; drop it eagerly instead.
  const closeAndCleanup = () => {
    if (inspection?.token && !importing) discardAgentImport(inspection.token).catch(() => {});
    onClose();
  };

  const runCheck = async () => {
    if (!repoUrl.trim()) return;
    setChecking(true);
    setError('');
    // Re-checking supersedes the previous staged clone.
    if (inspection?.token) discardAgentImport(inspection.token).catch(() => {});
    try {
      const { data } = await inspectAgentRepo({
        repo_url: repoUrl.trim(),
        branch: branch.trim() || undefined,
        agent_id: form.id.trim() || undefined,
        url: form.url.trim() || undefined,
        workspace: workspace || undefined,
      });
      setInspection(data);
      setForm({
        id: data.suggested.id || '',
        name: data.suggested.name || '',
        description: data.suggested.description || '',
        url: data.suggested.url || '',
      });
      setShowRequirements(!data.report.runnable);
    } catch (e) {
      setInspection(null);
      setError(e.response?.data?.detail || e.message);
    } finally {
      setChecking(false);
    }
  };

  // Re-run the checks against edited fields without re-cloning is not possible
  // server-side (the report is computed from the clone), so this simply repeats
  // the inspection — cheap, and it keeps one source of truth for the verdict.
  const recheckWithEdits = () => runCheck();

  const doImport = async () => {
    if (!inspection) return;
    setImporting(true);
    setError('');
    try {
      const { data } = await registerImportedAgent({
        token: inspection.token,
        repo_url: repoUrl.trim(),
        agent_id: form.id.trim(),
        name: form.name.trim() || undefined,
        description: form.description.trim() || undefined,
        url: form.url.trim() || undefined,
        branch: branch.trim() || undefined,
        workspace: workspace || undefined,
      });
      onDone?.(data);
      onClose();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
      setImporting(false);
    }
  };

  const report = inspection?.report;
  const runnable = !!report?.runnable;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 border-b border-gray-100">
          <div>
            <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
              <Download className="w-5 h-5 text-indigo-600" />
              {t('importAgentModal.importAgentFromRepository')}
            </h3>
            <p className="text-sm text-gray-500 mt-0.5">
              Bring in an agent that already exists and is already tested. Its code stays in its
              own repository and its own process — this hub talks to it over HTTP.
            </p>
          </div>
          <button onClick={closeAndCleanup} className="text-gray-400 hover:text-gray-600 p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5 space-y-5">
          {/* ── Source ─────────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_180px] gap-3">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                {t('importAgentModal.sourceUrl')}
              </label>
              <input
                className={inputCls}
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runCheck()}
                placeholder={t('importAgentModal.httpsGithubComOwnerMy')}
              />
              {/* An A2A agent is already running and describes itself, so its
                  card replaces the repository entirely. */}
              <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                {t('importAgentModal.sourceUrlHint')}
              </p>
            </div>
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                {t('importAgentModal.branch')}
              </label>
              <input
                className={inputCls}
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder={t('importAgentModal.default')}
              />
            </div>
          </div>

          <button
            onClick={runCheck}
            disabled={!repoUrl.trim() || checking}
            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-bold flex items-center gap-2 hover:bg-indigo-700 disabled:opacity-50"
          >
            {checking ? <Loader className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            {checking ? t('importAgentModal.cloningAndChecking') : t('importAgentModal.checkRepository')}
          </button>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-800 rounded-lg px-4 py-3 text-sm">
              {error}
            </div>
          )}

          {/* ── Requirements (visible before and after the check) ───────── */}
          {requirements && (
            <RequirementsPanel
              requirements={requirements}
              open={showRequirements}
              onToggle={() => setShowRequirements((v) => !v)}
              showManifest={showManifest}
              onToggleManifest={() => setShowManifest((v) => !v)}
            />
          )}

          {/* ── Readiness report ───────────────────────────────────────── */}
          {report && (
            <div className="space-y-4">
              <div
                className={`rounded-xl px-4 py-3 border flex items-start gap-3 ${
                  runnable
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                    : 'bg-amber-50 border-amber-200 text-amber-900'
                }`}
              >
                {runnable ? (
                  <Check className="w-5 h-5 mt-0.5 shrink-0" />
                ) : (
                  <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" />
                )}
                <div className="text-sm">
                  <p className="font-bold">{report.summary}</p>
                  {!runnable && (
                    <p className="mt-1 opacity-90">
                      You can still import it. The agent will appear in the list marked
                      “needs setup”, carrying the list below, and can be re-checked from its page
                      once you have fixed things.
                    </p>
                  )}
                </div>
              </div>

              <ul className="space-y-2">
                {report.checks.map((c) => (
                  <CheckRow key={c.id} check={c} />
                ))}
              </ul>

              {/* ── Fields ───────────────────────────────────────────── */}
              <div className="border-t border-gray-100 pt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label={t('importAgentModal.agentId')} hint={t('importAgentModal.uniqueWithinThisHub')}>
                  <input
                    className={inputCls}
                    value={form.id}
                    onChange={(e) => setForm({ ...form, id: e.target.value })}
                    placeholder="my-agent"
                  />
                </Field>
                <Field label={t('importAgentModal.displayName')}>
                  <input
                    className={inputCls}
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </Field>
                <Field
                  label={t('importAgentModal.endpointUrl')}
                  hint={t('importAgentModal.baseUrlOfTheRunning')}
                  wide
                >
                  <div className="flex gap-2">
                    <input
                      className={inputCls}
                      value={form.url}
                      onChange={(e) => setForm({ ...form, url: e.target.value })}
                      placeholder="http://localhost:8410"
                    />
                    <button
                      onClick={recheckWithEdits}
                      disabled={checking}
                      title={t('importAgentModal.reRunTheChecksWith')}
                      className="shrink-0 border border-gray-300 rounded-lg px-3 text-sm text-gray-600 hover:bg-gray-50 flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${checking ? 'animate-spin' : ''}`} />
                      {t('importAgentModal.reCheck')}
                    </button>
                  </div>
                </Field>
                <Field label={t('importAgentModal.description')} wide>
                  <textarea
                    className={`${inputCls} h-16 resize-none`}
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </Field>
              </div>

              {inspection.commit && (
                <p className="text-xs text-gray-400">
                  {t('importAgentModal.importingCommit')} <code className="text-gray-600">{inspection.commit}</code>
                  {inspection.manifest?.path && (
                    <> · {t('importAgentModal.manifest')} <code className="text-gray-600">{inspection.manifest.path}</code></>
                  )}
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-100">
          <button onClick={closeAndCleanup} className="px-4 py-2 text-sm text-gray-500 font-medium">
            {t('importAgentModal.cancel')}
          </button>
          <button
            onClick={doImport}
            disabled={!inspection || !form.id.trim() || importing}
            className={`px-6 py-2 rounded-lg text-sm font-bold text-white shadow-md flex items-center gap-2 disabled:opacity-50 ${
              runnable ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-amber-600 hover:bg-amber-700'
            }`}
          >
            {importing ? <Loader className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            {!inspection || runnable
              ? t('importAgentModal.importAgent')
              : t('importAgentModal.importAnyway')}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, hint, wide = false, children }) {
  return (
    <div className={wide ? 'sm:col-span-2' : ''}>
      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
        {label}
        {hint && <span className="normal-case font-normal text-gray-400"> — {hint}</span>}
      </label>
      {children}
    </div>
  );
}

function CheckRow({ check }) {
  const { t } = useI18n();
  const failed = !check.ok;
  const blocking = failed && check.required;
  return (
    <li
      className={`rounded-lg border px-3 py-2 text-sm ${
        !failed
          ? 'border-gray-100 bg-gray-50'
          : blocking
          ? 'border-red-200 bg-red-50'
          : 'border-amber-200 bg-amber-50'
      }`}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">
          {!failed ? (
            <Check className="w-4 h-4 text-emerald-600" />
          ) : blocking ? (
            <X className="w-4 h-4 text-red-600" />
          ) : (
            <Info className="w-4 h-4 text-amber-600" />
          )}
        </span>
        <div className="min-w-0">
          <p className="font-semibold text-gray-800">
            {check.label}
            {failed && !check.required && (
              <span className="ml-2 text-[11px] font-medium text-amber-700 uppercase tracking-wide">
                {t('importAgentModal.recommended')}
              </span>
            )}
          </p>
          {check.detail && <p className="text-gray-600 break-words">{check.detail}</p>}
          {check.fix && <p className="text-gray-500 mt-1 italic">{check.fix}</p>}
        </div>
      </div>
    </li>
  );
}

function RequirementsPanel({ requirements, open, onToggle, showManifest, onToggleManifest }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const manifestJson = JSON.stringify(requirements.example_manifest, null, 2);

  const copy = () => {
    navigator.clipboard?.writeText(manifestJson).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="border border-indigo-100 bg-indigo-50/40 rounded-xl">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <span className="text-sm font-bold text-indigo-900 flex items-center gap-2">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          What the repository must provide
        </span>
        <span className="text-[11px] uppercase tracking-wider text-indigo-400 font-semibold">
          {requirements.schema}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          <ul className="space-y-2">
            {requirements.requirements.map((r) => (
              <li key={r.id} className="text-sm">
                <p className="font-semibold text-gray-800">{r.title}</p>
                <p className="text-gray-600">{r.detail}</p>
              </li>
            ))}
          </ul>

          <div className="text-sm bg-white border border-indigo-100 rounded-lg p-3">
            <p className="font-semibold text-gray-800 mb-1">{t('importAgentModal.theHttpContract')}</p>
            <pre className="text-xs text-gray-700 overflow-x-auto leading-relaxed">
{`POST <run_path>                      required
  {"prompt": "…", "run_id": "…", "workspace": "…"}
  -> {"ok": true, "output": "…", "error": null}

POST <stream_path>                   optional — live output
  -> NDJSON / SSE frames, one JSON object each:
     {"type": "token",  "token": "…"}
     {"type": "usage",  "prompt_tokens": N, "completion_tokens": N}
     {"type": "done",   "ok": true, "output": "…"}

GET  <health_path>   -> any 2xx/3xx`}
            </pre>
          </div>

          <div>
            <button
              onClick={onToggleManifest}
              className="text-xs font-semibold text-indigo-700 hover:text-indigo-900 flex items-center gap-1"
            >
              {showManifest ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              {t('importAgentModal.exampleManifest', { file: requirements.manifest_filenames[0] })}
            </button>
            {showManifest && (
              <div className="relative mt-2">
                <pre className="bg-gray-900 text-gray-100 rounded-lg p-3 text-[11px] overflow-x-auto leading-relaxed">
                  <code>{manifestJson}</code>
                </pre>
                <button
                  onClick={copy}
                  className="absolute top-2 right-2 p-1.5 rounded-lg bg-white/10 text-gray-300 hover:bg-white/20"
                  title={t('importAgentModal.copy')}
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
              </div>
            )}
          </div>

          <p className="text-xs text-gray-500 flex items-start gap-1.5">
            <ExternalLink className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            {t('importAgentModal.workedExampleBefore')}{' '}
            <code className="text-gray-600">examples/imported-agents/aider-agenthub</code>{t('importAgentModal.workedExampleAfter')}
          </p>
        </div>
      )}
    </div>
  );
}

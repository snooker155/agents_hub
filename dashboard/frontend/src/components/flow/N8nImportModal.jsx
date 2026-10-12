import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, FileJson, Loader2, Server, Upload, Workflow, X } from 'lucide-react';
import { importN8nFlow, listN8nWorkflows } from '../../api';
import { useI18n } from '../../i18n';

const INPUT = 'w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition';

function Choice({ active, onClick, icon: Icon, title, hint, testId }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={`flex flex-1 items-start gap-3 rounded-2xl border px-4 py-3 text-left transition ${
        active ? 'border-cyan-400 bg-cyan-50' : 'border-slate-200 bg-white hover:border-cyan-300'
      }`}
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${active ? 'text-cyan-600' : 'text-slate-400'}`} />
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-slate-900">{title}</span>
        <span className="block text-xs leading-5 text-slate-500">{hint}</span>
      </span>
    </button>
  );
}

// Bring an n8n workflow in as a hub flow, from the connected n8n instance or
// a pasted export. "Run in n8n" makes one node that starts the workflow
// where it is; "Convert" makes its steps hub nodes, every step an agent slot
// to fill in the editor. The backend's report says what became what
// (flow/n8n_import.py) and is shown before the flow is opened.
export default function N8nImportModal({ workspace, onClose, onOpen }) {
  const { t } = useI18n();
  const fileRef = useRef(null);
  const [source, setSource] = useState('instance'); // instance | json
  const [mode, setMode] = useState('convert'); // convert | run
  const [workflows, setWorkflows] = useState(null);
  const [listError, setListError] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [json, setJson] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  useEffect(() => {
    let alive = true;
    listN8nWorkflows(workspace || undefined)
      .then((r) => { if (alive) setWorkflows(Array.isArray(r.data) ? r.data : []); })
      .catch((e) => {
        if (!alive) return;
        setWorkflows([]);
        setListError(e.response?.data?.detail || e.message || '');
        // Nothing to list: start on the paste tab instead.
        setSource('json');
      });
    return () => { alive = false; };
  }, [workspace]);

  const picked = (workflows || []).find((w) => w.id === workflowId);

  const pickFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setJson(await file.text());
    setError('');
  };

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const { data } = await importN8nFlow({
        mode,
        workspace: workspace || undefined,
        name: name.trim() || undefined,
        ...(source === 'instance' ? { workflow_id: workflowId } : { workflow: json }),
      });
      setResult(data);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || t('n8nImport.failed'));
    } finally {
      setBusy(false);
    }
  };

  const ready = source === 'instance' ? !!workflowId : !!json.trim();
  const report = result?.report;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
          <div className="flex items-center gap-2">
            <Workflow className="h-5 w-5 text-rose-500" />
            <h2 className="text-lg font-bold text-slate-900">{t('n8nImport.title')}</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        {result ? (
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-6" data-testid="n8n-import-report">
            <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              <div className="text-sm text-emerald-800">
                {mode === 'run'
                  ? t('n8nImport.doneRun', { name: result.flow.name })
                  : t('n8nImport.doneConvert', { name: result.flow.name, count: report.converted.length })}
              </div>
            </div>
            {report.converted.length ? (
              <div className="space-y-1.5">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t('n8nImport.converted')}</div>
                {report.converted.map((row) => (
                  <div key={row.name} className="flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm">
                    <span className="truncate text-slate-800">{row.name}</span>
                    <span className="shrink-0 text-xs text-slate-500">
                      {row.as === 'condition' ? t('n8nImport.asCondition') : row.ai ? t('n8nImport.asAiAgent') : t('n8nImport.asAgent')}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
            {report.dropped.length ? (
              <div className="space-y-1.5">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t('n8nImport.dropped')}</div>
                {report.dropped.map((row) => (
                  <div key={row.name} className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    <span className="font-semibold text-slate-800">{row.name}</span>: {row.reason}
                  </div>
                ))}
              </div>
            ) : null}
            {report.warnings.length ? (
              <div className="space-y-1.5">
                {report.warnings.map((w) => (
                  <div key={w} className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
            <div className="flex flex-col gap-2 sm:flex-row">
              <Choice
                active={mode === 'convert'}
                onClick={() => setMode('convert')}
                icon={Workflow}
                title={t('n8nImport.modeConvert')}
                hint={t('n8nImport.modeConvertHint')}
                testId="n8n-mode-convert"
              />
              <Choice
                active={mode === 'run'}
                onClick={() => setMode('run')}
                icon={Server}
                title={t('n8nImport.modeRun')}
                hint={t('n8nImport.modeRunHint')}
                testId="n8n-mode-run"
              />
            </div>

            <div className="flex gap-1 border-b border-slate-200">
              {[
                { id: 'instance', label: t('n8nImport.fromInstance'), icon: Server },
                { id: 'json', label: t('n8nImport.fromJson'), icon: FileJson },
              ].map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSource(id)}
                  className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-semibold ${
                    source === id ? 'border-cyan-600 text-cyan-700' : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </button>
              ))}
            </div>

            {source === 'instance' ? (
              workflows === null ? (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" /> {t('n8nImport.loading')}
                </div>
              ) : listError ? (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
                  {listError}{' '}
                  <Link to="/connectors?tab=n8n" className="font-semibold text-cyan-700 underline">{t('n8nImport.openConnector')}</Link>
                </div>
              ) : workflows.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                  {t('n8nImport.noWorkflows')}
                </div>
              ) : (
                <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
                  {workflows.map((w) => (
                    <button
                      key={w.id}
                      type="button"
                      onClick={() => setWorkflowId(w.id)}
                      className={`flex w-full items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left transition ${
                        workflowId === w.id ? 'border-cyan-400 bg-cyan-50' : 'border-slate-200 bg-slate-50 hover:border-cyan-300'
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-slate-800">{w.name || w.id}</span>
                        <span className="block text-[11px] text-slate-400">
                          {t('n8nImport.steps', { count: w.nodes })}
                          {w.trigger ? ` · ${t(`n8nImport.trigger.${w.trigger.kind}`)}` : ''}
                        </span>
                      </span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        w.active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'
                      }`}
                      >
                        {w.active ? t('n8nImport.active') : t('n8nImport.inactive')}
                      </span>
                    </button>
                  ))}
                </div>
              )
            ) : (
              <div className="space-y-2">
                <input ref={fileRef} type="file" accept=".json,application/json" onChange={pickFile} className="hidden" />
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50"
                >
                  <Upload className="h-3.5 w-3.5" />
                  {t('n8nImport.pickFile')}
                </button>
                <textarea
                  value={json}
                  onChange={(e) => setJson(e.target.value)}
                  rows={10}
                  spellCheck={false}
                  placeholder={t('n8nImport.pastePlaceholder')}
                  className={`${INPUT} font-mono text-xs`}
                  data-testid="n8n-json"
                />
                {mode === 'run' ? (
                  <p className="text-[11px] leading-5 text-slate-400">{t('n8nImport.runFromJsonHint')}</p>
                ) : null}
              </div>
            )}

            {mode === 'run' && picked && !picked.trigger && picked.nodes > 0 ? (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                <span>{t('n8nImport.noTrigger')}</span>
              </div>
            ) : null}

            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('n8nImport.name')}</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={picked?.name || t('n8nImport.namePlaceholder')}
                className={INPUT}
              />
            </div>

            {error ? (
              <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
                <p className="text-sm text-rose-700">{error}</p>
              </div>
            ) : null}
          </div>
        )}

        <div className="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
          <button
            onClick={onClose}
            className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50"
          >
            {result ? t('flowEditor.close') : t('agentFlows.cancel')}
          </button>
          {result ? (
            <button
              onClick={() => onOpen(result.flow, mode)}
              className="flex items-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700"
              data-testid="n8n-open-flow"
            >
              {mode === 'run' ? t('n8nImport.openRun') : t('n8nImport.openEditor')}
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={busy || !ready}
              className="flex items-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
              data-testid="n8n-import-submit"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {t('n8nImport.import')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

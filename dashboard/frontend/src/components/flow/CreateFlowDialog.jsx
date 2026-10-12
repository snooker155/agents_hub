import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronLeft, ChevronRight, Plus, X } from 'lucide-react';
import { createFlow, createFlowFromTemplate } from '../../api/flows';
import { useI18n } from '../../i18n';
import FlowTemplatePicker from './FlowTemplatePicker';
import { flowTemplateText } from './flowTemplates';

// The two steps' widths: four template cards across, then a short form.
const WIDTH = { 1: 'max-w-6xl', 2: 'max-w-lg' };

function StepDots({ step }) {
  const { t } = useI18n();
  const labels = [t('flowTemplates.stepTemplate'), t('flowTemplates.stepDetails')];
  return (
    <div className="flex items-center gap-1">
      {labels.map((label, i) => {
        const n = i + 1;
        const done = step > n;
        const active = step === n;
        return (
          <div key={label} className="flex flex-1 items-center gap-1">
            <div className={`flex items-center gap-1.5 ${active ? 'text-cyan-700' : done ? 'text-emerald-600' : 'text-slate-400'}`}>
              <div className={`flex h-6 w-6 items-center justify-center rounded-full border-2 text-xs font-bold ${
                active ? 'border-cyan-600 bg-cyan-50' : done ? 'border-emerald-500 bg-emerald-50' : 'border-slate-300 bg-white'
              }`}>
                {done ? <Check className="h-3 w-3" /> : n}
              </div>
              <span className="text-[11px] font-semibold">{label}</span>
            </div>
            {i < labels.length - 1 && <div className={`mx-1 h-0.5 flex-1 rounded ${done ? 'bg-emerald-400' : 'bg-slate-200'}`} />}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Create flow, in two steps: pick a blank canvas or a ready flow
 * (flow/templates/), then name and describe it. Picking a template fills the
 * name and description from it in the interface language; going back and
 * picking another refills them only if the person has not edited them. A
 * template's flow opens on its run page, ready to run; a blank one in the
 * editor.
 */
export default function CreateFlowDialog({ onClose }) {
  const { t, language } = useI18n();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [template, setTemplate] = useState(null);
  const [form, setForm] = useState({ name: '', description: '' });
  // What the form was last filled with from a template, so a later pick can
  // tell a typed name from one it put there itself.
  const [filled, setFilled] = useState({ name: '', description: '' });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  const pick = (tpl) => {
    setTemplate(tpl);
    const next = tpl ? flowTemplateText(t, tpl) : { name: '', description: '' };
    setForm((cur) => ({
      name: cur.name === filled.name ? next.name : cur.name,
      description: cur.description === filled.description ? next.description : cur.description,
    }));
    setFilled(next);
  };

  const submit = async (event) => {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) return;
    setCreating(true);
    setError('');
    try {
      const description = form.description.trim();
      if (template) {
        const { data } = await createFlowFromTemplate(template.id, { name, description, lang: language });
        navigate(`/flows/${data.id}`);
      } else {
        const { data } = await createFlow({ name, description });
        navigate(`/flows/${data.id}/edit`);
      }
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
      setCreating(false);
    }
  };

  const chosen = template ? flowTemplateText(t, template) : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className={`relative flex max-h-[90vh] w-full ${WIDTH[step]} flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl`}>
        <div className="space-y-4 border-b border-slate-100 px-6 py-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Plus className="h-5 w-5 text-cyan-600" />
              <h2 className="text-lg font-bold text-slate-900">{t('agentFlows.createFlow')}</h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <StepDots step={step} />
        </div>

        <form onSubmit={step === 1 ? (e) => { e.preventDefault(); setStep(2); } : submit} className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
            {step === 1 ? (
              <FlowTemplatePicker selectedId={template?.id || null} onPick={pick} onChoose={() => setStep(2)} />
            ) : (
              <>
                <div className="rounded-2xl border border-cyan-100 bg-cyan-50/60 px-4 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-cyan-700">
                    {t('flowTemplates.startingFrom')}
                  </div>
                  <div className="mt-0.5 text-sm font-semibold text-slate-900">
                    {chosen ? chosen.name : t('flowTemplates.blank')}
                  </div>
                  {template ? (
                    <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
                      {template.steps.map((s, i) => (
                        <span key={s.key} className="inline-flex items-center gap-1">
                          {i > 0 && <span className="text-slate-300">›</span>}
                          <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5" title={s.agent}>{s.label}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-1 text-xs text-slate-500">{t('agentFlows.startWithABlankCanvas')}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('agentFlows.name')}</label>
                  <input
                    autoFocus
                    value={form.name}
                    onChange={(e) => setForm((cur) => ({ ...cur, name: e.target.value }))}
                    placeholder={t('agentFlows.customerOnboardingPipeline')}
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition"
                    data-testid="create-flow-name"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">{t('agentFlows.description')}</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setForm((cur) => ({ ...cur, description: e.target.value }))}
                    rows={4}
                    placeholder={t('agentFlows.whatThisFlowIsResponsible')}
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition"
                  />
                </div>
                {error && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
                )}
              </>
            )}
          </div>

          <div className="flex justify-between gap-3 border-t border-slate-100 px-6 py-4">
            <button
              type="button"
              onClick={step === 1 ? onClose : () => setStep(1)}
              disabled={creating}
              className="flex items-center gap-1 rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
            >
              {step === 1 ? t('agentFlows.cancel') : <><ChevronLeft className="h-4 w-4" />{t('common.back')}</>}
            </button>
            {step === 1 ? (
              <button
                type="submit"
                className="flex items-center justify-center gap-1 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700"
                data-testid="create-flow-next"
              >
                {t('common.next')}
                <ChevronRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={creating || !form.name.trim()}
                className="flex items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
                data-testid="create-flow-submit"
              >
                <Plus className="h-4 w-4" />
                {creating ? t('flowTemplates.creating') : template ? t('flowTemplates.createFromTemplate') : t('agentFlows.createFlow')}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

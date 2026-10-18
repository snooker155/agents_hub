import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, X } from 'lucide-react';
import { createFlow } from '../../api/flows';
import { useI18n } from '../../i18n';

/**
 * Create flow: name and describe it, then the blank canvas opens in the
 * editor.
 */
export default function CreateFlowDialog({ onClose }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: '', description: '' });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event) => {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) return;
    setCreating(true);
    setError('');
    try {
      const { data } = await createFlow({ name, description: form.description.trim() });
      navigate(`/flows/${data.id}/edit`);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="relative flex max-h-[90vh] w-full max-w-lg flex-col rounded-[28px] border border-slate-200 bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-5">
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

        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
            <p className="text-xs text-slate-500">{t('agentFlows.startWithABlankCanvas')}</p>
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
          </div>

          <div className="flex justify-between gap-3 border-t border-slate-100 px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              disabled={creating}
              className="rounded-2xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
            >
              {t('agentFlows.cancel')}
            </button>
            <button
              type="submit"
              disabled={creating || !form.name.trim()}
              className="flex items-center justify-center gap-2 rounded-2xl bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-700 disabled:cursor-not-allowed disabled:bg-cyan-300"
              data-testid="create-flow-submit"
            >
              <Plus className="h-4 w-4" />
              {creating ? t('common.loading') : t('agentFlows.createFlow')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

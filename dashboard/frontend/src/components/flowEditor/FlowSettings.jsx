import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';

// The shared secret POST /{flow_id}/trigger requires a signature against. The
// value is write-only: the flow reports only whether one is set, so the field
// offers Set and Clear and never shows what is stored.
export function WebhookSecret({ flow, onSet }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const configured = !!flow.webhook_secret_configured;

  const run = async (secret) => {
    setBusy(true);
    try {
      await onSet(secret);
      setValue('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-slate-200 pt-3">
      <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {t('flowEditor.webhookSecret')}
      </label>
      <p className="px-1 text-[11px] leading-5 text-slate-400">
        {configured ? t('flowEditor.webhookSecretSet') : t('flowEditor.webhookSecretNotSet')}
      </p>
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t('flowEditor.webhookSecretPlaceholder')}
        className={INPUT_CLS}
      />
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !value.trim()}
          onClick={() => run(value.trim())}
          className="inline-flex items-center gap-1.5 rounded-xl border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t('flowEditor.setSecret')}
        </button>
        <button
          type="button"
          disabled={busy || !configured}
          onClick={() => run('')}
          className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:border-rose-300 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t('flowEditor.clearSecret')}
        </button>
      </div>
      <p className="px-1 text-[11px] leading-5 text-slate-400">{t('flowEditor.signatureHeaderNote')}</p>
    </div>
  );
}

// Flow-level meta editor: entry_point, mutability, recordability, and the
// initial state-key defaults. Patches go to `flow` and mark the editor dirty.
export function FlowSettings({ flow, nodes, onPatch, onSetWebhookSecret }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const mutability = flow.mutability !== false; // default true

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between py-1 text-sm font-bold text-slate-900"
      >
        Flow settings
        {open ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
      </button>
      {open ? (
        <div className="space-y-3 pt-2">
          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.entryPoint')}</label>
            <select
              value={flow.entry_point || ''}
              onChange={(e) => onPatch({ entry_point: e.target.value })}
              className={INPUT_CLS}
            >
              <option value="">{t('flowEditor.autoRootNodes')}</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>{n.data?.label || n.id}</option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 px-1 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={mutability}
              onChange={(e) => onPatch({ mutability: e.target.checked })}
            />
            {t('flowEditor.mutableState')}
            <span className="text-[11px] text-slate-400">
              {mutability ? t('flowEditor.keysOverwritable') : t('flowEditor.keysWriteOnce')}
            </span>
          </label>

          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.recordability')}</label>
            <select
              value={flow.recordability || 'full'}
              onChange={(e) => onPatch({ recordability: e.target.value })}
              className={INPUT_CLS}
            >
              <option value="full">{t('flowEditor.full')}</option>
              <option value="none">{t('flowEditor.none')}</option>
            </select>
          </div>

          {/* Execution policy, what a failed node does to the rest of the
              graph, and how much of the graph may run at once. */}
          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.onError')}</label>
            <select
              value={flow.on_error || 'fail_fast'}
              onChange={(e) => onPatch({ on_error: e.target.value })}
              className={INPUT_CLS}
            >
              <option value="fail_fast">{t('flowEditor.onErrorFailFast')}</option>
              <option value="continue">{t('flowEditor.onErrorContinue')}</option>
              <option value="isolate_branch">{t('flowEditor.onErrorIsolateBranch')}</option>
            </select>
            <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.onErrorHint')}</p>
          </div>

          <div>
            <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('flowEditor.maxParallel')}</label>
            <input
              type="number"
              min="1"
              value={flow.max_parallel ?? 4}
              onChange={(e) => onPatch({ max_parallel: Math.max(1, Number(e.target.value) || 1) })}
              className={INPUT_CLS}
            />
            <p className="px-1 pt-1 text-[11px] text-slate-400">{t('flowEditor.maxParallelHint')}</p>
          </div>

          <WebhookSecret flow={flow} onSet={onSetWebhookSecret} />
        </div>
      ) : null}
    </div>
  );
}

export default FlowSettings;

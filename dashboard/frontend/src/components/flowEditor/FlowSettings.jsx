import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';

// Flow-level meta editor: entry_point, mutability, recordability, and the
// initial state-key defaults. Patches go to `flow` and mark the editor dirty.
export function FlowSettings({ flow, nodes, onPatch }) {
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
        </div>
      ) : null}
    </div>
  );
}

export default FlowSettings;

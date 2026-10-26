import React from 'react';
import { Gauge, Loader, Save, Target } from 'lucide-react';
import { useI18n } from '../../i18n';
import { EVALUATOR_KINDS, graderText } from './loopFormat';

const FIELD = 'w-full text-sm border border-gray-300 rounded-lg px-3 py-2';
const LABEL = 'block text-xs font-semibold text-gray-600 mb-1';

/**
 * The loop's settings: the flow it repeats, what "good enough" means, who
 * judges it and when to give up. The Loop Creator edits the same record from
 * the side chat; the page decides whose version the form shows.
 */
export default function LoopSetupForm({ draft, onChange, flows, agents, onSave, saving }) {
  const { t } = useI18n();
  const set = onChange;
  return (
    <div className="p-5 space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className={LABEL}>{t('loops.name')}</label>
          <input value={draft.name} onChange={(e) => set({ name: e.target.value })} className={FIELD} />
        </div>
        <div>
          <label className={LABEL}>{t('loops.flowToRepeat')}</label>
          <select value={draft.flow_id} onChange={(e) => set({ flow_id: e.target.value })} className={FIELD}>
            <option value="">{t('loops.selectAFlow')}</option>
            {flows.map((f) => (
              <option key={f.id} value={f.id}>{f.name || f.id}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className={LABEL}>{t('loops.description')}</label>
        <textarea value={draft.description} onChange={(e) => set({ description: e.target.value })} rows={2} className={FIELD} />
      </div>

      <div>
        <label className={LABEL}>
          <Target className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
          {t('loops.exitCriterion')}
        </label>
        <textarea
          value={draft.exit_criterion} onChange={(e) => set({ exit_criterion: e.target.value })}
          rows={4} placeholder={t('loops.eGEveryClaimIn')} className={FIELD}
        />
        <p className="text-xs text-gray-500 mt-1">{t('loops.exitCriterionHint')}</p>
      </div>

      <div>
        <label htmlFor="loop-rubric" className={LABEL}>{t('outcomes.loopRubric')}</label>
        <textarea
          id="loop-rubric"
          value={draft.rubric || ''} onChange={(e) => set({ rubric: e.target.value })}
          rows={4} placeholder={t('outcomes.rubricPlaceholder')}
          className={`${FIELD} font-mono`}
        />
        <p className="text-xs text-gray-500 mt-1">{t('outcomes.loopRubricHint')}</p>
        {(draft.rubric || '').trim() && (
          <div className="mt-2 max-w-md">
            <label htmlFor="loop-grader" className={LABEL}>{t('outcomes.loopGrader')}</label>
            <input
              id="loop-grader"
              value={graderText(draft.grader)}
              onChange={(e) => set({ grader: e.target.value.trim() || null })}
              placeholder={t('outcomes.loopGraderPlaceholder')}
              className={FIELD}
            />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className={LABEL}>{t('loops.whoJudges')}</label>
          <select value={draft.evaluator_mode} onChange={(e) => set({ evaluator_mode: e.target.value })} className={FIELD}>
            {EVALUATOR_KINDS.map((k) => (
              <option key={k} value={k}>{t(`loops.evaluators.${k}`)}</option>
            ))}
          </select>
          <p className="text-xs text-gray-500 mt-1">{t('loops.whoJudgesHint')}</p>
        </div>
        {draft.evaluator_mode === 'agent' && (
          <div>
            <label className={LABEL}>{t('loops.reviewer')}</label>
            <select
              value={draft.evaluator_agent_id || ''}
              onChange={(e) => set({ evaluator_agent_id: e.target.value || null })}
              className={FIELD}
            >
              <option value="">{t('loops.selectAnAgent')}</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name || a.id}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <label className={LABEL}>{t('loops.maxIterations')}</label>
          <input
            type="number" min={1} max={50} value={draft.max_iterations}
            onChange={(e) => set({ max_iterations: Number(e.target.value) })} className={FIELD}
          />
        </div>
        <div>
          <label className={LABEL}>{t('loops.minIterations')}</label>
          <input
            type="number" min={1} max={50} value={draft.min_iterations}
            onChange={(e) => set({ min_iterations: Number(e.target.value) })} className={FIELD}
          />
          <p className="text-[11px] text-gray-500 mt-1">{t('loops.minIterationsHint')}</p>
        </div>
        <div>
          <label className={LABEL}>
            <Gauge className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('loops.targetScore')}
          </label>
          <input
            type="number" min={0} max={100}
            value={draft.target_score ?? ''}
            onChange={(e) => set({ target_score: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder={t('loops.none')} className={FIELD}
          />
        </div>
        <div>
          <label className={LABEL}>{t('loops.patience')}</label>
          <input
            type="number" min={0} max={20} value={draft.patience}
            onChange={(e) => set({ patience: Number(e.target.value) })} className={FIELD}
          />
          <p className="text-[11px] text-gray-500 mt-1">{t('loops.stopAfterThisManyPasses')}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={LABEL}>{t('loops.costCeiling')}</label>
          <input
            type="number" step="0.01" min={0} value={draft.cost_ceiling ?? ''}
            onChange={(e) => set({ cost_ceiling: e.target.value === '' ? null : Number(e.target.value) })}
            placeholder={t('loops.none')} className={FIELD}
          />
        </div>
        <div>
          <label className={LABEL}>{t('loops.wallClockCap')}</label>
          <input
            type="number" min={60} value={draft.max_wall_seconds}
            onChange={(e) => set({ max_wall_seconds: Number(e.target.value) })} className={FIELD}
          />
        </div>
      </div>

      <div className="flex justify-end">
        <button
          onClick={onSave} disabled={saving}
          className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
          {t('common.save')}
        </button>
      </div>
    </div>
  );
}

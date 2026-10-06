/**
 * The config of a sequence guardrail (guardrails/sequence.py): one rule about
 * a run's tool calls. `after` lets a tool run only once another one ran,
 * `sum_max` caps the total of one argument across calls, `same_as` makes an
 * argument match the one an earlier call used. Tool names take globs
 * (`mcp__bank__*`), arguments are dotted paths (`payee.account`); the
 * backend (guardrails/models.py) validates the same shape.
 */
import { useI18n } from '../../i18n';
import { SEQUENCE_RULES, defaultSequenceConfig } from './sequenceRules';

export default function SequenceRuleFields({ config, onChange, inputCls, labelCls }) {
  const { t } = useI18n();
  const rule = config.rule || 'after';
  const set = (key, value) => onChange({ ...config, [key]: value });
  const text = (key, placeholder) => (
    <input type="text" className={`${inputCls} font-mono`} value={config[key] ?? ''}
      aria-label={t(`guardrails.sequence.${key}`)}
      onChange={(e) => set(key, e.target.value)} placeholder={placeholder} />
  );

  return (
    <div className="space-y-3" data-testid="sequence-rule">
      <div>
        <label className={labelCls}>{t('guardrails.sequence.rule')}</label>
        <select className={inputCls} value={rule} aria-label={t('guardrails.sequence.rule')}
          onChange={(e) => onChange(defaultSequenceConfig(e.target.value))}>
          {SEQUENCE_RULES.map((r) => <option key={r} value={r}>{t(`guardrails.sequence.rules.${r}`)}</option>)}
        </select>
        <p className="text-[11px] text-gray-400 mt-1">{t(`guardrails.sequence.hints.${rule}`)}</p>
      </div>

      {rule === 'after' && (
        <>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.tool')}</label>
              {text('tool', 'pay_invoice')}
            </div>
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.after_tool')}</label>
              {text('after_tool', 'check_invoice')}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input type="checkbox" checked={!!config.require_success}
              onChange={(e) => set('require_success', e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
            {t('guardrails.sequence.require_success')}
          </label>
        </>
      )}

      {rule === 'sum_max' && (
        <>
          <div>
            <label className={labelCls}>{t('guardrails.sequence.tools')}</label>
            <textarea rows={2} className={`${inputCls} font-mono`} aria-label={t('guardrails.sequence.tools')}
              value={(config.tools || []).join('\n')} placeholder={t('guardrails.sequence.toolsPlaceholder')}
              onChange={(e) => set('tools', e.target.value.split('\n').map((s) => s.trim()).filter(Boolean))} />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.argument')}</label>
              {text('argument', 'amount')}
            </div>
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.max')}</label>
              <input type="number" min="0" className={inputCls} value={config.max ?? ''}
                aria-label={t('guardrails.sequence.max')}
                onChange={(e) => set('max', e.target.value === '' ? '' : Number(e.target.value))} />
            </div>
          </div>
        </>
      )}

      {rule === 'same_as' && (
        <>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.tool')}</label>
              {text('tool', 'refund')}
            </div>
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.argument')}</label>
              {text('argument', 'account_id')}
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.source_tool')}</label>
              {text('source_tool', 'get_order')}
            </div>
            <div className="flex-1">
              <label className={labelCls}>{t('guardrails.sequence.source_argument')}</label>
              {text('source_argument', t('guardrails.sequence.sourceArgumentPlaceholder'))}
            </div>
          </div>
        </>
      )}
      <p className="text-[11px] text-gray-400">{t('guardrails.sequence.patternsHint')}</p>
    </div>
  );
}

import { Plus, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';

// Branching decided while a flow runs (flow/branching.py on the backend): a map
// node that runs one agent per item of a list, a switch that follows the first
// case whose condition on a state key holds, and an agent node that picks its
// next step itself.

const SWITCH_OPS = [
  'equals', 'not_equals', 'contains', 'not_contains', 'matches',
  'gt', 'gte', 'lt', 'lte', 'empty', 'not_empty', 'count_gt', 'count_lt',
];
const NO_VALUE_OPS = new Set(['empty', 'not_empty']);

function Label({ k }) {
  const { t } = useI18n();
  return (
    <label className="block px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
      {t(k)}
    </label>
  );
}

function Hint({ k }) {
  const { t } = useI18n();
  return <p className="px-1 pt-1 text-[11px] leading-5 text-slate-400">{t(k)}</p>;
}

function TargetSelect({ value, successors, onChange, emptyKey, testId }) {
  const { t } = useI18n();
  const known = successors.some((s) => s.id === value);
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value)} className={INPUT_CLS} data-testid={testId}>
      <option value="">{t(emptyKey)}</option>
      {value && !known ? <option value={value}>{value}</option> : null}
      {successors.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
    </select>
  );
}

export function MapConfig({ node, agents = [], onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const config = d.config || {};
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });
  const current = config.agent_id || '';
  const known = agents.some((a) => a.id === current);
  return (
    <div className="space-y-3" data-testid="map-config">
      <div>
        <Label k="flowEditor.map.agent" />
        <select
          value={current}
          onChange={(e) => patchConfig({ agent_id: e.target.value })}
          className={`${INPUT_CLS} ${current ? '' : 'border-amber-400'}`}
          data-testid="map-agent"
        >
          <option value="">{t('flowEditor.chooseAgent')}</option>
          {current && !known ? <option value={current}>{current}</option> : null}
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
        </select>
      </div>
      <div>
        <Label k="flowEditor.map.task" />
        <textarea
          value={config.task || ''}
          onChange={(e) => patchConfig({ task: e.target.value })}
          rows={3}
          placeholder={t('flowEditor.map.taskPlaceholder')}
          className={INPUT_CLS}
        />
        <Hint k="flowEditor.map.taskHint" />
      </div>
      <div>
        <Label k="flowEditor.map.over" />
        <input
          value={config.over || ''}
          onChange={(e) => patchConfig({ over: e.target.value.trim() })}
          placeholder={t('flowEditor.map.overPlaceholder')}
          className={INPUT_CLS}
        />
        <Hint k="flowEditor.map.overHint" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-[11px] font-medium text-slate-500">
          {t('flowEditor.map.maxParallel')}
          <input
            type="number" min="0" max="64"
            value={config.max_parallel ?? 0}
            onChange={(e) => patchConfig({ max_parallel: Number(e.target.value) || 0 })}
            className={`${INPUT_CLS} mt-1`}
          />
        </label>
        <label className="block text-[11px] font-medium text-slate-500">
          {t('flowEditor.map.maxItems')}
          <input
            type="number" min="1" max="500"
            value={config.max_items ?? 50}
            onChange={(e) => patchConfig({ max_items: Number(e.target.value) || 50 })}
            className={`${INPUT_CLS} mt-1`}
          />
        </label>
      </div>
      <label className="flex items-start gap-2 px-1 text-xs text-slate-600">
        <input type="checkbox" className="mt-0.5" checked={Boolean(config.allow_failures)}
          onChange={(e) => patchConfig({ allow_failures: e.target.checked })} />
        {t('flowEditor.map.allowFailures')}
      </label>
      <label className="flex items-start gap-2 px-1 text-xs text-slate-600">
        <input type="checkbox" className="mt-0.5" checked={Boolean(config.include_context)}
          onChange={(e) => patchConfig({ include_context: e.target.checked })} />
        {t('flowEditor.map.includeContext')}
      </label>
      <Hint k="flowEditor.map.hint" />
    </div>
  );
}

export function SwitchConfig({ node, successors = [], onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const config = d.config || {};
  const cases = Array.isArray(config.cases) ? config.cases : [];
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });
  const setCase = (index, patch) => patchConfig({ cases: cases.map((c, i) => (i === index ? { ...c, ...patch } : c)) });
  return (
    <div className="space-y-3" data-testid="switch-config">
      <div>
        <Label k="flowEditor.switch.key" />
        <input
          value={config.key || ''}
          onChange={(e) => patchConfig({ key: e.target.value.trim() })}
          placeholder="score"
          className={INPUT_CLS}
        />
      </div>
      <div className="space-y-2">
        <Label k="flowEditor.switch.cases" />
        {cases.map((c, index) => (
          <div key={index} className="space-y-1.5 rounded-xl border border-slate-200 p-2">
            <div className="flex items-center gap-2">
              <select value={c.op || 'equals'} onChange={(e) => setCase(index, { op: e.target.value })} className={INPUT_CLS}>
                {SWITCH_OPS.map((op) => <option key={op} value={op}>{t(`flowEditor.switch.ops.${op}`)}</option>)}
              </select>
              <button
                type="button"
                onClick={() => patchConfig({ cases: cases.filter((_, i) => i !== index) })}
                className="shrink-0 rounded-xl border border-slate-200 px-2 py-2 text-slate-400 transition hover:border-rose-300 hover:text-rose-500"
                aria-label={t('flowEditor.switch.removeCase')}
              >
                <XCircle className="h-4 w-4" />
              </button>
            </div>
            {NO_VALUE_OPS.has(c.op) ? null : (
              <input
                value={c.value ?? ''}
                onChange={(e) => setCase(index, { value: e.target.value })}
                placeholder={t('flowEditor.switch.value')}
                className={INPUT_CLS}
              />
            )}
            <TargetSelect value={c.target} successors={successors} emptyKey="flowEditor.switch.chooseTarget"
              onChange={(target) => setCase(index, { target })} />
          </div>
        ))}
        <button
          type="button"
          onClick={() => patchConfig({ cases: [...cases, { op: 'equals', value: '', target: '' }] })}
          className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700"
        >
          <Plus className="h-3.5 w-3.5" />
          {t('flowEditor.switch.addCase')}
        </button>
      </div>
      <div>
        <Label k="flowEditor.switch.default" />
        <TargetSelect value={config.default} successors={successors} emptyKey="flowEditor.switch.noDefault"
          onChange={(target) => patchConfig({ default: target })} />
      </div>
      <label className="flex items-start gap-2 px-1 text-xs text-slate-600">
        <input type="checkbox" className="mt-0.5" checked={Boolean(config.all_matches)}
          onChange={(e) => patchConfig({ all_matches: e.target.checked })} />
        {t('flowEditor.switch.allMatches')}
      </label>
      {successors.length < 2 ? <p className="px-1 text-[11px] text-amber-600">{t('flowEditor.switch.needsTwo')}</p> : null}
      <Hint k="flowEditor.switch.hint" />
    </div>
  );
}

// On an agent node: the agent chooses which of its next steps runs.
export function RouteConfig({ node, successors = [], onPatch }) {
  const { t } = useI18n();
  const d = node.data || {};
  const mode = d.route === true ? 'one' : (d.route || '');
  return (
    <div className="space-y-2 rounded-[20px] border border-slate-200 bg-slate-50 p-4" data-testid="route-config">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {t('flowEditor.route.title')}
      </div>
      <select
        value={mode}
        onChange={(e) => onPatch({ route: e.target.value || null, ...(e.target.value ? {} : { route_default: null }) })}
        className={INPUT_CLS}
        disabled={successors.length < 2 && !mode}
        data-testid="route-mode"
      >
        <option value="">{t('flowEditor.route.off')}</option>
        <option value="one">{t('flowEditor.route.one')}</option>
        <option value="many">{t('flowEditor.route.many')}</option>
      </select>
      {mode ? (
        <>
          <TargetSelect value={d.route_default} successors={successors} emptyKey="flowEditor.route.noDefault"
            onChange={(target) => onPatch({ route_default: target || null })} />
          <input
            value={d.route_hint || ''}
            onChange={(e) => onPatch({ route_hint: e.target.value })}
            placeholder={t('flowEditor.route.hintPlaceholder')}
            className={INPUT_CLS}
          />
        </>
      ) : null}
      <p className="text-[11px] leading-5 text-slate-400">
        {successors.length < 2 ? t('flowEditor.route.needsTwo') : t('flowEditor.route.hint')}
      </p>
    </div>
  );
}

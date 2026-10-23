import { useRef, useState } from 'react';
import { ChevronDown, Plus, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { INPUT_CLS } from './graphHelpers';
import AgentSelect from './AgentSelect';

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
  return (
    <div className="space-y-3" data-testid="map-config">
      <div>
        <Label k="flowEditor.map.agent" />
        <AgentSelect
          agents={agents}
          value={current}
          onChange={(id) => patchConfig({ agent_id: id })}
          testId="map-agent"
        />
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

// A key typed by hand or picked from the keys the switch can read: the
// request a run starts with and what the nodes before it write.
function KeyCombo({ value, options, onChange, placeholder, testId }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const inputRef = useRef(null);
  const pick = (key) => { onChange(key); setOpen(false); };
  return (
    <div className="relative">
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value.trim())}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
        placeholder={placeholder}
        role="combobox"
        aria-expanded={open}
        className={`${INPUT_CLS} pr-8`}
        data-testid={testId}
      />
      <button
        type="button"
        tabIndex={-1}
        aria-label={t('flowEditor.switch.showKeys')}
        onMouseDown={(e) => {
          e.preventDefault();
          setOpen((v) => !v);
          inputRef.current?.focus();
        }}
        className="absolute inset-y-0 right-0 flex items-center px-2 text-slate-400 hover:text-slate-600"
      >
        <ChevronDown className="h-4 w-4" />
      </button>
      {open && options.length ? (
        <ul role="listbox" className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
          {options.map((o) => (
            <li
              key={o.key}
              role="option"
              aria-selected={o.key === value}
              // Before the input's blur, which would close the list first.
              onMouseDown={(e) => { e.preventDefault(); pick(o.key); }}
              className={`cursor-pointer px-3 py-1.5 hover:bg-cyan-50 ${o.key === value ? 'bg-slate-50' : ''}`}
            >
              <div className="truncate font-mono text-xs text-slate-800">{o.key}</div>
              {o.hint ? <div className="truncate text-[11px] text-slate-400">{o.hint}</div> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// `stateKeys`: what the switch can test. `upstream` is what the nodes before it
// write ({key, writers}), `declared` the keys the flow's own state names.
export function SwitchConfig({ node, successors = [], onPatch, stateKeys = {} }) {
  const { t } = useI18n();
  const d = node.data || {};
  const config = d.config || {};
  const cases = Array.isArray(config.cases) ? config.cases : [];
  const patchConfig = (patch) => onPatch({ config: { ...config, ...patch } });
  const setCase = (index, patch) => patchConfig({ cases: cases.map((c, i) => (i === index ? { ...c, ...patch } : c)) });
  const upstream = stateKeys.upstream || [];
  const declared = stateKeys.declared || [];
  const key = config.key || '';
  const keyOptions = [
    { key: 'user_request', hint: t('flowEditor.switch.requestKey') },
    ...upstream.filter((u) => u.key !== 'user_request').map((u) => ({ key: u.key, hint: u.writers.join(', ') })),
  ];
  // The run seeds `user_request` and the flow's declared keys; anything else
  // only a node before the switch can write.
  const unwritten = key && key !== 'user_request' && !declared.includes(key) && !upstream.some((u) => u.key === key);
  return (
    <div className="space-y-3" data-testid="switch-config">
      <div>
        <Label k="flowEditor.switch.key" />
        <KeyCombo
          value={key}
          options={keyOptions}
          onChange={(next) => patchConfig({ key: next })}
          placeholder="score"
          testId="switch-key"
        />
        {unwritten ? (
          <p className="px-1 pt-1 text-[11px] leading-5 text-amber-600" data-testid="switch-key-unwritten">
            {t('flowEditor.switch.keyUnwritten')}
          </p>
        ) : null}
        <p className="px-1 pt-1 text-[11px] leading-5 text-slate-400">
          {t('flowEditor.switch.keyHint', { field: t('flowEditor.outputStateKeys') })}
        </p>
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

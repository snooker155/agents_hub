/**
 * Create/edit modal for a guardrail, with one config section per rule kind
 * (guardrails/models.py's validate_config knows the same shapes). A
 * `sequence` guardrail checks a run's tool calls rather than its text: its
 * stage is always `tool`, and it blocks or asks a person instead of warning.
 */
import { useState } from 'react';
import { X, Loader, ShieldCheck } from 'lucide-react';
import { createGuardrail, updateGuardrail } from '../../api/guardrails';
import { useI18n } from '../../i18n';
import SequenceRuleFields from './SequenceRuleFields';
import { defaultSequenceConfig } from './sequenceRules';

const STAGES = ['input', 'output', 'both'];
const KINDS = ['regex', 'keywords', 'pii', 'max_chars', 'judge', 'sequence'];
const TEXT_ACTIONS = ['block', 'warn'];
const SEQUENCE_ACTIONS = ['block', 'ask'];
const actionsFor = (kind) => (kind === 'sequence' ? SEQUENCE_ACTIONS : TEXT_ACTIONS);
const APPLIES_TO = ['all', 'selected'];
const PII_DETECTORS = ['email', 'phone', 'credit_card', 'iban', 'api_key'];
const REGEX_FLAGS = ['IGNORECASE', 'MULTILINE', 'DOTALL'];

function linesToList(text) {
  return (text || '').split('\n').map((s) => s.trim()).filter(Boolean);
}

function listToLines(list) {
  return (list || []).join('\n');
}

function defaultConfig(kind, existing) {
  if (existing) return existing;
  if (kind === 'regex') return { pattern: '', flags: [] };
  if (kind === 'keywords') return { keywords: [] };
  if (kind === 'pii') return { detectors: [] };
  if (kind === 'max_chars') return { max_chars: 2000 };
  if (kind === 'judge') return { instruction: '' };
  if (kind === 'sequence') return defaultSequenceConfig();
  return {};
}

export default function GuardrailModal({ guardrail, workspace, onClose, onSaved }) {
  const { t } = useI18n();
  const isEdit = !!guardrail;
  const [name, setName] = useState(guardrail?.name || '');
  const [description, setDescription] = useState(guardrail?.description || '');
  const [scope, setScope] = useState(guardrail ? (guardrail.workspace ? 'workspace' : 'global') : (workspace ? 'workspace' : 'global'));
  const [stage, setStage] = useState(guardrail?.stage || 'input');
  const [kind, setKind] = useState(guardrail?.kind || 'regex');
  const [config, setConfig] = useState(defaultConfig(guardrail?.kind || 'regex', guardrail?.config));
  const [action, setAction] = useState(guardrail?.action || 'block');
  const [appliesTo, setAppliesTo] = useState(guardrail?.applies_to || 'all');
  const [enabled, setEnabled] = useState(guardrail?.enabled ?? true);
  const [failClosed, setFailClosed] = useState(guardrail?.fail_closed ?? true);
  const [model, setModel] = useState(guardrail?.model || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const changeKind = (next) => {
    setKind(next);
    setConfig(defaultConfig(next));
    if (!actionsFor(next).includes(action)) setAction('block');
  };
  const isSequence = kind === 'sequence';

  const toggleFlag = (flag) => {
    const flags = config.flags || [];
    setConfig({ ...config, flags: flags.includes(flag) ? flags.filter((f) => f !== flag) : [...flags, flag] });
  };

  const toggleDetector = (detector) => {
    const detectors = config.detectors || [];
    setConfig({
      ...config,
      detectors: detectors.includes(detector) ? detectors.filter((d) => d !== detector) : [...detectors, detector],
    });
  };

  const handleSave = async () => {
    if (!name.trim()) { setError(t('guardrails.errors.nameRequired')); return; }
    setSaving(true);
    setError('');
    const payload = {
      name: name.trim(),
      description: description.trim(),
      workspace: scope === 'workspace' ? (workspace || null) : null,
      stage: isSequence ? 'tool' : stage,
      kind,
      config,
      action,
      applies_to: appliesTo,
      enabled,
      fail_closed: failClosed,
      model: kind === 'judge' ? (model.trim() || null) : null,
    };
    try {
      if (isEdit) await updateGuardrail(guardrail.id, payload);
      else await createGuardrail(payload);
      onSaved();
    } catch (err) {
      setError(err?.response?.data?.detail || t('guardrails.errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const inputCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none';
  const labelCls = 'block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider';

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-600" />
            {isEdit ? t('guardrails.editGuardrail') : t('guardrails.newGuardrail')}
          </h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div>
          <label className={labelCls}>{t('guardrails.name')}</label>
          <input type="text" className={inputCls} value={name} onChange={(e) => setName(e.target.value)}
            placeholder={t('guardrails.namePlaceholder')} />
        </div>

        <div>
          <label className={labelCls}>{t('guardrails.description')}</label>
          <textarea rows={2} className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className={labelCls}>{t('guardrails.scope')}</label>
            <select className={inputCls} value={scope} onChange={(e) => setScope(e.target.value)} disabled={!workspace}>
              <option value="global">{t('guardrails.scopeGlobal')}</option>
              {workspace && <option value="workspace">{t('guardrails.scopeWorkspace', { workspace })}</option>}
            </select>
          </div>
          <div className="flex-1">
            <label className={labelCls}>{t('guardrails.stage.label')}</label>
            {isSequence ? (
              <select className={inputCls} value="tool" disabled>
                <option value="tool">{t('guardrails.stage.tool')}</option>
              </select>
            ) : (
              <select className={inputCls} value={stage} onChange={(e) => setStage(e.target.value)}>
                {STAGES.map((s) => <option key={s} value={s}>{t(`guardrails.stage.${s}`)}</option>)}
              </select>
            )}
          </div>
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className={labelCls}>{t('guardrails.kind.label')}</label>
            <select className={inputCls} value={kind} onChange={(e) => changeKind(e.target.value)} disabled={isEdit}>
              {KINDS.map((k) => <option key={k} value={k}>{t(`guardrails.kind.${k}`)}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className={labelCls}>{t('guardrails.action.label')}</label>
            <select className={inputCls} value={action} onChange={(e) => setAction(e.target.value)}>
              {actionsFor(kind).map((a) => <option key={a} value={a}>{t(`guardrails.action.${a}`)}</option>)}
            </select>
          </div>
        </div>

        {kind === 'regex' && (
          <div>
            <label className={labelCls}>{t('guardrails.config.pattern')}</label>
            <input type="text" className={`${inputCls} font-mono`} value={config.pattern || ''}
              onChange={(e) => setConfig({ ...config, pattern: e.target.value })}
              placeholder={t('guardrails.config.patternPlaceholder')} />
            <div className="flex gap-3 mt-2">
              {REGEX_FLAGS.map((flag) => (
                <label key={flag} className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                  <input type="checkbox" checked={(config.flags || []).includes(flag)}
                    onChange={() => toggleFlag(flag)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600" />
                  {t(`guardrails.config.flag${flag[0]}${flag.slice(1).toLowerCase()}`)}
                </label>
              ))}
            </div>
          </div>
        )}

        {kind === 'keywords' && (
          <div>
            <label className={labelCls}>{t('guardrails.config.keywords')}</label>
            <textarea rows={3} className={`${inputCls} font-mono`} value={listToLines(config.keywords)}
              onChange={(e) => setConfig({ ...config, keywords: linesToList(e.target.value) })}
              placeholder={t('guardrails.config.keywordsPlaceholder')} />
            <p className="text-[11px] text-gray-400 mt-1">{t('guardrails.config.keywordsHint')}</p>
          </div>
        )}

        {kind === 'pii' && (
          <div>
            <label className={labelCls}>{t('guardrails.config.detectors')}</label>
            <div className="grid grid-cols-2 gap-1.5">
              {PII_DETECTORS.map((d) => (
                <label key={d} className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                  <input type="checkbox" checked={(config.detectors || []).includes(d)}
                    onChange={() => toggleDetector(d)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600" />
                  {t(`guardrails.config.detector${d.replace(/(^|_)([a-z])/g, (_, __, c) => c.toUpperCase())}`)}
                </label>
              ))}
            </div>
            <p className="text-[11px] text-gray-400 mt-1">{t('guardrails.config.detectorsHint')}</p>
          </div>
        )}

        {kind === 'max_chars' && (
          <div>
            <label className={labelCls}>{t('guardrails.config.maxChars')}</label>
            <input type="number" min="1" className={inputCls} value={config.max_chars || ''}
              onChange={(e) => setConfig({ ...config, max_chars: Number(e.target.value) || '' })} />
          </div>
        )}

        {isSequence && (
          <SequenceRuleFields config={config} onChange={setConfig} inputCls={inputCls} labelCls={labelCls} />
        )}

        {kind === 'judge' && (
          <>
            <div>
              <label className={labelCls}>{t('guardrails.config.instruction')}</label>
              <textarea rows={3} className={inputCls} value={config.instruction || ''}
                onChange={(e) => setConfig({ ...config, instruction: e.target.value })}
                placeholder={t('guardrails.config.instructionPlaceholder')} />
              <p className="text-[11px] text-gray-400 mt-1">{t('guardrails.config.instructionHint')}</p>
            </div>
            <div>
              <label className={labelCls}>{t('guardrails.model')}</label>
              <input type="text" className={`${inputCls} font-mono`} value={model}
                onChange={(e) => setModel(e.target.value)} placeholder={t('guardrails.modelPlaceholder')} />
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" checked={failClosed} onChange={(e) => setFailClosed(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
              {t('guardrails.failClosed')}
            </label>
            <p className="text-[11px] text-gray-400">{t('guardrails.failClosedHint')}</p>
          </>
        )}

        <div className="flex gap-4">
          <div className="flex-1">
            <label className={labelCls}>{t('guardrails.appliesTo.label')}</label>
            <select className={inputCls} value={appliesTo} onChange={(e) => setAppliesTo(e.target.value)}>
              {APPLIES_TO.map((a) => <option key={a} value={a}>{t(`guardrails.appliesTo.${a}`)}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer self-end pb-2">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
            {t('guardrails.enabled')}
          </label>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('guardrails.cancel')}
          </button>
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
            {isEdit ? t('guardrails.save') : t('guardrails.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

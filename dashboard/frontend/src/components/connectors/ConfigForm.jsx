import { useState } from 'react';
import { RefreshCw, Save, Trash2, Wifi } from 'lucide-react';
import { inputCls } from '../settingsUi';

// The generic credentials form: the backend describes a connector's fields
// (GET /api/connectors) and this renders them, with secrets write only. Used
// by CredentialConnector for the tracker connectors.

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnSecondary = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';
const btnDanger = 'flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';

/** Whether a field's ``{other: value}`` condition (hidden_when, optional_when) holds. */
const when = (cond, values) => Object.entries(cond || {}).some(([k, v]) => String(values?.[k] ?? '') === String(v));

function FieldInput({ field, value, isSet, onChange, t, name, required, disabled }) {
  const label = t(`connectors.fields.${name}.${field.key}`, { defaultValue: field.key });
  const hint = t(`connectors.fieldHints.${name}.${field.key}`, { defaultValue: '' });
  const common = { className: inputCls, value: value ?? '', onChange: (e) => onChange(e.target.value), disabled };
  let input;
  if (field.kind === 'select') {
    input = (
      <select {...common}>
        {field.options.map((o) => <option key={o} value={o}>{t(`connectors.options.${name}.${field.key}.${o}`, { defaultValue: o })}</option>)}
      </select>
    );
  } else if (field.kind === 'textarea') {
    input = <textarea rows={3} {...common} placeholder={field.placeholder} />;
  } else if (field.secret) {
    input = (
      <input
        type="password"
        autoComplete="new-password"
        {...common}
        placeholder={isSet ? t('settings.keepExistingToken') : field.placeholder}
      />
    );
  } else {
    input = <input type={field.kind === 'number' ? 'number' : 'text'} {...common} placeholder={field.placeholder} />;
  }
  return (
    <div>
      <label className="text-sm font-medium text-gray-700 mb-1 block">
        {label}
        {required && <span className="text-red-500"> *</span>}
        {field.secret && isSet && <span className="text-gray-400 font-normal"> ({t('settings.currentlySet')})</span>}
      </label>
      {hint && <p className="text-xs text-gray-500 mb-1">{hint}</p>}
      {input}
    </div>
  );
}

// Non-secret values come from the server; secrets start empty (write-only).
function initialValues(fields, config) {
  const next = {};
  fields.forEach((f) => { if (!f.secret) next[f.key] = config?.[f.key] ?? ''; });
  return next;
}

export function ConfigForm({
  name, fields: allFields, config, onSave, onClear, onTest, saving, testing,
  testResult, t, readOnly,
}) {
  // A hidden field is written by the backend, never typed.
  const fields = allFields.filter((f) => f.kind !== 'hidden');
  const [values, setValues] = useState(() => initialValues(fields, config));
  const setValue = (key, v) => setValues((vs) => ({ ...vs, [key]: v }));
  // A save returns fresh config; re-seed the form from it so a cleared
  // secret or a server side default shows up without a reload.
  const [seenConfig, setSeenConfig] = useState(config);
  if (config !== seenConfig) {
    setSeenConfig(config);
    setValues(initialValues(fields, config));
  }

  const secretsSet = fields.filter((f) => f.secret && config?.[`has_${f.key}`]);
  const submit = () => {
    const payload = {};
    fields.forEach((f) => {
      const v = values[f.key];
      if (f.secret) { if (v && String(v).trim()) payload[f.key] = String(v).trim(); return; }
      if (v !== undefined) payload[f.key] = f.kind === 'number' && v !== '' ? Number(v) : v;
    });
    onSave(payload);
    setValues((vs) => {
      const cleared = { ...vs };
      fields.forEach((f) => { if (f.secret) cleared[f.key] = ''; });
      return cleared;
    });
  };

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        {fields.filter((f) => !when(f.hidden_when, values)).map((f) => (
          <FieldInput
            required={f.required && !when(f.optional_when, values)}
            key={f.key}
            field={f}
            name={name}
            t={t}
            value={values[f.key]}
            isSet={Boolean(config?.[`has_${f.key}`])}
            onChange={(v) => setValue(f.key, v)}
            disabled={readOnly}
          />
        ))}
      </div>
      {!readOnly && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={submit} disabled={saving} className={btnPrimary}>
              {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t('common.save')}
            </button>
            {onTest && (
              <button type="button" onClick={onTest} disabled={testing} className={btnSecondary}>
                {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
                {t('settings.testConnection')}
              </button>
            )}
            {secretsSet.length > 0 && onClear && (
              <button type="button" onClick={() => onClear(secretsSet.map((f) => f.key))} disabled={saving} className={btnDanger}>
                <Trash2 className="w-3.5 h-3.5" /> {t('connectors.clearSecrets')}
              </button>
            )}
          </div>
          {testResult && (
            <div className={`text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
              {testResult.ok
                ? <>{t('settings.connectedAs')} <strong>{testResult.identity || 'ok'}</strong></>
                : <>{t('settings.testFailed')}: {testResult.error}</>}
            </div>
          )}
        </>
      )}
    </div>
  );
}

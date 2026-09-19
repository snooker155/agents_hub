import React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  inputClass, OPERATORS, EFFECT_TYPES, EFFECT_FIELD_POOL, effectFieldRole,
  effectForType,
} from './world-spec';
import { Combo } from './combo';

/**
 * The parts a world is edited with.
 *
 * A world is eleven lists of records, and every one of them is the same
 * interaction: add a row, fill some fields, remove a row. Written out
 * per section that is eleven near-identical blocks of JSX and eleven chances
 * for them to drift; written once it is a column spec per section, which is
 * also the only thing that actually differs between them.
 *
 * Everything here is controlled and pure: a section hands in its rows and gets
 * the new rows back. The page owns the draft, so an unsaved world is one
 * object, which is what makes "you have unsaved changes" answerable at all.
 */

export function Field({ label, hint, children, className = '' }) {
  return (
    <div className={className}>
      {label && (
        <label className="block text-[11px] font-semibold text-gray-600 mb-0.5">{label}</label>
      )}
      {children}
      {hint && <p className="text-[11px] text-gray-400 mt-0.5">{hint}</p>}
    </div>
  );
}

export function Card({ icon: Icon, title, description, actions, children }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide flex items-center gap-1.5">
          {Icon && <Icon className="w-4 h-4 text-indigo-500" />} {title}
        </h3>
        {actions}
      </div>
      {description && <p className="text-xs text-gray-500 mb-4">{description}</p>}
      {children}
    </div>
  );
}

/**
 * A comma-separated list of names, with the legal ones offered as suggestions.
 *
 * Free text rather than a multi-select because these lists reference things
 * that may not exist yet — a role's actions while the actions are still being
 * written — and a picker with nothing in it reads as "you cannot do this".
 * The suggestions make the common case one keystroke; the validator catches
 * what the field lets through.
 */
export function NameList({ value, onChange, options = [], placeholder = '' }) {
  const id = React.useId();
  const text = Array.isArray(value) ? value.join(', ') : (value || '');
  return (
    <>
      <input
        list={options.length ? id : undefined}
        value={text}
        placeholder={placeholder}
        onChange={(e) => onChange(
          e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
        )}
        className={inputClass}
      />
      {options.length > 0 && (
        <datalist id={id}>
          {options.map((o) => <option key={o} value={o} />)}
        </datalist>
      )}
    </>
  );
}

/**
 * A column property, which may be written as a function of the row.
 *
 * An effect's boxes change meaning with its type, so the label, the options
 * and whether the box is usable at all are answers about *this* row rather
 * than about the column — and the alternative to resolving them here is a
 * second, nearly identical list component for effects alone.
 */
function resolveColumn(column, row) {
  const out = { ...column };
  for (const key of ['label', 'options', 'placeholder', 'disabled', 'type']) {
    if (typeof column[key] === 'function') out[key] = column[key](row);
  }
  return out;
}

function Cell({ column: spec, row, onChange, suggestions }) {
  const column = resolveColumn(spec, row);
  const value = row[column.key];
  const set = (v) => onChange(column.set ? column.set(row, v)
                                         : { ...row, [column.key]: v });
  const options = column.options
    || (column.suggest ? (suggestions[column.suggest] || []) : null);

  if (column.type === 'boolean') {
    return (
      // `pl-2` puts the box where the text of the inputs beside it starts,
      // rather than hard against the edge of its column.
      <label className="flex items-center gap-1.5 text-xs text-gray-600 py-1.5 pl-2">
        <input type="checkbox" checked={!!value} onChange={(e) => set(e.target.checked)}
               className="rounded border-gray-300 text-indigo-600" />
        {column.label}
      </label>
    );
  }
  if (column.type === 'select') {
    return (
      <select value={value ?? ''} onChange={(e) => set(e.target.value)} className={inputClass}>
        {(options || []).map((o) => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
      </select>
    );
  }
  if (column.type === 'names') {
    return <NameList value={value || []} onChange={set} options={options || []}
                     placeholder={column.placeholder} />;
  }
  if (column.type === 'combo') {
    return (
      <Combo
        value={value ?? ''} options={options || []} disabled={column.disabled}
        placeholder={column.placeholder} onChange={set} className={inputClass}
      />
    );
  }
  if (column.type === 'pairs') {
    return <PairsInput value={value || {}} onChange={set} placeholder={column.placeholder} />;
  }
  const listId = options && options.length ? `${column.key}-opts` : undefined;
  return (
    <>
      <input
        type={column.type === 'number' ? 'number' : 'text'}
        list={listId}
        value={value ?? ''}
        placeholder={column.placeholder}
        onChange={(e) => set(column.type === 'number'
          ? (e.target.value === '' ? '' : Number(e.target.value))
          : e.target.value)}
        className={inputClass}
      />
      {listId && (
        <datalist id={listId}>
          {(options || []).map((o) => <option key={o.value ?? o} value={o.value ?? o} />)}
        </datalist>
      )}
    </>
  );
}

/**
 * ``key=value, key=value`` as an object.
 *
 * An entity's state and a role's stat overrides are both small flat maps, and
 * a key/value grid for two entries costs more screen than the world around it.
 */
export function PairsInput({ value, onChange, placeholder = '' }) {
  const [text, setText] = React.useState(
    Object.entries(value || {}).map(([k, v]) => `${k}=${v}`).join(', '),
  );
  // Re-sync when the row is replaced from outside (a reload, a discard) but
  // never while it is being typed in: parsing mid-word would rewrite the text
  // under the cursor.
  const mirror = React.useRef(value);
  React.useEffect(() => {
    if (JSON.stringify(mirror.current) === JSON.stringify(value)) return;
    mirror.current = value;
    setText(Object.entries(value || {}).map(([k, v]) => `${k}=${v}`).join(', '));
  }, [value]);

  const commit = (raw) => {
    setText(raw);
    const out = {};
    for (const part of raw.split(',')) {
      const [k, ...rest] = part.split('=');
      if (k && k.trim()) out[k.trim()] = rest.join('=').trim();
    }
    mirror.current = out;
    onChange(out);
  };
  return (
    <input value={text} placeholder={placeholder}
           onChange={(e) => commit(e.target.value)} className={inputClass} />
  );
}

/**
 * A list of records, one row each.
 *
 * ``columns`` is the only thing a section has to describe: its label, how it is
 * edited, and which pool of names to suggest. Rows are added from ``blank``
 * and removed by index, so nothing here knows what a location or an item is.
 */
export function RecordList({
  rows, columns, onChange, blank, addLabel, empty, suggestions = {},
}) {
  const { t } = useI18n();
  const update = (i, next) => onChange(rows.map((r, j) => (j === i ? next : r)));
  const remove = (i) => onChange(rows.filter((_, j) => j !== i));

  return (
    <div className="space-y-2">
      {rows.length === 0 && (
        <p className="text-xs text-gray-400 italic">{empty}</p>
      )}
      {rows.map((row, i) => (
        <div key={i} className="flex items-start gap-3 rounded-lg border border-gray-100 bg-gray-50/60 p-2">
          <div className="grid grid-cols-1 md:grid-cols-12 gap-2 flex-1 min-w-0">
            {columns.map((spec) => {
              const column = resolveColumn(spec, row);
              return (
              <div key={column.key} className={column.span || 'md:col-span-3'}>
                {/* A checkbox carries its own label beside the box, but it
                    still needs the line the other fields' labels occupy, or it
                    floats a row above everything it sits next to. */}
                {column.type !== 'boolean' ? (
                  <label className={`block text-[10px] font-semibold uppercase mb-0.5 ${
                    column.disabled ? 'text-gray-300' : 'text-gray-500'}`}>
                    {column.label}
                  </label>
                ) : (
                  <span aria-hidden="true" className="hidden md:block text-[10px] mb-0.5">
                    &nbsp;
                  </span>
                )}
                <Cell column={spec} row={row} suggestions={suggestions}
                      onChange={(next) => update(i, next)} />
              </div>
              );
            })}
          </div>
          <button
            onClick={() => remove(i)}
            title={t('worlds.remove')}
            // Its own lane, set off from the fields: the last column is
            // sometimes a checkbox with a word beside it, and without the rule
            // the two read as one control.
            className="p-1 mt-4 ml-1 pl-2 border-l border-gray-200 text-gray-400 hover:text-red-600 shrink-0"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...rows, { ...blank }])}
        className="inline-flex items-center text-xs font-semibold text-indigo-600 hover:text-indigo-800"
      >
        <Plus className="w-3.5 h-3.5 mr-1" /> {addLabel}
      </button>
    </div>
  );
}

/**
 * Conditions — the world's own veto, as triples.
 *
 * Three fields and no expression box, deliberately: a condition is checked by
 * the server against values it declared, so there is nothing to evaluate and
 * nothing an author can write that the engine does not already understand.
 */
export function ConditionList({ conditions, onChange, pools }) {
  const { t } = useI18n();
  return (
    <RecordList
      rows={conditions}
      onChange={onChange}
      blank={{ scope: 'global', name: '', key: '', op: '>=', value: '' }}
      addLabel={t('worlds.addCondition')}
      empty={t('worlds.noConditions')}
      suggestions={pools}
      columns={[
        { key: 'scope', label: t('worlds.scope'), type: 'select', span: 'md:col-span-2',
          options: [
            { value: 'global', label: t('worlds.scopeGlobal') },
            { value: 'stat', label: t('worlds.scopeStat') },
            { value: 'entity', label: t('worlds.scopeEntity') },
            { value: 'item', label: t('worlds.scopeItem') },
          ] },
        { key: 'name', label: t('worlds.which'), span: 'md:col-span-3', suggest: 'names' },
        { key: 'key', label: t('worlds.stateKey'), span: 'md:col-span-2',
          placeholder: t('worlds.stateKeyHint') },
        { key: 'op', label: t('worlds.operator'), type: 'select', span: 'md:col-span-2',
          options: OPERATORS },
        { key: 'value', label: t('worlds.value'), span: 'md:col-span-3' },
      ]}
    />
  );
}

/**
 * Effects — what the action does, one row each.
 *
 * The three boxes after the type are the same three fields the engine reads,
 * but what they *mean* is decided by the type: a recipient here, a destination
 * there, nothing at all for a log line. So the row asks the effect table what
 * this type uses, labels each box accordingly, offers the names the world
 * actually declared, and greys out the boxes this effect ignores — which is
 * the difference between an author knowing an effect is complete and an author
 * finding out a tick later that it did nothing.
 */
export function EffectList({ effects, onChange, pools }) {
  const { t } = useI18n();

  // Every box is described by the role its field plays for this row's type.
  const role = (key) => (row) => effectFieldRole(row.type, key);
  const labelFor = (key) => (row) => {
    const r = role(key)(row);
    return r ? t(`worlds.effectField.${r}`) : t('worlds.effectField.unused');
  };
  const optionsFor = (key) => (row) => {
    const pool = EFFECT_FIELD_POOL[role(key)(row)];
    return pool ? (pools[pool] || []) : [];
  };
  const disabledFor = (key) => (row) => !role(key)(row);
  const placeholderFor = (key) => (row) => {
    const r = role(key)(row);
    return r ? t(`worlds.effectField.${r}Hint`, { defaultValue: '' }) : '';
  };
  const box = (key, span) => ({
    key, span, type: 'combo', label: labelFor(key), options: optionsFor(key),
    disabled: disabledFor(key), placeholder: placeholderFor(key),
  });

  return (
    <RecordList
      rows={effects}
      onChange={onChange}
      blank={{ type: 'add_global', target: '', name: '', value: '' }}
      addLabel={t('worlds.addEffect')}
      empty={t('worlds.noEffects')}
      suggestions={pools}
      columns={[
        { key: 'type', label: t('worlds.effect'), type: 'select', span: 'md:col-span-3',
          options: EFFECT_TYPES,
          // Changing the type changes which boxes are read at all, so the ones
          // the new type ignores are cleared rather than left showing a value
          // nothing will use.
          set: (row, type) => effectForType(row, type) },
        box('target', 'md:col-span-3'),
        box('name', 'md:col-span-3'),
        box('value', 'md:col-span-3'),
      ]}
    />
  );
}


/**
 * The two substitution languages, told apart.
 *
 * There are two, and conflating them is the mistake: braces in *text* print
 * something, `arg:x` in a *picker* points the effect at something and prints
 * nothing at all. Both are written with the action's own first argument rather
 * than a stand-in `x`, and the same argument is then shown doing both jobs in
 * one worked example — which is the shortest way to say what the difference
 * actually is.
 */
export function PlaceholderHelp({ action, args = [], globals = [], stats = [] }) {
  const { t } = useI18n();
  const names = (list) => (list.length ? list.join(', ') : t('worlds.placeholderNone'));
  const argNames = args.map((a) => a.name).filter(Boolean);
  const sample = argNames[0] || 'location';

  const inText = [
    ['{actor}', t('worlds.placeholderActor'), ''],
    [`{arg.${sample}}`, t('worlds.placeholderArg'), names(argNames)],
    ['{global.x}', t('worlds.placeholderGlobal'), names(globals)],
    ['{stat.x}', t('worlds.placeholderStat'), names(stats)],
  ];
  const inFields = [
    ['actor', t('worlds.placeholderFieldActor'), ''],
    [`arg:${sample}`, t('worlds.placeholderFieldArg', { arg: sample }), names(argNames)],
  ];

  // Indigo carries the tokens and nothing else. A tinted panel with tinted
  // text on it was the legend nobody could read, and the one thing here worth
  // colouring is the handful of words an author copies out of it.
  const group = (title, hint, rows) => (
    <div>
      <div className="text-[11px] font-semibold text-gray-700">{title}</div>
      <p className="text-xs text-gray-500 mb-1">{hint}</p>
      <dl className="space-y-1">
        {rows.map(([token, meaning, available]) => (
          <div key={token} className="flex flex-wrap items-baseline gap-x-2 text-xs">
            <dt className="font-mono text-indigo-700 bg-white border border-gray-200 rounded px-1 shrink-0">
              {token}
            </dt>
            <dd className="text-gray-700">
              {meaning}
              {available && <span className="text-gray-500"> ({available})</span>}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-3">
      <div className="text-[11px] font-bold text-gray-600 uppercase">
        {t('worlds.placeholders')}
      </div>
      {group(t('worlds.placeholdersInText'), t('worlds.placeholdersInTextHint'), inText)}
      {group(t('worlds.placeholdersInFields'), t('worlds.placeholdersInFieldsHint'), inFields)}
      <div className="text-xs text-gray-700 space-y-0.5 border-t border-gray-200 pt-2">
        <p>{t('worlds.placeholderExampleIntro', {
          action: action || 'search', arg: sample,
        })}</p>
        <p>{t('worlds.placeholderExampleInText', { token: `{arg.${sample}}` })}</p>
        <p>{t('worlds.placeholderExampleInField', { token: `arg:${sample}` })}</p>
      </div>
    </div>
  );
}


/**
 * One rule per line — the prose the world cannot enforce but does expect.
 *
 * The typed text is held locally and the parsed lines are handed up, because
 * the parse drops blank lines: feeding those back into the textarea would
 * swallow the newline the moment somebody pressed Enter, and the field would
 * be one paragraph forever.
 */
export function LineList({ value, onChange, rows = 4, placeholder = '' }) {
  const [text, setText] = React.useState((value || []).join('\n'));
  const mirror = React.useRef(value);
  React.useEffect(() => {
    if (JSON.stringify(mirror.current) === JSON.stringify(value)) return;
    mirror.current = value;
    setText((value || []).join('\n'));
  }, [value]);

  return (
    <textarea
      rows={rows}
      value={text}
      placeholder={placeholder}
      onChange={(e) => {
        setText(e.target.value);
        const lines = e.target.value.split('\n').map((l) => l.trim()).filter(Boolean);
        mirror.current = lines;
        onChange(lines);
      }}
      className={`${inputClass} font-normal`}
    />
  );
}

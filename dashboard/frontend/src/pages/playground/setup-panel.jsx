import React, { useEffect, useRef, useState } from 'react';
import { BookText, Save, Settings2, Users, Zap, Loader } from 'lucide-react';
import { updateScenario } from '../../api';
import { useToast } from '../../components/toast';
import { useI18n } from '../../i18n';
import { CharacterCard, CharacterDialog, ModelSelect } from './characters';
import { emptyCharacter } from './roles';

/** Setup mode: environment parameters (declared by the env) and role overlays. */
export function SetupPanel({
  scenario, environments, agents, models, onSaved, onOpenNarrative, className = '',
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [draft, setDraft] = useState(scenario);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // The role being edited: {index, role}. index -1 is a role being added, so
  // the dialog does not have to know the difference until it saves.
  const [editing, setEditing] = useState(null);

  // The chat in the sidebar edits the same scenario this form does, so the
  // stored scenario can change while you are typing in it. Re-sync when it
  // does — but never over unsaved edits: silently replacing what someone just
  // typed is worse than leaving a stale field they can still save or discard.
  const syncedRef = useRef(scenario);
  const [outOfSync, setOutOfSync] = useState(false);
  useEffect(() => {
    if (scenario === syncedRef.current) return;
    const previous = syncedRef.current;
    syncedRef.current = scenario;
    setDraft((current) => {
      if (JSON.stringify(current) === JSON.stringify(previous)) {
        setOutOfSync(false);
        return scenario;
      }
      // There are edits to protect. The name is not one of them — the header
      // owns it, this form has no field for it — so adopt it and weigh only
      // what the form itself could have written. Otherwise renaming a
      // scenario from the header would announce itself as a conflict.
      const settled = (v) => JSON.stringify({ ...v, name: '', updated_at: '' });
      setOutOfSync(settled(scenario) !== settled(previous));
      return { ...current, name: scenario.name };
    });
  }, [scenario]);

  const envSpec = environments.find((e) => e.env_id === draft.environment);
  // Everything on this panel edits a draft, including the role dialog — and
  // the dialog's own Save button makes it easy to believe a role is stored
  // when only the draft holds it. So say so, wherever you are on the page.
  const dirty = JSON.stringify(draft) !== JSON.stringify(scenario);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data } = await updateScenario(draft.scenario_id, draft);
      // Mark this version as the one the form is synced to *before* handing it
      // up: the parent's state change re-runs the sync effect, and without this
      // the edits we just saved would read as an unsynced conflict.
      syncedRef.current = data;
      setDraft(data);
      setOutOfSync(false);
      onSaved(data);
      toast.success(t('playground.scenarioSaved'));
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  /** Commit the dialog's copy back into the draft — new role, or edited one. */
  const commitRole = (index, role) => {
    const roles = index < 0
      ? [...draft.roles, role]
      : draft.roles.map((r, j) => (j === index ? role : r));
    setDraft({ ...draft, roles });
    setEditing(null);
  };

  return (
    <div className={`space-y-6 ${className}`}>
      {error && <div className="text-xs text-red-600">{error}</div>}

      {outOfSync && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2">
          <span className="text-xs text-sky-800">{t('playground.changedElsewhere')}</span>
          <button
            onClick={() => { setDraft(scenario); setOutOfSync(false); }}
            className="text-xs font-semibold text-sky-700 hover:text-sky-900 underline"
          >
            {t('playground.discardAndReload')}
          </button>
        </div>
      )}

      {dirty && (
        <div className="sticky top-2 z-10 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 shadow-sm">
          <span className="text-xs text-amber-800">{t('playground.unsavedChanges')}</span>
          <div className="flex items-center gap-3">
            {/* The way back, as on the world form: a banner that only offers
                Save leaves undoing a change the author regrets to Escape and
                a reload. Going back to the stored scenario is the same
                one-liner the out-of-sync banner above already does. */}
            <button onClick={() => setDraft(scenario)} disabled={saving}
                    className="text-xs font-semibold text-amber-800 underline disabled:opacity-50">
              {t('playground.discard')}
            </button>
            <button
              onClick={save} disabled={saving}
              className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
              {t('playground.saveScenario')}
            </button>
          </div>
        </div>
      )}

      {/* The description, and only it: the name is edited in the page header,
          where it is on screen in every mode. No heading over a single field —
          the label is the heading. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <label className="block text-xs font-semibold text-gray-600 mb-0.5">
          {t('playground.description')}
        </label>
        <textarea
          rows={3}
          value={draft.description || ''}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
        />
        <p className="text-[11px] text-gray-400 mt-0.5">{t('playground.descriptionHint')}</p>
        <button
          type="button" onClick={onOpenNarrative}
          className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-800"
        >
          <BookText className="w-3.5 h-3.5" /> {t('playgroundNarrative.openFromSetup')}
        </button>
      </div>

      {/* Environment parameters — rendered generically from the declared schema,
          so a new environment needs no frontend change. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-1 flex items-center gap-1.5">
          <Settings2 className="w-4 h-4 text-indigo-500" /> {t('playground.environment')}
        </h3>
        <p className="text-xs text-gray-500 mb-4">{envSpec?.description}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(envSpec?.params || []).map((p) => (
            <div key={p.name}>
              <label className="block text-xs font-semibold text-gray-600 mb-0.5">{p.name}</label>
              <input
                value={draft.env_params[p.name] ?? (Array.isArray(p.default) ? p.default.join(', ') : p.default)}
                onChange={(e) => setDraft({
                  ...draft,
                  env_params: { ...draft.env_params, [p.name]: e.target.value },
                })}
                className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
              />
              <p className="text-[11px] text-gray-400 mt-0.5">{p.description}</p>
            </div>
          ))}
        </div>

        {envSpec?.actions?.length > 0 && (
          <div className="mt-4 pt-3 border-t border-gray-100">
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
              {t('playground.actionApiTheOnlyThings')}
            </div>
            <div className="space-y-1">
              {envSpec.actions.map((a) => (
                <div key={a.name} className="text-xs">
                  <span className="font-mono font-semibold text-indigo-700">{a.name}</span>
                  <span className="text-gray-500"> — {a.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Activation — who gets a turn, and why. The single knob that decides
          whether this is a simulated clock or a reactive sandbox. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Zap className="w-4 h-4 text-indigo-500" /> {t('playground.activationTitle')}
        </h3>
        {/* The grace period belongs beside the mode it qualifies, not under
            both of them: it is the second half of choosing "triggered", and a
            row of its own read as a third setting that applied either way.
            It stays in place when the mode is synchronous, greyed rather than
            gone — a control that vanishes takes the row's height with it, and
            the block jumps every time the mode is toggled. */}
        <div className="flex flex-col md:flex-row md:items-stretch gap-3">
          {['synchronous', 'triggered'].map((mode) => (
            <button
              key={mode}
              onClick={() => setDraft({ ...draft, activation: mode })}
              className={`flex-1 min-w-0 text-left rounded-lg border p-3 ${
                (draft.activation || 'synchronous') === mode
                  ? 'border-indigo-300 bg-indigo-50'
                  : 'border-gray-200 hover:bg-gray-50'
              }`}
            >
              <div className="text-xs font-bold text-gray-900">
                {t(`playground.activation.${mode}`)}
              </div>
              <p className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                {t(`playground.activationHint.${mode}`)}
              </p>
            </button>
          ))}
          {(() => {
            const idle = (draft.activation || 'synchronous') === 'triggered';
            return (
              <div className={`md:w-52 md:shrink-0 md:pl-3 md:border-l md:border-gray-100 ${
                idle ? '' : 'opacity-50'
              }`}>
                <label className="block text-[11px] font-semibold text-gray-600 mb-0.5">
                  {t('playground.limits.idleGrace')}
                </label>
                <input
                  type="number" min={0} value={draft.idle_grace_seconds ?? 0}
                  disabled={!idle}
                  onChange={(e) => setDraft({
                    ...draft, idle_grace_seconds: parseFloat(e.target.value) || 0,
                  })}
                  className={`w-full text-sm border border-gray-300 rounded-md px-2 py-1.5 ${
                    idle ? '' : 'bg-gray-50 cursor-not-allowed'
                  }`}
                />
                <p className="text-[11px] text-gray-400 mt-0.5 leading-snug">
                  {idle ? t('playground.idleGraceHint') : t('playground.triggeredOnly')}
                </p>
              </div>
            );
          })()}
        </div>
      </div>

      {/* The cast — one card per character, edited in a dialog.
          A character carries eight fields including two paragraphs of prose,
          and laid out inline that is a wall of inputs that hides the only
          thing you scan the list for: who is in this world and what they
          want. So the block shows cards and the editing happens in a dialog. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide flex items-center gap-1.5">
            <Users className="w-4 h-4 text-indigo-500" /> {t('playground.characters')}
          </h3>
          <button
            onClick={() => setEditing({ index: -1, role: emptyCharacter(agents) })}
            className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
          >
            {t('playground.addCharacter')}
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          {t('playground.charactersDescription')}
        </p>

        {draft.roles.length === 0 ? (
          <p className="text-xs text-gray-400 italic">{t('playground.noCharactersYet')}</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {draft.roles.map((r, i) => (
              <CharacterCard
                key={i}
                role={r}
                agent={agents.find((a) => a.id === r.agent_id)}
                world={envSpec}
                triggered={(draft.activation || 'synchronous') === 'triggered'}
                onOpen={() => setEditing({ index: i, role: r })}
                onRemove={() => setDraft({
                  ...draft, roles: draft.roles.filter((_, j) => j !== i),
                })}
              />
            ))}
          </div>
        )}
      </div>

      {/* Run-level parameters */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">{t('playground.runLimits')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            ['max_ticks', t('playground.limits.maxTicks'), 'number', ''],
            ['seed', t('playground.limits.seed'), 'number', ''],
            ['max_concurrent', t('playground.limits.maxConcurrent'), 'number', ''],
            // A timeout on silence, not on the answer: a model still streaming
            // is still working, and cutting it off throws away a reply that
            // was on its way.
            ['stall_timeout', t('playground.limits.stallTimeout'), 'number',
              t('playground.limits.stallTimeoutHint')],
            ['max_turn_seconds', t('playground.limits.maxTurnSeconds'), 'number',
              t('playground.limits.maxTurnSecondsHint')],
            ['cost_ceiling', t('playground.limits.costCeiling'), 'number', ''],
            // Empty means no wall clock at all, which is worth saying on the
            // field: a blank number input otherwise reads as "unset" and the
            // user has no way to know whether that is allowed.
            ['max_wall_seconds', t('playground.limits.wallClock'), 'number',
              t('playground.limits.wallClockHint')],
          ].map(([key, label, type, hint]) => (
            <div key={key}>
              <label className="block text-[11px] font-semibold text-gray-600 mb-0.5" title={hint}>{label}</label>
              <input
                type={type} value={draft[key] ?? ''}
                onChange={(e) => setDraft({
                  ...draft,
                  [key]: type === 'number'
                    ? (e.target.value === '' ? null : parseFloat(e.target.value))
                    : e.target.value,
                })}
                className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
              />
              {hint && <p className="text-[11px] text-gray-400 mt-0.5">{hint}</p>}
            </div>
          ))}
          {/* Provider and model are picked as one pair: chosen separately they
              can name a model the provider's client does not serve, which only
              surfaces as a failed first tick.

              One cell wide, not two: seven numeric fields plus this one fill
              the grid exactly, so the model sits on the second row instead of
              being pushed onto a third of its own with a hole beside it. */}
          <div>
            <label className="block text-[11px] font-semibold text-gray-600 mb-0.5">
              {t('playground.limits.defaultModel')}
            </label>
            <ModelSelect
              provider={draft.default_provider} model={draft.default_model} options={models}
              placeholder={t('playground.inheritFromWorkspace')}
              onChange={({ provider, model }) => setDraft({
                ...draft, default_provider: provider, default_model: model,
              })}
              className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
            />
          </div>
        </div>
        <div className="flex justify-end mt-4">
          <button
            onClick={save} disabled={saving}
            className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
            {t('playground.saveScenario')}
          </button>
        </div>
      </div>

      {editing && (
        <CharacterDialog
          role={editing.role}
          isNew={editing.index < 0}
          agents={agents}
          models={models}
          objectives={envSpec?.objectives || []}
          world={envSpec}
          triggered={(draft.activation || 'synchronous') === 'triggered'}
          onSave={(role) => commitRole(editing.index, role)}
          onRemove={editing.index < 0 ? null : () => {
            setDraft({ ...draft, roles: draft.roles.filter((_, j) => j !== editing.index) });
            setEditing(null);
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

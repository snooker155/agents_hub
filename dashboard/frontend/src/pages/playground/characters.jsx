import React, { useEffect, useState } from 'react';
import { Lock, Save, Trash2, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { Combo } from './combo';
import { worldRoleFor, roleGrants, roleInherits } from './roles';

/**
 * A character: the card that summarises one, the dialog that edits the whole
 * of it, and the model picker both the dialog and the setup form's own
 * default-model field use.
 *
 * ``ModelSelect`` lives here rather than in ``setup-panel.jsx``: the setup
 * form already has to import ``CharacterCard`` and ``CharacterDialog`` from
 * this file for the cast section, and importing the model picker back the
 * other way would make the two files depend on each other. One direction only.
 */

/**
 * Provider + model as a single choice, from the enabled catalogue only.
 *
 * Typing a model name by hand was the old behaviour and it had three failure
 * modes: a typo only showed up as a failed tick, a model could be handed to a
 * provider that does not serve it, and an unpriced pair made both the estimate
 * and the cost ceiling read zero. A pair that came from the catalogue has none
 * of them.
 */
export function ModelSelect({ provider, model, options, placeholder, onChange, className }) {
  const value = model ? `${provider || ''}::${model}` : '';
  const byProvider = options.reduce((acc, o) => {
    (acc[o.provider] = acc[o.provider] || []).push(o.model);
    return acc;
  }, {});
  // A stored pair the catalogue no longer offers stays selectable — otherwise
  // editing an unrelated field would silently drop the scenario's model.
  const known = options.some((o) => `${o.provider}::${o.model}` === value);

  return (
    <select
      value={value}
      onChange={(e) => {
        const v = e.target.value;
        const i = v.indexOf('::');
        onChange(i === -1
          ? { provider: null, model: null }
          : { provider: v.slice(0, i) || null, model: v.slice(i + 2) || null });
      }}
      className={className}
    >
      <option value="">{placeholder}</option>
      {value && !known && (
        <option value={value}>{model}{provider ? ` (${provider})` : ''}</option>
      )}
      {Object.entries(byProvider).map(([p, ids]) => (
        <optgroup key={p} label={p}>
          {ids.map((id) => <option key={`${p}::${id}`} value={`${p}::${id}`}>{id}</option>)}
        </optgroup>
      ))}
    </select>
  );
}


/** What a character is, at a glance: who it is, what it wants, how it is wired. */
export function CharacterCard({ role, agent, world, triggered, onOpen, onRemove }) {
  const { t } = useI18n();
  // Cast in one of the world's roles, or in the generic role the world falls
  // back to. Only the second is worth an amber line, and only when this world
  // declares roles at all: then somebody meant to pick one and did not, and
  // the character will quietly play something else.
  const cast = worldRoleFor(world, role.role);
  const unbound = (world?.roles || []).length > 0 && !cast;
  const grants = roleGrants(world, cast, t);
  const chips = [];
  if (role.model) chips.push(role.model);
  if (role.objective) chips.push(role.objective);
  chips.push(t('playground.memoryChip', { n: role.memory_horizon ?? 0 }));
  if (triggered && role.wake_every > 0) {
    chips.push(t('playground.wakeChip', { n: role.wake_every }));
  }
  if (triggered && role.starts) chips.push(t('playground.startsChip'));
  if (triggered && role.npc) chips.push(t('playground.npcChip'));

  return (
    <div className="rounded-lg border border-gray-200 p-3 flex flex-col gap-2 hover:border-indigo-200">
      <div className="flex items-start gap-2">
        {/* The name is the handle: it is what the others and the log call
            this character, so it is the heading and the way into the full
            record. */}
        <button onClick={onOpen} className="flex-1 min-w-0 text-left group">
          <div className="text-sm font-bold text-gray-900 truncate group-hover:text-indigo-700">
            {role.name || agent?.name || agent?.id || t('playground.unnamedCharacter')}
          </div>
          <div className="text-[11px] text-gray-500 truncate">
            {agent?.name || agent?.id || t('playground.noAgentSelected')}
            {role.role ? ` · ${role.role}` : ''}
          </div>
        </button>
        <button
          onClick={onRemove}
          title={t('playground.characterDialog.remove')}
          className="p-1 text-gray-300 hover:text-red-600 shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <p className={`text-xs line-clamp-3 ${role.goal ? 'text-gray-600' : 'text-gray-400 italic'}`}>
        {role.goal || t('playground.noGoalSet')}
      </p>

      {grants.length > 0 && (
        <p className={`text-[10px] leading-snug ${
          unbound ? 'text-amber-700' : 'text-gray-500'
        }`}>
          {[unbound ? t('playground.worldRole.unboundChip') : null, ...grants]
            .filter(Boolean).join(' · ')}
        </p>
      )}

      <div className="flex flex-wrap gap-1 mt-auto">
        {role.private_knowledge?.trim() && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[10px] font-medium">
            <Lock className="w-2.5 h-2.5" />{t('playground.privateKnowledgeChip')}
          </span>
        )}
        {chips.map((c) => (
          <span key={c} className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px]">
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}


/**
 * The whole character in one dialog.
 *
 * It edits a copy and hands it back on save, so closing a half-typed goal
 * leaves the scenario as it was — the same contract as every other dialog
 * here, and the reason the card can stay a read-only summary.
 */
export function CharacterDialog({ role, isNew, agents, models, objectives, world,
                                  triggered, onSave, onRemove, onClose }) {
  const { t } = useI18n();
  const [copy, setCopy] = useState(role);
  const set = (patch) => setCopy((prev) => ({ ...prev, ...patch }));

  const worldRoles = world?.roles || [];
  const cast = worldRoleFor(world, copy.role);
  // Four things this field can be: bound, nothing typed, typed and matching
  // nothing, or no roles to bind to at all. Each gets a sentence saying which
  // — and then, whichever it was, the same line the world page prints, because
  // what this character will actually start with is the question the field is
  // really being asked.
  const roleLead = cast
    ? cast.description
    : (!worldRoles.length
      ? t('playground.worldRole.noneDeclared')
      : (copy.role.trim()
        ? t('playground.worldRole.unknown')
        : t('playground.worldRole.unset')));
  // One line, not two. The sentence and what it costs are the same thought,
  // and the field they sit under is one row high — a second line pushes the
  // grid apart for a reader who was going to read both anyway.
  const roleHint = [
    roleLead,
    cast ? null : t('playground.worldRole.unboundChip'),
    ...roleGrants(world, cast, t),
  ].filter(Boolean).join(' · ');

  // Escape closes: a dialog over a form the user was already filling in must
  // not be a trap.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const field = 'w-full text-sm border border-gray-300 rounded-md px-2 py-1.5';
  const label = 'block text-[11px] font-bold text-gray-500 uppercase mb-1';

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900">
            {isNew ? t('playground.characterDialog.new') : t('playground.characterDialog.edit')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4 overflow-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={label}>{t('playground.characterDialog.agent')}</label>
              <select
                value={copy.agent_id} onChange={(e) => set({ agent_id: e.target.value })}
                className={field}
              >
                <option value="">{t('playground.selectAgent')}</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.name')}</label>
              <input
                value={copy.name} onChange={(e) => set({ name: e.target.value })}
                placeholder={t('playground.inWorldName')} className={field}
              />
            </div>
            <div>
              {/* Where the world declares roles this is an ordinary select, and
                  looks like every other one in this dialog, because the answer
                  really is one of a known list — plus "no role", which is a
                  choice with consequences of its own rather than an empty
                  field. A value stored before the world had that role stays
                  selectable: opening a dialog must not quietly rewrite a
                  scenario, and the line underneath says what it now means.
                  Worlds that declare no roles keep a free text field — there
                  the string binds to nothing and is prose for the prompt. */}
              <label className={label}>{t('playground.characterDialog.role')}</label>
              {worldRoles.length ? (
                <Combo
                  value={copy.role}
                  options={worldRoles}
                  emptyLabel={t('playground.worldRole.none')}
                  placeholder={t('playground.worldRole.pick')}
                  onChange={(role) => set({ role })}
                  className={field}
                />
              ) : (
                <input
                  value={copy.role} onChange={(e) => set({ role: e.target.value })}
                  placeholder={t('playground.worldRole.freeText')}
                  className={field}
                />
              )}
              {roleHint && (
                <p className={`text-[11px] mt-0.5 leading-snug ${
                  worldRoles.length && !cast ? 'text-amber-700' : 'text-gray-400'
                }`}>
                  {roleHint}
                </p>
              )}
            </div>
            <div>
              {/* A combobox, not a select: the environment's own objectives are
                  offered, but a scenario may target one the env does not
                  declare yet, and that must not need a code change. */}
              <label className={label}>{t('playground.characterDialog.objective')}</label>
              <input
                list="role-dialog-objectives"
                value={copy.objective || ''}
                onChange={(e) => set({ objective: e.target.value.trim() || null })}
                placeholder={t('playground.noScoredObjective')}
                title={t('playground.scoredObjectiveOverFinalState')}
                className={field}
              />
              <datalist id="role-dialog-objectives">
                {objectives.map((o) => <option key={o} value={o} />)}
              </datalist>
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.model')}</label>
              <ModelSelect
                provider={copy.provider} model={copy.model} options={models}
                placeholder={roleInherits(agents.find((a) => a.id === copy.agent_id), t)}
                onChange={(patch) => set(patch)}
                className={field}
              />
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.memory')}</label>
              <input
                type="number" min={0} max={50} value={copy.memory_horizon ?? 0}
                onChange={(e) => set({ memory_horizon: parseInt(e.target.value, 10) || 0 })}
                className={field}
              />
              <p className="text-[11px] text-gray-400 mt-0.5">
                {t('playground.howManyPastTicksThis')}
              </p>
            </div>
          </div>

          {/* Only a triggered world has anything to do with these:
              synchronously, everyone acts every tick regardless. */}
          {triggered && (
            <div className="rounded-lg border border-gray-200 p-3">
              <div className="text-[11px] font-bold text-gray-500 uppercase mb-2">
                {t('playground.characterDialog.triggers')}
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  {t('playground.wakeEvery')}
                  <input
                    type="number" min={0} max={100} value={copy.wake_every ?? 0}
                    onChange={(e) => set({ wake_every: parseInt(e.target.value, 10) || 0 })}
                    title={t('playground.wakeEveryHint')}
                    className="text-sm border border-gray-300 rounded-md px-2 py-1.5 w-20"
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox" checked={!!copy.starts}
                    onChange={(e) => set({ starts: e.target.checked })}
                  />
                  {t('playground.starts')}
                </label>
                {/* The opposite end of the same dial: a role that waits to be
                    reached rather than one that goes looking for a turn. */}
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox" checked={!!copy.npc}
                    onChange={(e) => set({ npc: e.target.checked, starts: e.target.checked ? false : copy.starts })}
                  />
                  {t('playground.npc')}
                </label>
              </div>
              <p className="text-[11px] text-gray-400 mt-2">{t('playground.startsHint')}</p>
              <p className="text-[11px] text-gray-400 mt-1">{t('playground.npcHint')}</p>
            </div>
          )}

          <div>
            <label className={label}>{t('playground.characterDialog.goal')}</label>
            <textarea
              value={copy.goal} onChange={(e) => set({ goal: e.target.value })}
              placeholder={t('playground.statedGoalWhatThisAgent')} rows={4}
              className={`${field} resize-y`}
            />
          </div>
          <div>
            <label className={label}>{t('playground.characterDialog.privateKnowledge')}</label>
            <textarea
              value={copy.private_knowledge}
              onChange={(e) => set({ private_knowledge: e.target.value })}
              placeholder={t('playground.privateKnowledgeWhatOnlyThis')} rows={4}
              className={`${field} resize-y`}
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 px-5 py-3 border-t border-gray-200">
          {onRemove ? (
            <button
              onClick={onRemove}
              className="inline-flex items-center text-xs font-semibold text-red-600 hover:text-red-700"
            >
              <Trash2 className="w-3.5 h-3.5 mr-1" /> {t('playground.characterDialog.remove')}
            </button>
          ) : <span />}
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('playground.cancel')}
            </button>
            <button
              onClick={() => onSave(copy)}
              className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              <Save className="w-4 h-4 mr-1.5" /> {t('playground.characterDialog.save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

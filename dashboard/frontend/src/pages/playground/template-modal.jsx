import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, FlaskConical, Loader, Save, X } from 'lucide-react';
import { getAgents, getTeams } from '../../api';
import { createScenarioFromTemplate, getScenarioTemplates } from '../../api/lab';
import { useI18n } from '../../i18n';

/**
 * "New from template": a button that lists the ready made scenarios, and the
 * small dialog that casts the chosen one.
 *
 * A template is complete except for who plays it, so the dialog asks exactly
 * that: one agent for every role, or a team whose members play it. The list
 * is read from the backend (GET /playground/scenarios/templates) so a new
 * template needs no change here.
 */
export function TemplateMenu({ onPick }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState(null);
  const [error, setError] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    if (!open || templates) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getScenarioTemplates();
        if (!cancelled) setTemplates(data?.templates || []);
      } catch {
        if (!cancelled) { setTemplates([]); setError(t('playground.templates.loadFailed')); }
      }
    })();
    return () => { cancelled = true; };
  }, [open, templates, t]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex items-center px-3 py-2 text-sm font-medium text-indigo-700 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50"
      >
        <FlaskConical className="w-4 h-4 mr-1.5" /> {t('playground.templates.newFromTemplate')}
        <ChevronDown className="w-3.5 h-3.5 ml-1" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 mt-1 w-72 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1">
          {templates == null && (
            <div className="px-3 py-2 text-xs text-gray-400 flex items-center gap-1.5">
              <Loader className="w-3.5 h-3.5 animate-spin" /> {t('playground.templates.loading')}
            </div>
          )}
          {error && <div className="px-3 py-2 text-xs text-red-600">{error}</div>}
          {templates && templates.length === 0 && !error && (
            <div className="px-3 py-2 text-xs text-gray-400 italic">{t('playground.templates.none')}</div>
          )}
          {(templates || []).map((tpl) => (
            <button
              key={tpl.id}
              type="button"
              role="menuitem"
              onClick={() => { setOpen(false); onPick(tpl); }}
              className="w-full text-left px-3 py-2 hover:bg-indigo-50"
            >
              <div className="text-sm font-semibold text-gray-900">{tpl.name}</div>
              <div className="text-[11px] text-gray-500">{tpl.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Cast a template: one agent for every role, or a team. */
export function TemplateModal({ template, workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [castBy, setCastBy] = useState('agent');
  const [agents, setAgents] = useState([]);
  const [teams, setTeams] = useState([]);
  const [agentId, setAgentId] = useState('');
  const [teamId, setTeamId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getAgents(workspace);
        const list = data?.agents || data || [];
        if (!cancelled) {
          setAgents(list);
          setAgentId((current) => current || list[0]?.id || '');
        }
      } catch { /* an empty picker says enough */ }
      try {
        const { data } = await getTeams(workspace);
        if (!cancelled) setTeams(data?.teams || []);
      } catch { /* teams are optional */ }
    })();
    return () => { cancelled = true; };
  }, [workspace]);

  const submit = async () => {
    setError('');
    if (castBy === 'agent' && !agentId) { setError(t('playground.templates.pickAgent')); return; }
    if (castBy === 'team' && !teamId) { setError(t('playground.templates.pickTeam')); return; }
    setSaving(true);
    try {
      const { data } = await createScenarioFromTemplate({
        template: template.id,
        workspace,
        agentId: castBy === 'agent' ? agentId : undefined,
        teamId: castBy === 'team' ? teamId : undefined,
      });
      onCreated(data);
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.createFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md" role="dialog" aria-label={template.name}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
            <FlaskConical className="w-4 h-4 text-indigo-500" /> {template.name}
          </h3>
          <button onClick={onClose} aria-label={t('playground.cancel')} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <p className="text-xs text-gray-500">{template.description}</p>
          {error && <div className="text-xs text-red-600" role="alert">{error}</div>}
          <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
            {['agent', 'team'].map((k) => (
              <button
                key={k} type="button" onClick={() => setCastBy(k)}
                className={`px-3 py-1.5 text-xs font-semibold ${
                  castBy === k ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600'
                }`}
              >
                {t(`playground.templates.castBy.${k}`)}
              </button>
            ))}
          </div>
          {castBy === 'agent' ? (
            <div>
              <label htmlFor="template-agent" className="block text-xs font-bold text-gray-500 uppercase mb-1">
                {t('playground.templates.agentLabel')}
              </label>
              <select
                id="template-agent"
                value={agentId} onChange={(e) => setAgentId(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-md px-3 py-2"
              >
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
              </select>
              <p className="text-[11px] text-gray-400 mt-1">{t('playground.templates.agentHint')}</p>
            </div>
          ) : (
            <div>
              <label htmlFor="template-team" className="block text-xs font-bold text-gray-500 uppercase mb-1">
                {t('playground.templates.teamLabel')}
              </label>
              <select
                id="template-team"
                value={teamId} onChange={(e) => setTeamId(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-md px-3 py-2"
              >
                <option value="">{t('playground.templates.chooseTeam')}</option>
                {teams.map((tm) => <option key={tm.team_id} value={tm.team_id}>{tm.name || tm.team_id}</option>)}
              </select>
              <p className="text-[11px] text-gray-400 mt-1">{t('playground.templates.teamHint')}</p>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('playground.cancel')}
            </button>
            <button
              onClick={submit} disabled={saving}
              className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
              {t('playground.create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TemplateModal;

import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  UsersRound, Plus, Trash2, AlertTriangle, X, Crown, ArrowRight, Radio,
} from 'lucide-react';
import { getTeams, createTeam, deleteTeam, getTeamRuns, getAgents } from '../api';
import { useWorkspace } from '../components/workspace';
import {
  MODE_BADGE, modeHelp, modeLabel, modeOptions, modeSummary, STATUS_STYLES,
  isLive,
} from '../components/teamModes';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
/**
 * Teams — the roster of rosters.
 *
 * This page answers one question: which teams exist and what is each one doing
 * right now. Everything about a single team — its charter, its members, its
 * settings and every run it has done — lives on that team's own page, because
 * the two are different jobs and sharing one screen made both of them cramped.
 */

const emptyTeam = (workspace) => ({
  name: '', description: '', workspace: workspace || null, mode: 'centralized',
  charter: '', leader_agent_id: null, leader_name: '', entry_agent_id: null,
  members: [], max_rounds: 6, max_concurrent: 4, turn_timeout: 300,
  cost_ceiling: null, max_wall_seconds: 3600, allow_direct_messages: true,
  synthesize: true,
});

function TeamCard({ team, lastRun, onDelete }) {
  const { t } = useI18n();
  const live = isLive(lastRun?.status);
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow flex flex-col">
      <Link to={`/teams/${team.team_id}`} className="p-4 flex-1 min-w-0">
        <div className="flex items-start gap-2">
          <h3 className="text-base font-bold text-gray-900 truncate flex-1">{team.name}</h3>
          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${MODE_BADGE[team.mode] || MODE_BADGE.parallel}`}>
            {modeLabel(team.mode, t)}
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{modeSummary(team.mode, t)}</p>
        {team.description && (
          <p className="text-sm text-gray-600 mt-2 line-clamp-2">{team.description}</p>
        )}

        <div className="mt-3 flex flex-wrap gap-1.5">
          {team.members.map((m) => (
            <span
              key={m.agent_id}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-700 bg-gray-100 rounded-full px-2 py-0.5"
            >
              {team.leader_agent_id === m.agent_id && <Crown className="w-3 h-3 text-amber-600" />}
              {m.display_name}
            </span>
          ))}
          {team.members.length === 0 && (
            <span className="text-xs text-gray-400 italic">{t('teams.noMembersYet')}</span>
          )}
        </div>

        {team.missing_agents?.length > 0 && (
          <p className="mt-2 text-xs text-red-600">
            {t('teams.missingAgents', { count: team.missing_agents.length })}
          </p>
        )}
        {team.members_without_manifest?.length > 0 && (
          <p className="mt-2 text-xs text-amber-700">
            {t('teams.noManifestFor', { names: team.members_without_manifest.join(', ') })}
          </p>
        )}
      </Link>

      <div className="border-t border-gray-100 px-4 py-2.5 flex items-center gap-2">
        {lastRun ? (
          <>
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[lastRun.status] || 'bg-gray-100 text-gray-600'}`}>
              {lastRun.status}
            </span>
            {live && <Radio className="w-3.5 h-3.5 text-blue-500 animate-pulse" />}
            <span className="text-xs text-gray-500 truncate">
              {new Date(lastRun.started_at).toLocaleString()}
            </span>
          </>
        ) : (
          <span className="text-xs text-gray-400 italic">{t('teams.neverRun')}</span>
        )}
        <button
          onClick={onDelete}
          className="ml-auto p-1 text-gray-400 hover:text-red-600"
          title={t('teams.deleteThisTeam')}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
        <Link
          to={`/teams/${team.team_id}`}
          className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
        >
          Open <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </div>
  );
}

export default function Teams() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const navigate = useNavigate();
  const [teams, setTeams] = useState([]);
  const [agents, setAgents] = useState([]);
  const [lastRuns, setLastRuns] = useState({});
  const [showNew, setShowNew] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    Promise.all([getTeams(selectedWorkspace), getTeamRuns()])
      .then(([{ data }, { data: runs }]) => {
        setTeams(data.teams || []);
        // Runs come back newest first, so the first one seen for a team is its latest.
        const latest = {};
        (runs.runs || []).forEach((r) => {
          if (!latest[r.team_id]) latest[r.team_id] = r;
        });
        setLastRuns(latest);
      })
      .catch(() => setTeams([]));
  }, [selectedWorkspace]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getAgents(selectedWorkspace);
        setAgents(data.agents || data || []);
      } catch { /* the first-member default degrades to an empty roster */ }
    })();
  }, [selectedWorkspace]);

  const handleCreate = async (name, mode) => {
    try {
      // A team is created with its first member already chosen, because an empty
      // roster cannot be saved — the API rejects it, and rightly so.
      const first = agents[0];
      const { data } = await createTeam({
        ...emptyTeam(selectedWorkspace), name, mode,
        leader_agent_id: mode === 'centralized' ? (first?.id || null) : null,
        members: first ? [{
          agent_id: first.id, name: first.name || first.id, role: '',
          manifest: '', goal: '',
        }] : [],
      });
      setShowNew(false);
      navigate(`/teams/${data.team_id}?tab=settings`);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('teams.createFailed'));
    }
  };

  const handleDelete = async (team) => {
    if (!window.confirm(t('teams.confirmDelete', { name: team.name }))) return;
    try {
      await deleteTeam(team.team_id);
      load();
    } catch (e) {
      setMessage(e.response?.data?.detail || t('teams.deleteFailed'));
    }
  };

  return (
    <PageContainer>
      <PageHeader
        icon={UsersRound}
        title={t('teams.teams')}
        description={t('teams.aFixedSmallCastOf')}
        actions={
          <button
            onClick={() => setShowNew(true)}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4 mr-1.5" /> {t('teams.newTeam2')}
          </button>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      {teams.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
          <UsersRound className="w-8 h-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm text-gray-500 max-w-md mx-auto">
            {t('teams.emptyState')}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {teams.map((team) => (
            <TeamCard
              key={team.team_id} team={team} lastRun={lastRuns[team.team_id]}
              onDelete={(e) => { e.preventDefault(); handleDelete(team); }}
            />
          ))}
        </div>
      )}

      {showNew && <NewTeamModal onClose={() => setShowNew(false)} onCreate={handleCreate} />}
    </PageContainer>
  );
}

function NewTeamModal({ onClose, onCreate }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [mode, setMode] = useState('centralized');
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-base font-bold text-gray-900">{t('teams.newTeam')}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teams.name')}</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)} autoFocus
              placeholder={t('teams.launchCrew')}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teams.howItIsRun')}</label>
            <select
              value={mode} onChange={(e) => setMode(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
            >
              {modeOptions(t).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-xs text-gray-500 mt-1">{modeHelp(mode, t)}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-800">
            {t('teams.cancel')}
          </button>
          <button
            onClick={() => onCreate(name.trim(), mode)}
            disabled={!name.trim()}
            className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {t('teams.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

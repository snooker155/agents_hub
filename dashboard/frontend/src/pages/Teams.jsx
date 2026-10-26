import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  UsersRound, Plus, Trash2, AlertTriangle, X, Crown, ArrowRight, Loader2,
} from 'lucide-react';
import { getTeams, createTeam, deleteTeam, getTeamRuns, getAgents } from '../api';
import { useWorkspace } from '../components/workspace';
import {
  MODE_BADGE, modeHelp, modeLabel, modeOptions, modeSummary,
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

const RUN_DOT = {
  completed: 'bg-green-500', failed: 'bg-red-500', stopped: 'bg-amber-500',
  running: 'bg-blue-500', pending: 'bg-blue-300', stopping: 'bg-amber-400',
};

function TeamCard({ team, lastRun, onDelete }) {
  const { t } = useI18n();
  // A run is now born "pending" — queued for a process, not yet spawned —
  // and is as much "in progress" as "running" is; isLive predates that
  // status (see the same widening in TeamDetails.jsx).
  const live = isLive(lastRun?.status) || lastRun?.status === 'pending';
  const lead = team.members.find((m) => m.agent_id === team.leader_agent_id);
  // Without a description, how the team works and who is on it.
  const meta = [
    modeSummary(team.mode, t),
    team.members.length ? team.members.map((m) => m.display_name).join(', ') : t('teams.noMembersYet'),
  ].join(' · ');
  // The card of a loop on the Loops page (pages/Loops.jsx): the name, three
  // lines under it, two tiles and the row of actions, so the three lists
  // share one size. The name's link covers the whole card (its ::after),
  // the actions sit above it; a hover anywhere on it lights the name.
  return (
    <div className="group relative rounded-lg border border-gray-100 bg-white p-4 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <Link
            to={`/teams/${team.team_id}`}
            className="block truncate text-sm font-semibold text-gray-900 group-hover:text-cyan-600 after:absolute after:inset-0 after:rounded-lg"
          >
            {team.name}
          </Link>
          {live && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-cyan-500" />}
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${MODE_BADGE[team.mode] || MODE_BADGE.parallel}`}>
          {modeLabel(team.mode, t)}
        </span>
      </div>
      <div className="mb-3 mt-1">
        <div className="line-clamp-3 text-[11px] text-gray-500" title={meta}>
          {team.description || meta}
        </div>
        {team.missing_agents?.length > 0 && (
          <div className="mt-0.5 text-[11px] text-red-600">
            {t('teams.missingAgents', { count: team.missing_agents.length })}
          </div>
        )}
        {team.members_without_manifest?.length > 0 && (
          <div className="mt-0.5 line-clamp-2 text-[11px] text-amber-700">
            {t('teams.noManifestFor', { names: team.members_without_manifest.join(', ') })}
          </div>
        )}
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <div className="rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-center">
          <div className="text-[9px] font-semibold uppercase tracking-wide text-blue-500">{t('teams.members')}</div>
          <div className="inline-flex max-w-full items-center justify-center gap-1 text-xs font-semibold text-blue-900">
            {lead && <Crown className="h-3 w-3 shrink-0 text-amber-600" title={lead.display_name} />}
            {team.members.length}
          </div>
        </div>
        <div className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-center">
          <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">{t('teams.lastRun')}</div>
          {/* How it ended as a dot by the date: one badge on top keeps the
              card the loop card's height. */}
          <div className="flex items-center justify-center gap-1 text-xs font-semibold text-amber-900" title={lastRun?.status}>
            {lastRun && <span className={`h-2 w-2 shrink-0 rounded-full ${RUN_DOT[lastRun.status] || 'bg-gray-400'}`} />}
            <span className="truncate">
              {lastRun?.started_at ? new Date(lastRun.started_at).toLocaleString() : t('teams.none')}
            </span>
          </div>
        </div>
      </div>

      <div className="relative z-10 flex justify-end gap-1">
        <Link
          to={`/teams/${team.team_id}`}
          className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-600"
          title={t('teams.openTeam')} aria-label={t('teams.openTeam')}
        >
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
        <button
          onClick={onDelete}
          title={t('teams.deleteThisTeam')} aria-label={t('teams.deleteThisTeam')}
          className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 min-[1800px]:grid-cols-5">
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

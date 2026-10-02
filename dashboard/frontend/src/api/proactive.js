/**
 * The agent's proactive profile and its pulse (proactive/, docs/proactive.md):
 * the schedule, quiet hours, budgets and brief that make an agent wake up on
 * its own, plus the job the profile owns, today's usage and the tick feed.
 * Every call answers with the same body: { agent_id, profile, schedule, job,
 * usage, ticks } (routes/agent_proactive.py).
 */
import api from './index';

const base = (agentId) => `/agents/${encodeURIComponent(agentId)}/proactive`;

export const getAgentProactive = (agentId, limit) =>
  api.get(base(agentId), { params: limit ? { limit } : {} });

// Only the keys present in `patch` change; the rest of the profile stays.
export const updateAgentProactive = (agentId, patch) => api.put(base(agentId), patch);

export const pauseAgentProactive = (agentId) => api.post(`${base(agentId)}/pause`);
export const resumeAgentProactive = (agentId) => api.post(`${base(agentId)}/resume`);
// A tick now, through the quiet hours but not past the budget or a running tick.
export const wakeAgentProactive = (agentId) => api.post(`${base(agentId)}/wake`);

// The Dashboard widget: every pulse that is on and what its ticks of the
// last `hours` came to; { hours, workspace, agents: [...], totals: {...} }.
export const getProactiveSummary = (workspace, hours) =>
  api.get('/proactive/summary', { params: { ...(workspace ? { workspace } : {}), ...(hours ? { hours } : {}) } });

export const TRIGGER_KINDS = ['webhook', 'file', 'task', 'eval', 'telegram', 'agent', 'slack', 'discord', 'teams', 'mail', 'watch'];
// The one filter field each trigger kind understands (proactive/events.py).
export const TRIGGER_FILTERS = {
  webhook: 'name', file: 'pattern', task: 'statuses', eval: 'eval_set_id', telegram: null, agent: 'from',
  slack: null, discord: null, teams: null, mail: null,
  // a watcher (docs/watchers.md): the card offers the workspace's watchers in a select
  watch: 'watcher_id',
};

export const INTERVAL_MINUTES = [5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440];
export const NOTIFY_CHANNELS = ['dashboard', 'telegram', 'slack', 'webhook', 'discord', 'teams', 'mail'];

/**
 * The tick feed with runs of quiet (and skipped) ticks folded into one row,
 * so a pulse that mostly finds nothing to do reads as "12 quiet ticks" rather
 * than twelve identical lines. A tick that acted, is blocked, failed or is
 * still running always gets its own row. Input is newest first, as the API
 * sends it; output keeps that order.
 */
export const collapseTicks = (ticks) => {
  const out = [];
  const quietLike = (t) => t.outcome === 'quiet' || t.outcome === 'quiet_hours'
    || t.outcome === 'busy' || t.outcome === 'rate' || t.outcome === 'budget' || t.outcome === 'disabled';
  (ticks || []).forEach((tick) => {
    const last = out[out.length - 1];
    // A compacted row (proactive.service.compact_journal) already stands
    // for several ticks; its count is what it says.
    const n = Number(tick.count) > 1 ? Number(tick.count) : 1;
    if (quietLike(tick) && last && last.group && last.outcome === tick.outcome) {
      last.count += n;
      last.until = tick.at;
      return;
    }
    if (quietLike(tick)) {
      out.push({ group: true, outcome: tick.outcome, count: n, at: tick.at, until: tick.at, id: tick.id });
      return;
    }
    out.push({ ...tick, group: false });
  });
  return out;
};

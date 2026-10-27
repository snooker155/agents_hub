/**
 * What the chat's agent picker lists, apart from how it is drawn: the agents
 * this workspace offers, cut down by a scope (own or system), a domain and a
 * search, and laid out in sections.
 *
 * With no search the sections are: the default chat agent, the agents the
 * latest conversations talked to, then own agents and system ones. An agent
 * appears once, in the first section that has it. A search drops the sections
 * for one list, best match first: a name that starts with the text, then a
 * word of the name that does, then the name or id holding it anywhere, then
 * the description, the domain or a tool.
 */

const norm = (s) => String(s || '').toLowerCase();

/** The agents of the most recently touched agent conversations, newest first. */
export function recentAgentIds(conversations, limit = 4) {
  const rows = (conversations || [])
    .filter((c) => c && c.agent_id && (c.target_mode || 'agent') === 'agent')
    .map((c) => ({ id: String(c.agent_id), at: Date.parse(c.updated_at || c.created_at || '') || 0 }))
    .sort((a, b) => b.at - a.at);
  const ids = [];
  for (const r of rows) {
    if (!ids.includes(r.id)) ids.push(r.id);
    if (ids.length >= limit) break;
  }
  return ids;
}

/**
 * How many scheduled jobs that run one agent (tasks and runs) each agent has,
 * and the agents with the most of them first: the picker's counts and its
 * first section on the Plan and Deployments forms.
 */
export function agentJobStats(jobs) {
  const counts = new Map();
  for (const j of jobs || []) {
    if (!j?.agent_id || (j.kind !== 'agent_task' && j.kind !== 'agent_run')) continue;
    counts.set(j.agent_id, (counts.get(j.agent_id) || 0) + 1);
  }
  const recent = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([id]) => id);
  return { counts, recent };
}

/** How many conversations each agent has, by id. */
export function chatCounts(conversations) {
  const counts = new Map();
  for (const c of conversations || []) {
    if (!c?.agent_id || (c.target_mode || 'agent') !== 'agent') continue;
    const id = String(c.agent_id);
    counts.set(id, (counts.get(id) || 0) + 1);
  }
  return counts;
}

/** The domains the agents carry, each once whatever its case, sorted. */
export function agentDomains(agents) {
  const byKey = new Map();
  for (const a of agents || []) {
    if (!a?.domain) continue;
    const key = norm(a.domain);
    if (!byKey.has(key)) byKey.set(key, { key, label: a.domain, count: 0 });
    byKey.get(key).count += 1;
  }
  return [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/** How well an agent answers a search, 0 for not at all. */
export function matchScore(agent, query) {
  const q = norm(query).trim();
  if (!q) return 1;
  const name = norm(agent.name || agent.id);
  if (name.startsWith(q)) return 5;
  if (name.split(/[\s_\-.]+/).some((w) => w.startsWith(q))) return 4;
  if (name.includes(q) || norm(agent.id).includes(q)) return 3;
  if (norm(agent.description).includes(q) || norm(agent.domain).includes(q)) return 2;
  if ((agent.tools || []).some((tool) => norm(tool).includes(q))) return 1;
  return 0;
}

const byName = (a, b) => norm(a.name || a.id).localeCompare(norm(b.name || b.id));

/**
 * @param {object[]} agents
 * @param {{query?: string, scope?: 'all'|'own'|'system', domain?: string, recent?: string[]}} opts
 * @returns {{key: string, items: object[]}[]} the non-empty sections in order.
 */
export function pickerSections(agents, { query = '', scope = 'all', domain = '', recent = [] } = {}) {
  const pool = (agents || []).filter((a) => (
    (scope === 'all' || (scope === 'system' ? !!a.system : !a.system))
    && (!domain || norm(a.domain) === domain)
  ));

  if (query.trim()) {
    const items = pool
      .map((a) => ({ a, s: matchScore(a, query) }))
      .filter((r) => r.s > 0)
      .sort((x, y) => y.s - x.s || byName(x.a, y.a))
      .map((r) => r.a);
    return items.length ? [{ key: 'matches', items }] : [];
  }

  const taken = new Set();
  const take = (list) => list.filter((a) => {
    if (taken.has(a.id)) return false;
    taken.add(a.id);
    return true;
  });
  const byId = new Map(pool.map((a) => [a.id, a]));
  const sections = [
    { key: 'default', items: take(pool.filter((a) => a.is_default_chat_agent)) },
    { key: 'recent', items: take(recent.map((id) => byId.get(id)).filter(Boolean)) },
    { key: 'own', items: take(pool.filter((a) => !a.system).sort(byName)) },
    { key: 'system', items: take(pool.filter((a) => a.system).sort(byName)) },
  ];
  return sections.filter((s) => s.items.length);
}

const AVATAR_TONES = [
  'bg-indigo-100 text-indigo-700', 'bg-emerald-100 text-emerald-700', 'bg-amber-100 text-amber-700',
  'bg-sky-100 text-sky-700', 'bg-rose-100 text-rose-700', 'bg-teal-100 text-teal-700',
  'bg-orange-100 text-orange-700', 'bg-cyan-100 text-cyan-700',
];

/** A steady colour per agent id, for its initial. */
export function avatarTone(id) {
  let h = 0;
  for (const ch of String(id || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_TONES[h % AVATAR_TONES.length];
}

/** One or two letters for the agent's avatar. */
export function initials(agent) {
  const words = String(agent?.name || agent?.id || '?').split(/[\s_\-.]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] || '?').slice(0, 2);
  return letters.toUpperCase();
}

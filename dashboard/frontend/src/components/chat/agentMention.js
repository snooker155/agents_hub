/**
 * `@agent` in a composer: the person names the agent the chat goes to.
 *
 * Typing `@` at the start of a word opens a list of agents (MentionMenu.jsx);
 * a pick writes `@<agent id> ` in place of what was typed after the `@`. The
 * mention is plain text until the message is sent, so it survives every way
 * the text gets there (typed, pasted, picked) and the box needs no state of
 * its own: `takeMention` reads the first `@<id>` of a known agent off the
 * text when it is sent, and the rest of the text is the message.
 *
 * The main chat hands the conversation to that agent (useChatSend); with a
 * team as the target, and on the team page (TeamRunChat), only the team's
 * members are offered and the request goes to that member first
 * (teams/runner.py `_run_addressed`). Pure functions, so the parsing is
 * testable on its own.
 */

// What may follow the `@` while the list is open: no spaces, so the list
// closes once the word ends.
const QUERY_RE = /(^|\s)@([\p{L}\p{N}_-]*)$/u;
// A finished mention: `@id` followed by a space, punctuation or the end.
const TOKEN_RE = /(^|\s)@([\p{L}\p{N}_-]+)(?=$|[\s,.:;!?)])/gu;

/** How many agents the list shows at most. */
export const MENTION_LIMIT = 8;

/**
 * The mention being typed at the caret: `{start, query}`, where `start` is
 * the index of the `@`, or null when the caret is not in one.
 */
export function mentionAt(text, caret = String(text || '').length) {
  const before = String(text || '').slice(0, caret);
  const m = QUERY_RE.exec(before);
  if (!m) return null;
  return { start: m.index + m[1].length, query: m[2] };
}

/**
 * The agents that match what was typed after the `@`: an id or a name that
 * starts with it first, then one that contains it, each in the given order.
 * `agents` are `{id, name}`.
 */
export function mentionSuggestions(agents, query, limit = MENTION_LIMIT) {
  const q = String(query || '').toLowerCase();
  const list = (agents || []).filter((a) => a?.id);
  if (!q) return list.slice(0, limit);
  const starts = [];
  const contains = [];
  for (const a of list) {
    const id = String(a.id).toLowerCase();
    const name = String(a.name || '').toLowerCase();
    if (id.startsWith(q) || name.startsWith(q) || name.split(/\s+/).some((w) => w.startsWith(q))) starts.push(a);
    else if (id.includes(q) || name.includes(q)) contains.push(a);
  }
  return [...starts, ...contains].slice(0, limit);
}

/**
 * The text with the mention being typed (`mention` from `mentionAt`)
 * replaced by `@<id> `, and where the caret goes after it.
 */
export function insertMention(text, caret, mention, agentId) {
  const value = String(text || '');
  const end = mention.start + 1 + mention.query.length;
  const rest = value.slice(Math.max(end, caret));
  const head = `${value.slice(0, mention.start)}@${agentId} `;
  const tail = rest.startsWith(' ') ? rest.slice(1) : rest;
  return { text: head + tail, caret: head.length };
}

/**
 * The first `@<id>` in the text that names one of `agents`: the agent and
 * the text without the mention, or null. An `@word` that is no agent's id
 * stays in the text as written.
 */
export function takeMention(text, agents) {
  const value = String(text || '');
  const byId = new Map((agents || []).filter((a) => a?.id).map((a) => [String(a.id).toLowerCase(), a]));
  for (const m of value.matchAll(TOKEN_RE)) {
    const agent = byId.get(m[2].toLowerCase());
    if (!agent) continue;
    const at = m.index + m[1].length;
    const before = value.slice(0, at);
    const after = value.slice(at + 1 + m[2].length);
    // "ask @coder, then" reads "ask, then"; "@coder fix it" reads "fix it".
    const joined = /^[,.:;!?)]/.test(after)
      ? before.replace(/\s+$/, '') + after
      : before + after.replace(/^ /, '');
    return { agent, text: joined.trim() };
  }
  return null;
}

/**
 * A team's members as mention targets (`{id, name, description}`), one row
 * per agent: a second seat of the same agent is reached by the first.
 */
export function teamMentionMembers(team) {
  return (team?.members || [])
    .filter((m, i, all) => m?.agent_id && all.findIndex((o) => o.agent_id === m.agent_id) === i)
    .map((m) => ({ id: m.agent_id, name: m.display_name || m.name || m.agent_id, description: m.role || '' }));
}
